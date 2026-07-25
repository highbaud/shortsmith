"""Verified brand logos for b-roll logo slides.

Simple Icons is an excellent primary source. It serves the current official
mark in the brand's own color, and an audit of all 41 curated brands found zero
wrong marks. Its problem is coverage: 14 of those 41 have no Simple Icons entry,
and vectorlogo.zone only answers for 2 of the 14. The rest silently produced no
slide at all, including BlackRock and JPMorgan, the two most-mentioned
institutions in this channel's content.

So a verified middle tier sits between them: Wikidata's P154 ("logo image"),
which recovers BlackRock, JPMorgan, Vanguard, Berkshire Hathaway, Anchorage
Digital and Starlink. Resolution order:

  1. Simple Icons     : current official mark, brand color; the <title> is
                        checked against the brand so a future slug reassignment
                        can't silently serve someone else's icon.
  2. Wikidata P154    : the entity's designated logo, from a pinned QID where we
                        have one. Verified: the entity must not be a human and
                        must carry a logo claim.
  3. vectorlogo.zone  : community-contributed and can be stale (it served the
                        retired Coinbase "C"), so it goes last and only answers
                        where the two verified sources didn't.

Nothing found means no slide. A wrong logo is worse than no logo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import wikidata
from wikidata import HUMAN, Fetch

# Pinned QIDs for brands whose name is ambiguous or whose Wikidata label differs
# from how the transcript says it. Verified via `gen_broll.py --audit-brands`.
BRAND_QIDS: dict[str, str] = {
    "BlackRock": "Q219635",
    "JPMorgan": "Q192314",              # labelled "JPMorgan Chase"
    "Vanguard": "Q849363",              # labelled "The Vanguard Group"
    "Berkshire Hathaway": "Q217583",
    "Anchorage": "Q110660778",          # labelled "Anchorage Digital"
    "Starlink": "Q19867977",
    # Pinned to the RIGHT company even though it has no logo on Wikidata: search
    # otherwise lands on Fidelity International (Q5446786), a separate firm from
    # the Fidelity Investments that US financial content means. Pinning here
    # yields no logo and no slide, which is the correct outcome.
    "Fidelity": "Q1411292",
}

# Brand words that are also ordinary words or unrelated products. These never
# resolve by search from ANY source; only a pinned QID can supply them.
#
# Every one of these was caught producing a confidently-wrong mark: "swift"
# serves Apple's Swift language rather than the interbank network, "quant"
# resolved to the TV series Quantico, "xdc" to Sony's XDCAM, and "circle",
# "flare" and "anchorage" are common nouns before they are companies.
AMBIGUOUS_BRANDS = frozenset({
    "swift", "circle", "flare", "quant", "anchorage", "xdc",
})

SVG_TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.DOTALL)


@dataclass(frozen=True)
class LogoResult:
    """A downloaded logo plus where it came from, for the audit trail."""

    data: bytes
    ext: str      # ".svg" | ".png" | ".jpg"
    source: str   # "simple-icons" | "wikidata-p154" | "vectorlogo.zone"
    detail: str   # slug, Commons filename, or QID (whatever identifies the pick)

    @property
    def is_verified(self) -> bool:
        """False for vectorlogo.zone, whose marks nothing cross-checks."""
        return self.source != "vectorlogo.zone"


def slugify(brand: str) -> str:
    return re.sub(r"[^a-z0-9]", "", brand.lower())


def _ext_of(filename: str) -> str:
    # Logos default to .svg: Commons serves most brand marks as vectors.
    return wikidata.file_extension(filename, default=".svg")


# --------------------------------------------------------------------------- #
# 1. Simple Icons
# --------------------------------------------------------------------------- #
def _svg_title(raw: bytes) -> str:
    match = SVG_TITLE.search(raw)
    return match.group(1).decode("utf-8", "replace").strip() if match else ""


def from_simple_icons(fetch: Fetch, brand: str) -> LogoResult | None:
    slug = slugify(brand)
    if slug in AMBIGUOUS_BRANDS:
        return None
    raw = fetch(f"https://cdn.simpleicons.org/{slug}")
    if not raw or b"<svg" not in raw:
        return None
    # Guard against a slug being reassigned to a different brand upstream.
    title = _svg_title(raw)
    if title and slugify(title) != slug:
        return None
    return LogoResult(data=raw, ext=".svg", source="simple-icons", detail=slug)


# --------------------------------------------------------------------------- #
# 2. Wikidata P154
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BrandIdentity:
    qid: str
    label: str
    description: str = ""
    logo: str | None = None   # P154 Commons filename
    pinned: bool = False

    def summary(self) -> str:
        bits = [f"{self.qid} {self.label}"]
        if self.description:
            bits.append(f"({self.description})")
        return " ".join(bits)


def _build_brand(qid: str, entity: dict, *, pinned: bool) -> BrandIdentity:
    # current_claim_values, not claim_values: P154 statements run roughly
    # chronologically, so plain order hands back Microsoft's 1980 wordmark.
    logos = wikidata.current_claim_values(entity, "P154")
    return BrandIdentity(
        qid=qid,
        label=wikidata.label_of(entity) or qid,
        description=wikidata.description_of(entity),
        logo=logos[0] if logos else None,
        pinned=pinned,
    )


# Properties only an operating company carries. A band, film or animal sharing
# the brand's name has none of them, which is how "Kraken" stopped resolving to
# a Colombian metal band whose Wikidata label is, accurately, "Kraken".
COMPANY_SIGNALS = (
    "P452",   # industry
    "P159",   # headquarters location
    "P1454",  # legal form
    "P414",   # stock exchange
    "P169",   # chief executive officer
    "P2403",  # total assets
    "P1128",  # employees
)


def looks_like_a_company(entity: dict) -> bool:
    return any(wikidata.claim_values(entity, prop) for prop in COMPANY_SIGNALS)


def label_matches(brand: str, label: str) -> bool:
    """Is `label` the same company the transcript said, or that company plus a
    suffix?

    Whole-word anchored, which is the entire point: "JPMorgan" must match
    "JPMorgan Chase", but "Quant" must NOT match "Quantico" and "XDC" must NOT
    match "XDCAM". Both of those shipped a wrong logo before this check existed.
    """
    wanted = re.sub(r"[^a-z0-9 ]", "", brand.strip().lower()).strip()
    got = re.sub(r"[^a-z0-9 ]", "", label.strip().lower()).strip()
    if not wanted or not got:
        return False
    return got == wanted or got.startswith(wanted + " ")


def resolve_brand(fetch: Fetch, brand: str) -> BrandIdentity | None:
    """Resolve a brand name to a Wikidata entity that has a logo, or None.

    An unpinned entity qualifies only if it is not a human, carries a P154 logo
    claim, looks like an operating company, and its label matches the spoken
    brand on a word boundary. Together those rule out the founder, the
    same-named band or film, and the near-miss substring matches that caused the
    Quantico / XDCAM mistakes.
    """
    pinned = BRAND_QIDS.get(brand.strip())
    if pinned:
        entity = wikidata.get_entities(fetch, [pinned]).get(pinned)
        if entity:
            return _build_brand(pinned, entity, pinned=True)
        # Pin failed to load (offline / deleted item). Fall through to search.

    if slugify(brand) in AMBIGUOUS_BRANDS:
        return None
    hits = wikidata.search_entity_ids(fetch, brand)
    if not hits:
        return None
    entities = wikidata.get_entities(fetch, hits)
    for qid in hits:  # search relevance order
        entity = entities.get(qid)
        if not entity or HUMAN in wikidata.claim_values(entity, "P31"):
            continue
        if not looks_like_a_company(entity):
            continue
        candidate = _build_brand(qid, entity, pinned=False)
        if candidate.logo and label_matches(brand, candidate.label):
            return candidate
    return None


def from_wikidata(fetch: Fetch, brand: str) -> LogoResult | None:
    identity = resolve_brand(fetch, brand)
    if not identity or not identity.logo:
        return None
    raw = fetch(wikidata.commons_file_url(identity.logo, width=1024))
    if not raw or len(raw) < 200:
        return None
    return LogoResult(data=raw, ext=_ext_of(identity.logo), source="wikidata-p154",
                      detail=f"{identity.qid} {identity.logo}")


# --------------------------------------------------------------------------- #
# 3. vectorlogo.zone (unverified, last resort)
# --------------------------------------------------------------------------- #
def from_vectorlogo(fetch: Fetch, brand: str) -> LogoResult | None:
    slug = slugify(brand)
    if slug in AMBIGUOUS_BRANDS:
        return None
    raw = fetch(f"https://www.vectorlogo.zone/logos/{slug}/{slug}-icon.svg")
    if not raw or b"<svg" not in raw:
        return None
    return LogoResult(data=raw, ext=".svg", source="vectorlogo.zone", detail=slug)


# --------------------------------------------------------------------------- #
# Chain
# --------------------------------------------------------------------------- #
RESOLVERS = (from_simple_icons, from_wikidata, from_vectorlogo)


def resolve_logo(fetch: Fetch, brand: str) -> LogoResult | None:
    """First source that yields a usable mark, in verified-first order."""
    for resolver in RESOLVERS:
        result = resolver(fetch, brand)
        if result:
            return result
    return None

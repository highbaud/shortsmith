"""Identity-verified photos of named public figures, for b-roll person slides.

The problem this solves: a keyword search for a person's name returns files whose
*description text* mentions those words, which is not the same thing as a photo
of that person. Searching Wikimedia Commons for "David Schwartz" returns a photo
of Anna Schwartz; the Wikipedia article at `/David_Schwartz` is an American
composer. Neither is Ripple's CTO, and nothing downstream noticed.

So this module never searches for a *name*. It resolves the name to a Wikidata
entity first, and then only accepts images that Wikidata or Commons has bound to
that specific entity:

  1. Resolve  name (+ role hint) -> QID. Pinned in PERSON_QIDS when we know it;
     otherwise `wbsearchentities` filtered to humans (P31=Q5) and scored against
     the role hint via description / occupation (P106) / employer (P108, P169).
  2. Gather   images attached to that QID only:
       * P18       - the entity's designated image (nearly always a clean portrait)
       * P180      - Commons files whose structured data says they DEPICT the entity
       * P373      - members of the entity's own Commons category
  3. Rank     prefer P18 and solo portraits; reject signatures, graves, plaques,
     logos, and group shots ("X and Y", "A & B").
  4. Fail closed. No verified image means the caller drops the slide. A missing
     cutaway is invisible; a stranger's face is not.

The caller supplies the HTTP getter (`fetch`), so this module inherits
gen_broll's on-disk cache, throttle, retry, and offline mode without importing
it back.
"""
from __future__ import annotations

import random
import re
import urllib.parse
from dataclasses import dataclass, replace

import wikidata
from wikidata import COMMONS_API, HUMAN, Fetch
from wikidata import api_json as _api_json
from wikidata import claim_values as _claim_values
from wikidata import commons_file_url as file_url
from wikidata import commons_image_info as _image_info
from wikidata import get_entities as _get_entities
from wikidata import labels_for as _labels_for
from wikidata import strip_file_prefix as _strip_file_prefix

# Curated QID pins. A pinned name skips entity search entirely, so an ambiguous
# name can never resolve to a stranger no matter how the search index shifts.
# Every entry below was verified against Wikidata via `gen_broll.py
# --audit-people`; re-run it after editing this map.
#
# Pinning is not belt-and-braces. Search genuinely gets these wrong: unpinned,
# "Michael Saylor" resolves to a substitute teacher in Kentucky (Q127446347),
# because that item's label is an exact string match while the real one is
# labelled "Michael J. Saylor". "Jim Rickards" resolves to nothing at all; his
# item is "James G. Rickards".
PERSON_QIDS: dict[str, str] = {
    # Ripple / XRP
    "Brad Garlinghouse": "Q4953945",
    "David Schwartz": "Q110190051",
    "Chris Larsen": "Q19864583",
    "Jed McCaleb": "Q28449997",
    # Crypto founders / execs
    "Michael Saylor": "Q6831501",      # labelled "Michael J. Saylor"
    "Vitalik Buterin": "Q16197959",
    "Brian Armstrong": "Q64705310",
    "Changpeng Zhao": "Q52714313",
    # Wall Street / asset managers / TradFi
    "Larry Fink": "Q3218882",
    "Warren Buffett": "Q47213",
    "Jamie Dimon": "Q922169",
    "Cathie Wood": "Q104587868",
    "Michael Burry": "Q6828961",
    "Peter Schiff": "Q512741",
    "Jim Rickards": "Q6134385",        # labelled "James G. Rickards"
    # Policy / government
    "Donald Trump": "Q22686",
    "Jerome Powell": "Q6182718",
    "Gary Gensler": "Q1494852",
    "Scott Bessent": "Q7435987",
    "Nayib Bukele": "Q17712353",
    # Tech founders
    "Elon Musk": "Q317521",
    "Jensen Huang": "Q305177",
    "Jeff Bezos": "Q312556",
    "Mark Zuckerberg": "Q36215",
    # Finance personalities / authors
    "Kevin O'Leary": "Q6397147",
    "Robert Kiyosaki": "Q311147",
    "Jordan Belfort": "Q3183674",
    "Patrick Bet-David": "Q16217194",
}

# Last word on which file to use, when the ranked pick is technically of the
# right person but still a bad frame. Maps a display name to a Commons filename;
# the file is used as-is, skipping ranking. Verify with --audit-people.
PERSON_PHOTO_OVERRIDES: dict[str, str] = {}

# Names that have no legitimate photograph. Resolving these to *anything* is a
# mistake, so short-circuit them to "no photo" and let the caller drop the slide.
NO_PHOTO_EXISTS = frozenset({"satoshi nakamoto"})

# Files that are about a person without being a usable photo OF them.
REJECT_WORDS = (
    "signature", "autograph", "coat of arms", "grave", "tomb", "memorial",
    "plaque", "statue", "bust", "sculpture", "mural", "logo", "seal", "flag",
    "book cover", "poster", "caricature", "cartoon", "sketch", "drawing",
    "diagram", "chart", "map", "letter", "document", "screenshot", "quote",
)
# Titles that describe more than one subject. A group shot crops badly in 9:16
# and puts the wrong face center-frame.
GROUP_MARKERS = (" & ", " and ", " with ", " meets ", " hosts ", " vs ", " v. ",
                 " y ", " und ", " et ", ";")
PORTRAIT_WORDS = ("portrait", "headshot", "official photo", "profile picture")

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
MIN_WIDTH = 400     # below this a full-frame 9:16 cutaway visibly softens
GOOD_WIDTH = 600
VARIETY_BAND = 12   # shuffle among candidates within this many points of the best

# Scoring weights. Tuned so the entity's designated portrait (P18) beats a
# same-subject action shot, and any group photo loses to both.
W_P18 = 20
W_NAME_IN_TITLE = 12
W_TITLE_STARTS_WITH_NAME = 6
W_PORTRAIT_WORD = 6
W_GOOD_WIDTH = 4
W_GROUP_PENALTY = -30
W_OTHER_PERSON_PENALTY = -30


@dataclass(frozen=True)
class Identity:
    """A resolved Wikidata human entity."""

    qid: str
    label: str
    description: str = ""
    occupations: tuple[str, ...] = ()
    employers: tuple[str, ...] = ()
    image: str | None = None            # P18 Commons filename
    commons_category: str | None = None  # P373
    score: int = 0
    pinned: bool = False
    sitelinks: int = 0

    def summary(self) -> str:
        bits = [f"{self.qid} {self.label}"]
        if self.description:
            bits.append(f"({self.description})")
        if self.employers:
            bits.append(f"[{', '.join(self.employers)}]")
        if not self.pinned:
            bits.append(f"<{self.sitelinks} sitelinks>")
        return " ".join(bits)


@dataclass(frozen=True)
class Candidate:
    """A Commons file bound to a resolved identity."""

    title: str          # filename, no "File:" prefix
    origin: str         # "P18" | "depicts" | "category"
    width: int = 0
    url: str = ""
    score: int = 0
    reasons: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Wikidata
# --------------------------------------------------------------------------- #
def _build_identity(fetch: Fetch, qid: str, entity: dict, *, score: int,
                    pinned: bool) -> Identity:
    p18 = _claim_values(entity, "P18")
    p373 = _claim_values(entity, "P373")
    return Identity(
        qid=qid,
        label=wikidata.label_of(entity) or qid,
        description=wikidata.description_of(entity),
        occupations=_labels_for(fetch, _claim_values(entity, "P106")[:4]),
        employers=_labels_for(fetch, _claim_values(entity, "P108")[:3]
                              + _claim_values(entity, "P169")[:2]),
        image=p18[0] if p18 else None,
        commons_category=p373[0] if p373 else None,
        score=score,
        pinned=pinned,
        sitelinks=wikidata.sitelinks_count(entity),
    )


def _hint_tokens(role_hint: str) -> list[str]:
    """Meaningful words from a role string like 'CTO, Ripple' -> ['ripple'].

    Titles (CEO/CTO/founder) are dropped: almost every candidate is one, so they
    carry no disambiguating signal. Organisations and domains do.
    """
    generic = {"ceo", "cto", "cfo", "coo", "chair", "chairman", "chairwoman",
               "founder", "cofounder", "co-founder", "former", "president",
               "author", "the", "and", "of", "at"}
    tokens = re.split(r"[^A-Za-z0-9']+", role_hint.lower())
    return [t for t in tokens if len(t) > 2 and t not in generic]


# Notability from Wikimedia coverage: +1 per NOTABILITY_DIVISOR sitelinks, capped
# at +10. An unhinted human needs MIN_SITELINKS to resolve at all; the exact
# label match that once picked a substitute teacher over Michael Saylor is now
# worth a single point.
NOTABILITY_DIVISOR = 5
MIN_SITELINKS = 5


def _score_entity(entity: dict, name: str, hint_tokens: list[str],
                  blob: str) -> tuple[int, bool]:
    """(score, eligible). Eligible means the role hint matched or the entity is
    notable enough (MIN_SITELINKS) to be the person a video would mention."""
    hint_hits = sum(1 for token in hint_tokens if token in blob)
    links = wikidata.sitelinks_count(entity)
    score = 3 * hint_hits + min(links, 50) // NOTABILITY_DIVISOR
    if wikidata.label_of(entity).lower() == name.lower():
        score += 1
    if _claim_values(entity, "P18"):
        score += 1  # having a designated portrait correlates with being the notable one
    return score, (hint_hits > 0 or links >= MIN_SITELINKS)


def resolve_identity(fetch: Fetch, name: str, role_hint: str = "") -> Identity | None:
    """Resolve a person's name to a Wikidata human entity, or None.

    A pinned QID is trusted outright. Otherwise every search hit is checked to be
    a human (P31=Q5) and scored against the role hint; ties and zero-signal
    matches resolve to the highest-scoring human, and no humans means None.
    """
    if name.strip().lower() in NO_PHOTO_EXISTS:
        return None

    pinned = PERSON_QIDS.get(name.strip())
    if pinned:
        entity = _get_entities(fetch, [pinned]).get(pinned)
        if entity:
            return _build_identity(fetch, pinned, entity, score=99, pinned=True)
        # Pin failed to load (offline / deleted item). Fall through to search.

    hits = wikidata.search_entity_ids(fetch, name)
    if not hits:
        return None

    hint_tokens = _hint_tokens(role_hint)
    entities = _get_entities(fetch, hits, "claims%7Cdescriptions%7Clabels%7Csitelinks")
    best: Identity | None = None
    for qid in hits:  # preserve search relevance order for ties
        entity = entities.get(qid)
        if not entity or HUMAN not in _claim_values(entity, "P31"):
            continue
        identity = _build_identity(fetch, qid, entity, score=0, pinned=False)
        blob = " ".join((identity.description, *identity.occupations,
                         *identity.employers)).lower()
        score, eligible = _score_entity(entity, name, hint_tokens, blob)
        if not eligible:
            continue
        identity = replace(identity, score=score)
        if best is None or score > best.score:
            best = identity
    return best


# --------------------------------------------------------------------------- #
# Commons: files bound to the resolved entity
# --------------------------------------------------------------------------- #
def _depicts_titles(fetch: Fetch, qid: str, limit: int = 12) -> list[str]:
    """Files whose Commons structured data asserts P180 (depicts) = this entity."""
    url = (f"{COMMONS_API}?action=query&list=search"
           f"&srsearch=haswbstatement%3AP180%3D{qid}&srnamespace=6&srlimit={limit}"
           "&format=json")
    results = (_api_json(fetch, url).get("query") or {}).get("search") or []
    return [_strip_file_prefix(r["title"]) for r in results if r.get("title")]


def _category_titles(fetch: Fetch, category: str | None, limit: int = 12) -> list[str]:
    """Members of the entity's own Commons category."""
    if not category:
        return []
    title = urllib.parse.quote("Category:" + category.replace(" ", "_"))
    url = (f"{COMMONS_API}?action=query&list=categorymembers&cmtitle={title}"
           f"&cmtype=file&cmlimit={limit}&format=json")
    members = (_api_json(fetch, url).get("query") or {}).get("categorymembers") or []
    return [_strip_file_prefix(m["title"]) for m in members if m.get("title")]


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def _name_tokens(name: str) -> list[str]:
    """`name` split into lower-case word tokens, in order.

    An apostrophe stays inside a token, so O'Leary is one word rather than two.
    Every name comparison here goes through this, so "how a name splits" cannot
    drift between the surname sweep and the filename scorer.
    """
    return [t for t in re.split(r"[^A-Za-z']+", name.lower()) if t]


def _other_person_surnames(name: str) -> set[str]:
    """Surnames of *other* people we know about, to catch two-subject photos."""
    own = set(_name_tokens(name))
    others: set[str] = set()
    for known in PERSON_QIDS:
        tokens = [t for t in _name_tokens(known) if len(t) > 3]
        others.update(t for t in tokens if t not in own)
    return others


def score_candidate(title: str, origin: str, width: int, name: str,
                    other_surnames: set[str] | None = None) -> tuple[int, tuple[str, ...]]:
    """Score one file. A score of None-equivalent (-999) means reject outright."""
    lower = title.lower()
    reasons: list[str] = []

    if not lower.endswith(IMAGE_EXTS):
        return (-999, ("not a still image",))
    for word in REJECT_WORDS:
        if word in lower:
            return (-999, (f"non-photo subject: {word}",))
    if width and width < MIN_WIDTH:
        return (-999, (f"too small ({width}px)",))

    score = 0
    if origin == "P18":
        score += W_P18
        reasons.append("designated portrait (P18)")

    name_tokens = _name_tokens(name)
    stem = lower.rsplit(".", 1)[0]
    if name_tokens and all(re.search(rf"\b{re.escape(t)}", stem) for t in name_tokens):
        score += W_NAME_IN_TITLE
        reasons.append("full name in filename")
        if stem.startswith(name.lower()):
            score += W_TITLE_STARTS_WITH_NAME
            reasons.append("filename leads with the name")

    if any(word in lower for word in PORTRAIT_WORDS):
        score += W_PORTRAIT_WORD
        reasons.append("portrait/headshot")
    if width >= GOOD_WIDTH:
        score += W_GOOD_WIDTH

    # "(cropped)" is a Commons convention for a derivative cropped to the
    # subject, so a two-name title with that suffix is a solo shot after all.
    padded = f" {stem} "
    if "cropped" not in stem and any(marker in padded for marker in GROUP_MARKERS):
        score += W_GROUP_PENALTY
        reasons.append("looks like a group photo")
    for surname in (other_surnames if other_surnames is not None
                    else _other_person_surnames(name)):
        if re.search(rf"\b{re.escape(surname)}\b", stem):
            score += W_OTHER_PERSON_PENALTY
            reasons.append(f"another known person in frame ({surname})")
            break

    return (score, tuple(reasons))


def rank_candidates(fetch: Fetch, identity: Identity, name: str) -> list[Candidate]:
    """All verified files for an identity, best first."""
    titles: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(title: str, origin: str) -> None:
        key = title.lower()
        if title and key not in seen:
            seen.add(key)
            titles.append((title, origin))

    if identity.image:
        add(identity.image, "P18")
    for title in _depicts_titles(fetch, identity.qid):
        add(title, "depicts")
    for title in _category_titles(fetch, identity.commons_category):
        add(title, "category")
    if not titles:
        return []

    info = _image_info(fetch, [t for t, _ in titles])
    other_surnames = _other_person_surnames(name)
    out: list[Candidate] = []
    for title, origin in titles:
        url, width = info.get(title, ("", 0))
        score, reasons = score_candidate(title, origin, width, name, other_surnames)
        if score <= -999:
            continue
        out.append(Candidate(title=title, origin=origin, width=width,
                             url=url or file_url(title), score=score,
                             reasons=reasons))
    out.sort(key=lambda c: (-c.score, c.title))
    return out


def shuffle_within_band(candidates: list[Candidate], seed: int | None = None,
                        band: int = VARIETY_BAND) -> list[Candidate]:
    """Vary the pick among near-equal candidates without ever demoting the best.

    Files within `band` points of the top score are interchangeable in quality,
    so shuffling them gives run-to-run variety. Anything below the band keeps its
    rank and is only reached if everything above it fails to download.
    """
    if not candidates:
        return []
    rng = random.Random(seed) if seed is not None else random.Random()
    cutoff = candidates[0].score - band
    top = [c for c in candidates if c.score >= cutoff]
    rest = [c for c in candidates if c.score < cutoff]
    rng.shuffle(top)
    return top + rest


def resolve_photo_candidates(fetch: Fetch, name: str, role_hint: str = "",
                             seed: int | None = None
                             ) -> tuple[Identity | None, list[Candidate]]:
    """Full pipeline: name (+role) -> identity -> ranked, shuffled candidates."""
    identity = resolve_identity(fetch, name, role_hint)
    if not identity:
        return (None, [])

    override = PERSON_PHOTO_OVERRIDES.get(name.strip())
    if override:
        info = _image_info(fetch, [override]).get(override, ("", 0))
        return (identity, [Candidate(title=override, origin="override",
                                     width=info[1], url=info[0] or file_url(override),
                                     score=999, reasons=("manual override",))])

    return (identity, shuffle_within_band(rank_candidates(fetch, identity, name), seed))

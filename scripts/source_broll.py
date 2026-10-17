"""Opt-in, evidence-checked source cutaways for a scaffolded short.

``broll.sources.json`` accepts ``enabled`` (CSV or list) and ``items`` (list).
Items require ``kind`` and ``query`` (an exact transcript phrase). Optional keys:
start, end, url, image_url, qid, entity_name, official_domain, quote, date, license, title.
``qid`` identifies the exact place or the official source organization.
Discovery proposes candidates; only independently verified sources are inserted.
No paid service, network, capture, or output runs when disabled or in dry-run.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import math
import os
import posixpath
import re
import urllib.parse
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import source_capture
import source_discovery
import wikidata

KINDS = ("places", "images", "press", "tweets")
MANIFEST = "broll.sources.json"
PROVENANCE = "broll.sources.provenance.json"
REVIEW = "broll.sources.review.json"
# Existing gen_broll._gen_heuristic.add uses a 3.5-second default cutaway.
CUTAWAY_DURATION = 3.5
# These words describe an announcement without identifying its subject.
_GENERIC_EVENT_WORDS = frozenset((
    "a an the and or of to in on at for with by from as is are was were be been "
    "it its they their this that these those has have had will would can could "
    "said says say announced announces announce announcement announcements "
    "news press release releases released releasing statement statements "
    "today yesterday tomorrow recently new latest official company corporation "
    "launch launched launches launching launch announcement update updates updated "
    "posted post posts tweet tweets tweeted according about"
).split())
_SUBJECT_AUXILIARIES = frozenset(
    "has have had is are was were will would can could may might just also recently officially".split()
)
_CLAUSE_CHANGES = frozenset("while whereas but although however yet instead".split())
_ENTITY_TOKEN = "_source_entity_"
log = logging.getLogger(__name__)


def parse_sources(value: str | list[str]) -> frozenset[str]:
    """Parse source switches, rejecting spelling errors instead of ignoring them."""
    if isinstance(value, str):
        parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    elif isinstance(value, list) and all(isinstance(part, str) for part in value):
        parts = [part.strip().lower() for part in value]
    else:
        raise ValueError("B-roll sources enabled must be a CSV string or a list of names")
    if parts == ["all"]:
        return frozenset(KINDS)
    if not parts or parts == ["off"]:
        return frozenset()
    unknown = set(parts) - set(KINDS)
    if unknown:
        raise ValueError(f"Unknown B-roll source(s): {', '.join(sorted(unknown))}; use {','.join(KINDS)}, all, or off")
    return frozenset(parts)


def add_source_argument(parser: Any) -> None:
    parser.add_argument(
        "--sources", default=None, metavar="KINDS", type=_source_argument,
        help="New B-roll sources: all, off, or comma-separated places,images,press,tweets. "
        "Overrides saved per-short settings and SHORTSMITH_BROLL_SOURCES. Default off.",
    )


def _source_argument(value: str) -> str:
    try:
        parse_sources(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _manifest(short_dir: Path) -> dict:
    path = short_dir / MANIFEST
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if "enabled" in value:
        parse_sources(value["enabled"])
    items = value.get("items", [])
    if not isinstance(items, list):
        raise ValueError(f"{path}: items must be a list")
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise ValueError(f"{path}: items[{index}] needs a valid kind: {','.join(KINDS)}")
        if not isinstance(item.get("query"), str) or not item["query"].strip():
            raise ValueError(f"{path}: items[{index}] needs a nonempty query")
    return value


def enabled_sources(short_dir: Path, override: str | None = None) -> frozenset[str]:
    """Explicit switch > per-short manifest > environment > off.

    Explicit off is an emergency stop and does not read even a broken manifest.
    """
    if override is not None and not parse_sources(override):
        return frozenset()
    manifest = _manifest(short_dir)
    value = override if override is not None else manifest.get(
        "enabled", os.environ.get("SHORTSMITH_BROLL_SOURCES", "off")
    )
    return parse_sources(value)


def filter_slides(slides: list[dict], short_dir: Path, *, sources: str | None = None) -> list[dict]:
    enabled = enabled_sources(short_dir, sources)
    records = []
    transcript = ""
    if enabled and any(isinstance(slide, dict) and slide.get("source_kind") in enabled
                       for slide in slides):
        try:
            words = json.loads((short_dir / "assets" / "words.json").read_text(encoding="utf-8"))
            transcript = _transcript_hash(words)
            records = _cache(short_dir, transcript)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.warning("Source B-roll transcript evidence is unavailable: %s", exc)
    kept = []
    for slide in slides:
        if not isinstance(slide, dict):
            kept.append(slide)
            continue
        if "source_kind" not in slide:
            kept.append(slide)
        elif slide.get("source_kind") in enabled:
            try:
                if (slide.get("provenance", {}).get("transcript_sha256") != transcript
                        or not any(record.get("slide") == slide for record in records if isinstance(record, dict))):
                    raise ValueError("Source slide has no matching current transcript/provenance record")
                kept.append(_cached({"slide": slide,
                                     "sha256": slide.get("provenance", {}).get("asset_sha256")}, short_dir))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("Cached source B-roll skipped: %s", exc)
    return kept


def _normal(value: str) -> str:
    return " ".join(re.findall(r"\w+", html.unescape(value).casefold()))


def _text(words: list[dict]) -> str:
    return " ".join(str(word.get("text") or word.get("word") or "") for word in words)


def _transcript_hash(words: list[dict]) -> str:
    entries = [{"text": _normal(str(word.get("text") or word.get("word") or "")),
                "start": word.get("start"), "end": word.get("end")} for word in words]
    return hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _contains(phrase: str, text: str) -> bool:
    needle = _normal(phrase)
    return bool(needle) and f" {needle} " in f" {_normal(text)} "


def _tokens(words: list[dict]) -> list[tuple[str, float, float]]:
    return [(token, float(word["start"]), float(word["end"])) for word in words
            for token in _normal(str(word.get("text") or word.get("word") or "")).split()]


def _mentions(query: str, tokens: list[tuple[str, float, float]]) -> list[tuple[float, float]]:
    wanted = query.split()
    return [(tokens[i][1], tokens[i + len(wanted) - 1][2])
            for i in range(len(tokens) - len(wanted) + 1)
            if [token[0] for token in tokens[i : i + len(wanted)]] == wanted]


def _time(item: dict, words: list[dict], gaps: list[tuple[float, float]]) -> tuple[float, float]:
    query = _normal(item.get("query", ""))
    transcript = _normal(_text(words))
    if not query or f" {query} " not in f" {transcript} ":
        raise ValueError("query does not occur in the transcript")
    entity_name = _normal(item.get("entity_name", item["query"]))
    if not entity_name or f" {entity_name} " not in f" {transcript} ":
        raise ValueError("entity_name does not occur in the transcript")
    mentions = _mentions(query, _tokens(words))
    if "start" in item or "end" in item:
        start, end = float(item["start"]), float(item["end"])
        if not any(start < right and end > left for left, right in mentions):
            raise ValueError("Source times do not overlap the transcript mention")
    else:
        start, end = mentions[0]
    if not all(math.isfinite(t) for t in (start, end)) or end <= start or start < 0:
        raise ValueError("source times must be finite, nonnegative, and end after start")
    for left, right in gaps:
        if left <= (start + end) / 2 <= right:
            if "start" in item:
                timing = max(left, start), min(right, end)
            else:
                duration = min(CUTAWAY_DURATION, right - left)
                begin = max(left, min(start, right - duration))
                timing = begin, begin + duration
            if not any(timing[0] < b and timing[1] > a for a, b in mentions):
                raise ValueError("Final source times do not overlap the transcript mention")
            return timing
    raise ValueError("source mention has no free gap")


def _json(url: str) -> dict:
    raw, _, _ = source_capture.fetch_public(url)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Source API did not return an object")
    return value


def _fetch(url: str) -> bytes:
    return source_capture.fetch_public(url)[0]


def _entity(item: dict, *, place: bool = False) -> tuple[str, dict]:
    query = item.get("entity_name") or item["query"]
    qid = item.get("qid")
    if qid and (not isinstance(qid, str) or not re.fullmatch(r"Q[1-9]\d*", qid)):
        raise ValueError("Invalid Wikidata QID")
    ids = [qid] if qid else wikidata.search_entity_ids(_fetch, query)
    entities = wikidata.get_entities(_fetch, ids, "claims%7Clabels%7Caliases")
    matches = []
    for entity_id, entity in entities.items():
        names = [wikidata.label_of(entity)] + [a.get("value", "") for a in entity.get("aliases", {}).get("en", [])]
        if _normal(query) not in {_normal(name) for name in names}:
            continue
        claims = entity.get("claims", {})
        if place and (not claims.get("P625") or "Q5" in wikidata.claim_values(entity, "P31")):
            continue
        if not place and not wikidata.claim_values(entity, "P856"):
            continue
        matches.append((entity_id, entity))
    if len(matches) != 1:
        raise ValueError("Source entity is missing or ambiguous; supply its exact name and QID")
    return matches[0]


def _host(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Source URL must use public HTTPS")
    return parsed.hostname.lower().removeprefix("www.")


def _same_site(url: str, domain: str) -> bool:
    host = _host(url)
    return host == domain or host.endswith("." + domain)


def _canonical_key(url: str) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    parsed = urllib.parse.urlsplit(url)
    query = tuple(sorted((key, value) for key, value in urllib.parse.parse_qsl(parsed.query)
                         if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}))
    return _host(url), parsed.path.rstrip("/"), query


def _within_official(url: str, official: str) -> bool:
    """A Wikidata website can identify one tenant under a shared hostname."""
    site = urllib.parse.urlsplit(official)
    page = urllib.parse.urlsplit(url)
    if _host(url) != _host(official):
        return False
    prefix = _scope_path(site.path).rstrip("/")
    page_path = _scope_path(page.path)
    if prefix and (_host(url) != _host(official)
                   or (page_path != prefix and not page_path.startswith(prefix + "/"))):
        return False
    required = urllib.parse.parse_qs(site.query, keep_blank_values=True)
    actual = urllib.parse.parse_qs(page.query, keep_blank_values=True)
    return all(actual.get(key) == values for key, values in required.items())


def _scope_path(path: str) -> str:
    decoded = urllib.parse.unquote(path)
    while decoded != path:
        path, decoded = decoded, urllib.parse.unquote(decoded)
    return posixpath.normpath(path.replace("\\", "/") or "/")


class _Page(HTMLParser):
    """Extract evidence from source HTML without treating scripts as page copy."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.images: list[str] = []
        self.license = ""
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden += 1
        if tag == "meta":
            self.meta[attr.get("property") or attr.get("name") or ""] = attr.get("content") or ""
        if tag == "link" and "canonical" in (attr.get("rel") or "").split():
            self.canonical = attr.get("href") or ""
        if tag == "link" and "license" in (attr.get("rel") or "").split():
            self.license = attr.get("href") or ""
        if tag == "img" and attr.get("src"):
            self.images.append(attr["src"])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden = max(0, self._hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.text.append(data)


def _place(item: dict, dest: Path) -> dict:
    qid, entity = _entity(item, place=True)
    images = wikidata.current_claim_values(entity, "P18")
    if not images:
        raise ValueError("Exact place has no Wikidata P18 image")
    filename = images[0]
    url = wikidata.COMMONS_API + "?" + urllib.parse.urlencode({
        "action": "query", "titles": "File:" + filename, "prop": "imageinfo",
        "iiprop": "url|extmetadata", "format": "json",
    })
    pages = _json(url).get("query", {}).get("pages", {})
    records = [info for page in pages.values() for info in page.get("imageinfo", [])]
    if len(records) != 1:
        raise ValueError("Bound Commons image is unavailable")
    info = records[0]
    metadata = info.get("extmetadata", {})
    license_name = metadata.get("LicenseShortName", {}).get("value", "")
    if not re.match(r"^(CC BY(?:-SA)?(?: |$)|CC0(?: |$)|Public domain$)", license_name, re.I):
        raise ValueError("Place image has no confirmed reusable Commons license")
    source_url = info.get("descriptionurl", "")
    if not _same_site(source_url, "commons.wikimedia.org"):
        raise ValueError("Invalid Commons description URL")
    image_url = info.get("url", "")
    if not _same_site(image_url, "wikimedia.org"):
        raise ValueError("Image is not hosted by Wikimedia")
    path = source_capture.download_image(image_url, dest)
    return {"path": path, "source_url": source_url, "source_label": wikidata.label_of(entity),
            "qid": qid, "evidence": "Wikidata P625 and own P18", "image_url": image_url,
            "license": license_name, "license_url": metadata.get("LicenseUrl", {}).get("value", ""),
            "attribution": re.sub(r"<[^>]*>", "", metadata.get("Artist", {}).get("value", "")),
            "date": re.sub(r"<[^>]*>", "", metadata.get("DateTimeOriginal", {}).get("value", "")),
            "wikidata_url": f"https://www.wikidata.org/wiki/{qid}"}


def _identity_tokens(tokens: list[tuple[str, float, float]], names: list[str]) -> list[tuple[str, float, float]]:
    aliases = sorted({_normal(name) for name in names if _normal(name)}, key=len, reverse=True)
    out = []
    index = 0
    while index < len(tokens):
        alias = next((name.split() for name in aliases
                      if [entry[0] for entry in tokens[index:index + len(name.split())]] == name.split()), None)
        if alias:
            out.append((_ENTITY_TOKEN, tokens[index][1], tokens[index + len(alias) - 1][2]))
            index += len(alias)
        else:
            out.append(tokens[index])
            index += 1
    return out


def _identity_text(value: str, names: list[str]) -> str:
    tokens = [(token, 0.0, 0.0) for token in _normal(value).split()]
    return " ".join(token[0] for token in _identity_tokens(tokens, names))


def _claim_anchor(item: dict, quote: str, names: list[str], words: list[dict],
                  timing: tuple[float, float]) -> str:
    """Require the same literal subject and claim at the selected mention.

    Entity aliases are interchangeable. Mere co-occurrence, even in the same
    sentence, is not evidence that the organization owns another subject's claim.
    """
    query = _identity_text(item["query"], names)
    source = _identity_text(quote, names)
    if query == _ENTITY_TOKEN and item["kind"] == "images":
        return query  # An entity photo is a reference, not an event claim.
    if _CLAUSE_CHANGES.intersection(query.split()):
        raise ValueError("Source subject/claim anchor crosses different clauses")
    if _ENTITY_TOKEN in query.split():
        anchors = [query] if query.startswith(_ENTITY_TOKEN + " ") else []
    else:
        auxiliaries = "|".join(sorted(_SUBJECT_AUXILIARIES))
        pattern = rf"(?<!\S){_ENTITY_TOKEN}(?: (?:{auxiliaries}))* {re.escape(query)}(?!\S)"
        anchors = re.findall(pattern, source)
    tokens = _identity_tokens(_tokens(words), names)
    mentions = _mentions(_normal(item["query"]), _tokens(words))
    selected = ([span for span in mentions if timing[0] < span[1] and timing[1] > span[0]]
                if "start" in item else mentions[:1])
    for anchor in anchors:
        if f" {anchor} " not in f" {source} ":
            continue
        prefix = len(anchor.split()) - len(query.split())
        for index in range(len(tokens) - len(anchor.split()) + 1):
            if " ".join(token[0] for token in tokens[index:index + len(anchor.split())]) == anchor:
                span = tokens[index + prefix][1], tokens[index + len(anchor.split()) - 1][2]
                if span in selected:
                    return anchor
    raise ValueError("Source subject and claim are not grounded at the selected transcript mention")


def _official(item: dict, dest: Path, words: list[dict], timing: tuple[float, float]) -> dict:
    qid, entity = _entity(item)
    names = [wikidata.label_of(entity)] + [a.get("value", "") for a in entity.get("aliases", {}).get("en", [])]
    entity_words = {word for name in names for word in _normal(name).split()}
    entity_reference = item["kind"] == "images" and _normal(item["query"]) in {_normal(name) for name in names}
    if not entity_reference and not set(_normal(item["query"]).split()) - entity_words - _GENERIC_EVENT_WORDS:
        raise ValueError("Source query needs a concrete event subject beyond the source entity/announcement")
    url = item.get("url", "")
    official = [site.replace("http://", "https://", 1) for site in wikidata.claim_values(entity, "P856")]
    official_url = next((site for site in official if _within_official(url, site)), "")
    domain = _host(official_url) if official_url else ""
    asserted = item.get("official_domain", "").lower().removeprefix("www.")
    if not domain or (asserted and asserted != domain):
        raise ValueError("Page is not on the entity's Wikidata official website")
    raw, content_type, final = source_capture.fetch_public(url)
    if not _within_official(final, official_url) or "html" not in content_type.lower():
        raise ValueError("Official page redirected away or is not HTML")
    page = _Page()
    page.feed(raw.decode("utf-8"))
    canonical = urllib.parse.urljoin(final, page.canonical)
    if not page.canonical or not _within_official(canonical, official_url):
        raise ValueError("Official page lacks same-site canonical evidence")
    if _canonical_key(canonical) != _canonical_key(final):
        raise ValueError("Canonical URL points to a different page")
    visible = _normal(" ".join(page.text))
    quote = item.get("quote") or item["query"]
    if not _contains(quote, visible) or not _contains(
        _identity_text(item["query"], names), _identity_text(quote, names)
    ):
        raise ValueError("Official page does not contain the claimed source text")
    anchor = _claim_anchor(item, quote, names, words, timing)
    date = page.meta.get("article:published_time") or page.meta.get("date") or ""
    if item.get("date") and not date.startswith(item["date"]):
        raise ValueError("Source date is absent or differs from the requested event")
    evidence = "Wikidata P856, same-site final/canonical URL, source text"
    if item["kind"] == "press":
        if not re.search(r"press|news|release|media", urllib.parse.urlsplit(final).path, re.I):
            raise ValueError("Official page is not identified as a press/news release")
        path = source_capture.capture_page(final, dest, quote=quote)
        image_url = ""
    else:
        images = [urllib.parse.urljoin(final, src) for src in page.images]
        og_image = urllib.parse.urljoin(final, page.meta.get("og:image", ""))
        image_url = item.get("image_url") or (og_image if page.meta.get("og:image") else "")
        if not image_url or image_url not in images + ([og_image] if page.meta.get("og:image") else []):
            raise ValueError("Reference image is not bound to the verified original page")
        path = source_capture.download_image(image_url, dest)
    return {"path": path, "source_url": canonical, "source_label": wikidata.label_of(entity),
            "qid": qid, "evidence": evidence, "image_url": image_url, "date": date,
            "context_anchor": anchor, "context_quote": quote, "entity_names": names,
            "license": page.license or item.get("license") or "Not specified by source",
            "license_basis": "source" if page.license else "user supplied" if item.get("license") else "unknown"}


def _post_parts(url: str) -> tuple[str, str]:
    if _host(url) not in {"x.com", "twitter.com"}:
        raise ValueError("Tweet source must be an original X/Twitter status URL")
    match = re.fullmatch(r"/([A-Za-z0-9_]+)/status/([1-9]\d*)/?", urllib.parse.urlsplit(url).path)
    if not match:
        raise ValueError("Tweet URL needs its original account and status ID")
    return match.group(1), match.group(2)


def _tweet(item: dict, dest: Path) -> dict:
    url = item.get("url", "")
    author, status = _post_parts(url)
    canonical = f"https://x.com/{author}/status/{status}"
    data = _json("https://publish.twitter.com/oembed?" + urllib.parse.urlencode({
        "url": canonical, "omit_script": "true", "dnt": "true",
    }))
    author_url = data.get("author_url", "")
    if _host(author_url) not in {"x.com", "twitter.com"} or urllib.parse.urlsplit(author_url).path.strip("/").casefold() != author.casefold():
        raise ValueError("X oEmbed author differs from the original post URL")
    markup = data.get("html", "")
    post_links = re.findall(r'https://(?:twitter|x)\.com/[A-Za-z0-9_]+/status/[1-9]\d*', html.unescape(markup))
    if not any(_post_parts(link)[0].casefold() == author.casefold() and _post_parts(link)[1] == status for link in post_links):
        raise ValueError("X oEmbed does not identify the requested original post")
    paragraph = re.search(r"<p(?:\s[^>]*)?>(.*?)</p>", markup, re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]*>", " ", paragraph.group(1))) if paragraph else ""
    quote = item.get("quote", "")
    if not _contains(quote, text):
        raise ValueError("Tweet excerpt is absent from the original public post")
    account_words = set(_normal(data.get("author_name", "") + " " + author).split())
    if not set(_normal(item["query"]).split()) - account_words - _GENERIC_EVENT_WORDS:
        raise ValueError("Tweet query needs a concrete event subject beyond the account/announcement")
    if not _contains(item["query"], text):
        raise ValueError("Original post content does not match the transcript subject")
    if not _contains(item["query"], quote):
        raise ValueError("Tweet source excerpt does not contain the transcript event phrase")
    timestamp = ((int(status) >> 22) + 1288834974657) / 1000
    date = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
    if item.get("date") and item["date"] != date:
        raise ValueError("Original X post date differs from the requested date")
    path = source_capture.capture_page(canonical, dest, quote=quote)
    return {"path": path, "source_url": canonical, "source_label": "@" + author,
            "author": data.get("author_name", author), "date": date, "quote": text,
            "license": "Copyright retained by original author; no reuse license stated",
            "evidence": "X public oEmbed account, status ID, original text and date"}


def _key(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _raster(path: Path) -> bool:
    data = path.read_bytes()
    return (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")
            or (data.startswith(b"RIFF") and data[8:12] == b"WEBP"))


def _cache(short_dir: Path, transcript: str) -> list[dict]:
    try:
        data = json.loads((short_dir / PROVENANCE).read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("transcript_sha256") != transcript:
            return []
        records = data.get("assets", [])
        return records if isinstance(records, list) else []
    except (OSError, ValueError, AttributeError):
        return []


def _cached(record: dict, short_dir: Path) -> dict:
    slide = record["slide"]
    path = (short_dir / slide["src"]).resolve()
    if not path.is_relative_to(short_dir.resolve()) or not path.is_file() or not _raster(path):
        raise ValueError("Cached source file is missing or outside the project")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError("Cached source file changed since verification")
    if slide.get("source_kind") not in KINDS or slide.get("provenance", {}).get("verified") is not True:
        raise ValueError("Cached source has no verification evidence")
    if slide.get("type") != "image" or not slide.get("source_url") or not slide.get("source_label"):
        raise ValueError("Cached source is missing its original attribution")
    if slide.get("source_kind") in {"press", "images"}:
        proof = slide["provenance"]
        names = proof.get("entity_names")
        if (not proof.get("context_anchor") or not isinstance(proof.get("context_quote"), str)
                or not isinstance(names, list) or not names
                or any(not isinstance(name, str) or not name for name in names)):
            raise ValueError("Cached official source needs current subject/claim verification")
    return dict(slide)


def _place_mentions(words: list[dict]) -> list[dict]:
    # A capitalized name alone can be a person or company. Require location
    # grammar before proposing it without a semantic discovery provider.
    text = _text(words)
    proper_name = r"[A-Z][a-z]+(?:[ '-][A-Z][a-z]+)*"
    names = re.findall(r"\b(?:in|from|to|at|near|across|through|visiting|visit|visited|inside|outside|around)\s+(" + proper_name + r")\b", text)
    if re.fullmatch(proper_name, text.strip()):
        names.append(text.strip())
    return [{"kind": "places", "query": name} for name in dict.fromkeys(names)]


def _discovery_state(short_dir: Path, transcript: str) -> dict:
    try:
        data = json.loads((short_dir / REVIEW).read_text(encoding="utf-8"))
        if data.get("transcript_sha256") == transcript and data.get("discovery_version") == 1:
            value = data.get("discovery", {})
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _discover(words: list[dict], enabled: frozenset[str], review: list[dict],
              previous: dict) -> tuple[list[dict], dict]:
    items = _place_mentions(words) if "places" in enabled else []
    found, stages = source_discovery.discover_stages(words, enabled, previous, review)
    return items + found, stages


def should_retry(short_dir: Path, enabled: frozenset[str]) -> bool:
    """Retry incomplete requested work after recovery; leave complete searches cached."""
    if not enabled or os.environ.get("SHORTSMITH_BROLL_OFFLINE", "").lower() in {"1", "true", "yes", "on"}:
        return False
    try:
        review = json.loads((short_dir / REVIEW).read_text(encoding="utf-8"))
        if not isinstance(review, dict) or review.get("version") != 1 or not isinstance(review.get("items"), list):
            return True
        if review.get("offline"):
            return True
        for item in review.get("items", []):
            if not isinstance(item, dict):
                return True
            if item.get("status") != "skipped":
                continue
            stage = item.get("stage")
            kinds = item.get("kinds", list(enabled - {"tweets"}) if stage == "web discovery"
                             else ["tweets"] if stage == "X discovery" else [])
            if not enabled.intersection(kinds):
                continue
            retry_when = item.get("retry_when")
            if retry_when is None and stage in {"web discovery", "X discovery"}:
                retry_when = ("web available" if "ANTHROPIC_API_KEY is not set" in item.get("reason", "")
                              else "online")
            if retry_when == "online" or (retry_when == "web available" and os.environ.get("ANTHROPIC_API_KEY")):
                return True
    except (OSError, ValueError, AttributeError, TypeError):
        return True  # There is no evidence that the enabled source stages completed.
    return False


def record_no_free_gaps(short_dir: Path) -> None:
    """No source discovery is needed when the video has no cutaway slot."""
    _write_json(short_dir / REVIEW, {"version": 1, "offline": False, "items": [],
                                   "complete": "no free gaps"})


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _resolve(item: dict, short_dir: Path, timing: tuple[float, float], words: list[dict]) -> dict:
    kind = item["kind"]
    dest = short_dir / "assets" / "broll" / "sources" / (kind + "-" + _key(item) + ".png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = (_place(item, dest) if kind == "places" else _tweet(item, dest) if kind == "tweets"
              else _official(item, dest, words, timing))
    path = Path(result.pop("path")).resolve()
    if not path.is_relative_to(short_dir.resolve()) or not _raster(path):
        raise ValueError("Resolved source is not a local project raster image")
    return {"type": "image", "source_kind": kind, "start": timing[0], "end": timing[1],
            "src": path.relative_to(short_dir.resolve()).as_posix(),
            "title": item["query"],
            "source_label": result["source_label"], "source_url": result["source_url"],
            "date": result.get("date", ""), "attribution": result.get("attribution", ""),
            "license": result.get("license", ""),
            "provenance": {**result, "verified": True,
                           "asset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "retrieved_at": datetime.now(UTC).isoformat()}}


def generate(short_dir: Path, words: list[dict], gaps: list[tuple[float, float]], *,
             sources: str | None = None, dry_run: bool = False) -> list[dict]:
    """Resolve new slides; failed/uncertain sources are recorded for review.

    Offline only reuses transcript-bound, hash-checked local verified assets.
    Dry run prints the enabled plan and returns no unverified slides.
    """
    enabled = enabled_sources(short_dir, sources)
    if not enabled or not gaps:
        return []
    manifest = _manifest(short_dir)
    items = [item for item in manifest.get("items", []) if item["kind"] in enabled]
    if dry_run:
        print(f"  source B-roll plan: {','.join(sorted(enabled))}; {len(items)} explicit candidates; discovery and verification deferred")
        return []
    transcript = _transcript_hash(words)
    records = _cache(short_dir, transcript)
    offline = os.environ.get("SHORTSMITH_BROLL_OFFLINE", "").lower() in {"1", "true", "yes", "on"}
    review: list[dict] = []
    discovery = _discovery_state(short_dir, transcript)
    if not offline:
        found, discovery = _discover(words, enabled, review, discovery)
        items += found
    items += [record["candidate"] for record in records if isinstance(record, dict)
              and isinstance(record.get("candidate"), dict) and record["candidate"].get("kind") in enabled]
    cached = {_key(record["candidate"]): record for record in records if isinstance(record, dict)
              and isinstance(record.get("candidate"), dict)}
    slides, written, seen = [], [], set()
    for item in items:
        try:
            if not isinstance(item, dict) or item.get("kind") not in enabled:
                raise ValueError("Invalid discovered source candidate")
            if item.get("_discovery_error"):
                raise ValueError(item["_discovery_error"])
            key = _key(item)
            if key in seen:
                continue
            seen.add(key)
            timing = _time(item, words, gaps)
            if any(timing[0] < s["end"] and timing[1] > s["start"] for s in slides):
                continue
            if key in cached:
                try:
                    slide = _cached(cached[key], short_dir)
                except ValueError:
                    if offline:
                        raise
                    slide = _resolve(item, short_dir, timing, words)
                else:
                    if item["kind"] in {"press", "images"}:
                        proof = slide["provenance"]
                        _claim_anchor(item, proof["context_quote"], proof["entity_names"], words, timing)
                    slide = {**slide, "start": timing[0], "end": timing[1]}
            elif offline:
                raise ValueError("Offline: no verified local source cache")
            else:
                slide = _resolve(item, short_dir, timing, words)
            slide = {**slide, "provenance": {**slide["provenance"], "transcript_sha256": transcript}}
            slides.append(slide)
            written.append({"candidate": item, "slide": slide,
                            "sha256": hashlib.sha256((short_dir / slide["src"]).read_bytes()).hexdigest()})
            if slide["provenance"].get("license_basis") in {"unknown", "user supplied"}:
                review.append({"candidate": item, "status": "rights review", "reason": "Source match verified; reuse rights are not confirmed by the source"})
        except Exception as exc:
            log.warning("Source B-roll skipped: %s", exc)
            review.append({"candidate": item, "status": "skipped", "reason": str(exc)})
    preserved = [record for record in records if isinstance(record, dict)
                 and _key(record.get("candidate", {})) not in {_key(r["candidate"]) for r in written}]
    try:
        _write_json(short_dir / PROVENANCE, {"version": 1, "transcript_sha256": transcript,
                                           "assets": preserved + written})
        _write_json(short_dir / REVIEW, {"version": 1, "offline": offline, "items": review,
                                        "transcript_sha256": transcript, "discovery_version": 1,
                                        "discovery": discovery})
    except OSError as exc:
        log.warning("Source B-roll provenance could not be saved: %s", exc)
        return []
    return slides

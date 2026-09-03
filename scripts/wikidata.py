"""Shared Wikidata / Wikimedia Commons primitives.

Both b-roll asset resolvers are built on the same idea: resolve the thing being
mentioned to a Wikidata entity, then accept only files that entity points at.
The shared API plumbing lives here:

  * person_photos.py : people (P18 portrait, P180 depicts, P373 category)
  * brand_logos.py   : companies (P154 logo image)

Every function takes the HTTP getter as its first argument, so callers supply
their own cache / throttle / retry / offline policy (gen_broll's `_http_get`)
and this module stays free of network policy.
"""
from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable

# Returns response bytes, or None on any failure (offline, 404, timeout).
Fetch = Callable[[str], bytes | None]

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
HUMAN = "Q5"  # Wikidata: "human"


def api_json(fetch: Fetch, url: str) -> dict:
    """GET a MediaWiki API URL and parse it. Any failure yields {}."""
    raw = fetch(url)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def claim_values(entity: dict, prop: str) -> list[str]:
    """Flatten a claim to entity ids (for item values) or strings."""
    out: list[str] = []
    for claim in (entity.get("claims") or {}).get(prop, []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and "id" in value:
            out.append(value["id"])
        elif isinstance(value, str):
            out.append(value)
    return out


_RANK_ORDER = {"preferred": 0, "normal": 1, "deprecated": 2}


def current_claim_values(entity: dict, prop: str) -> list[str]:
    """Claim values ordered current-first.

    Plain `claim_values` returns statement order, which for a property like P154
    (logo image) is roughly chronological, so claims[0] is often the *oldest*
    logo. Microsoft's first P154 is its 1980 wordmark. Ordering by Wikidata rank
    and pushing anything with an end-time qualifier (P582, "used until") to the
    back surfaces the mark actually in use today.
    """
    scored: list[tuple[int, int, int, str]] = []
    for index, claim in enumerate((entity.get("claims") or {}).get(prop, [])):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and "id" in value:
            value = value["id"]
        if not isinstance(value, str):
            continue
        rank = _RANK_ORDER.get(claim.get("rank", "normal"), 1)
        retired = 1 if "P582" in (claim.get("qualifiers") or {}) else 0
        scored.append((rank, retired, index, value))
    return [value for *_, value in sorted(scored)]


def get_entities(fetch: Fetch, qids: list[str],
                 props: str = "claims%7Cdescriptions%7Clabels") -> dict[str, dict]:
    if not qids:
        return {}
    ids = "%7C".join(qids[:50])  # the API caps a batch at 50
    return api_json(fetch, f"{WIKIDATA_API}?action=wbgetentities&ids={ids}"
                           f"&props={props}&languages=en&format=json").get("entities") or {}


def search_entity_ids(fetch: Fetch, name: str, limit: int = 10) -> list[str]:
    query = urllib.parse.quote(name)
    result = api_json(fetch, f"{WIKIDATA_API}?action=wbsearchentities&search={query}"
                             f"&language=en&uselang=en&type=item&limit={limit}&format=json")
    return [hit["id"] for hit in (result.get("search") or []) if hit.get("id")]


def sitelinks_count(entity: dict) -> int:
    """How many Wikimedia projects have a page for the entity. A cheap
    notability signal: a substitute teacher has none, the MicroStrategy
    founder has dozens."""
    links = entity.get("sitelinks")
    return len(links) if isinstance(links, dict) else 0


def label_of(entity: dict) -> str:
    return ((entity.get("labels", {}).get("en") or {}).get("value") or "")


def description_of(entity: dict) -> str:
    return ((entity.get("descriptions", {}).get("en") or {}).get("value") or "")


def labels_for(fetch: Fetch, qids: list[str]) -> tuple[str, ...]:
    """Resolve a list of entity ids to their English labels, dropping misses."""
    if not qids:
        return ()
    entities = get_entities(fetch, qids, "labels")
    return tuple(label for qid in qids
                 if (label := label_of(entities.get(qid) or {})))


def strip_file_prefix(title: str) -> str:
    return title.split(":", 1)[1] if title.lower().startswith("file:") else title


def file_extension(filename: str, default: str = ".jpg") -> str:
    """Extension of a Commons filename, normalised.

    Anchored at the end rather than by substring search, so a file called
    "png sample.jpg" is a JPEG. `.jpeg` collapses to `.jpg` so a person or brand
    never caches under two spellings of the same format.
    """
    lower = filename.lower()
    for ext in (".svg", ".png", ".webp", ".jpeg", ".jpg"):
        if lower.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return default


def commons_file_url(title: str, width: int = 1280) -> str:
    """Direct-download URL for a Commons file, without an extra API round-trip.

    Special:FilePath redirects to the upload host, so this works for the raw
    file and (for rasters) for a resized rendition.
    """
    return ("https://commons.wikimedia.org/wiki/Special:FilePath/"
            f"{urllib.parse.quote(title.replace(' ', '_'))}?width={width}")


def commons_image_info(fetch: Fetch, titles: list[str],
                       width: int = 1280) -> dict[str, tuple[str, int]]:
    """Batch title -> (thumbnail url, thumbnail width) for up to 50 files."""
    if not titles:
        return {}
    joined = "%7C".join(urllib.parse.quote("File:" + t) for t in titles[:50])
    url = (f"{COMMONS_API}?action=query&titles={joined}&prop=imageinfo"
           f"&iiprop=url%7Csize&iiurlwidth={width}&format=json")
    pages = (api_json(fetch, url).get("query") or {}).get("pages") or {}
    out: dict[str, tuple[str, int]] = {}
    for page in pages.values():
        title = strip_file_prefix(page.get("title") or "")
        info = (page.get("imageinfo") or [{}])[0]
        src = info.get("thumburl") or info.get("url")
        got = int(info.get("thumbwidth") or info.get("width") or 0)
        if title and src:
            out[title] = (src, got)
    return out

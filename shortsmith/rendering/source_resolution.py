"""Resolve original source pages and images with identity checks."""

from __future__ import annotations

import html
import json
import posixpath
import re
import urllib.parse
from collections.abc import Sequence
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from shortsmith.records import TranscriptWord

from . import source_capture, source_evidence, wikidata


def fetch_json_object(url: str) -> dict:
    raw, _, _ = source_capture.fetch_public(url)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Source API did not return an object")
    return value


def fetch_public_bytes(url: str) -> bytes:
    return source_capture.fetch_public(url)[0]


def resolve_wikidata_entity(item: dict, *, place: bool = False) -> tuple[str, dict]:
    query = item.get("entity_name") or item["query"]
    qid = item.get("qid")
    if qid and (not isinstance(qid, str) or not re.fullmatch(r"Q[1-9]\d*", qid)):
        raise ValueError("Invalid Wikidata QID")
    ids = [qid] if qid else wikidata.search_entity_ids(fetch_public_bytes, query)
    entities = wikidata.get_entities(fetch_public_bytes, ids, "claims%7Clabels%7Caliases")
    matches = []
    for entity_id, entity in entities.items():
        names = [wikidata.label_of(entity)] + [
            a.get("value", "") for a in entity.get("aliases", {}).get("en", [])
        ]
        if source_evidence.normalize_text(query) not in {
            source_evidence.normalize_text(name) for name in names
        }:
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
    query = tuple(
        sorted(
            (key, value)
            for key, value in urllib.parse.parse_qsl(parsed.query)
            if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
        )
    )
    return _host(url), parsed.path.rstrip("/"), query


def _within_official(url: str, official: str) -> bool:
    """A Wikidata website can identify one tenant under a shared hostname."""
    site = urllib.parse.urlsplit(official)
    page = urllib.parse.urlsplit(url)
    if _host(url) != _host(official):
        return False
    prefix = _scope_path(site.path).rstrip("/")
    page_path = _scope_path(page.path)
    if prefix and (
        _host(url) != _host(official)
        or (page_path != prefix and not page_path.startswith(prefix + "/"))
    ):
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
        image_source = attr.get("src")
        if tag == "img" and image_source:
            self.images.append(image_source)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden = max(0, self._hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.text.append(data)


def resolve_place(item: dict, dest: Path) -> dict:
    qid, entity = resolve_wikidata_entity(item, place=True)
    images = wikidata.current_claim_values(entity, "P18")
    if not images:
        raise ValueError("Exact place has no Wikidata P18 image")
    filename = images[0]
    url = (
        wikidata.COMMONS_API
        + "?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "titles": "File:" + filename,
                "prop": "imageinfo",
                "iiprop": "url|extmetadata",
                "format": "json",
            }
        )
    )
    pages = fetch_json_object(url).get("query", {}).get("pages", {})
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
    return {
        "path": path,
        "source_url": source_url,
        "source_label": wikidata.label_of(entity),
        "qid": qid,
        "evidence": "Wikidata P625 and own P18",
        "image_url": image_url,
        "license": license_name,
        "license_url": metadata.get("LicenseUrl", {}).get("value", ""),
        "attribution": re.sub(r"<[^>]*>", "", metadata.get("Artist", {}).get("value", "")),
        "date": re.sub(r"<[^>]*>", "", metadata.get("DateTimeOriginal", {}).get("value", "")),
        "wikidata_url": f"https://www.wikidata.org/wiki/{qid}",
    }


def resolve_official_source(
    item: dict, dest: Path, words: Sequence[TranscriptWord], timing: tuple[float, float]
) -> dict:
    qid, entity = resolve_wikidata_entity(item)
    names = [wikidata.label_of(entity)] + [
        a.get("value", "") for a in entity.get("aliases", {}).get("en", [])
    ]
    entity_words = {word for name in names for word in source_evidence.normalize_text(name).split()}
    entity_reference = item["kind"] == "images" and source_evidence.normalize_text(
        item["query"]
    ) in {source_evidence.normalize_text(name) for name in names}
    if (
        not entity_reference
        and not set(source_evidence.normalize_text(item["query"]).split())
        - entity_words
        - source_evidence.GENERIC_EVENT_WORDS
    ):
        raise ValueError(
            "Source query needs a concrete event subject beyond the source entity/announcement"
        )
    url = item.get("url", "")
    official = [
        site.replace("http://", "https://", 1) for site in wikidata.claim_values(entity, "P856")
    ]
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
    visible = source_evidence.normalize_text(" ".join(page.text))
    quote = item.get("quote") or item["query"]
    if not source_evidence.contains_phrase(quote, visible) or not source_evidence.contains_phrase(
        source_evidence.identity_text(item["query"], names),
        source_evidence.identity_text(quote, names),
    ):
        raise ValueError("Official page does not contain the claimed source text")
    anchor = source_evidence.validate_claim_anchor(item, quote, names, words, timing)
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
        if not image_url or image_url not in images + (
            [og_image] if page.meta.get("og:image") else []
        ):
            raise ValueError("Reference image is not bound to the verified original page")
        path = source_capture.download_image(image_url, dest)
    return {
        "path": path,
        "source_url": canonical,
        "source_label": wikidata.label_of(entity),
        "qid": qid,
        "evidence": evidence,
        "image_url": image_url,
        "date": date,
        "context_anchor": anchor,
        "context_quote": quote,
        "entity_names": names,
        "license": page.license or item.get("license") or "Not specified by source",
        "license_basis": "source"
        if page.license
        else "user supplied"
        if item.get("license")
        else "unknown",
    }


def _post_parts(url: str) -> tuple[str, str]:
    if _host(url) not in {"x.com", "twitter.com"}:
        raise ValueError("Tweet source must be an original X/Twitter status URL")
    match = re.fullmatch(r"/([A-Za-z0-9_]+)/status/([1-9]\d*)/?", urllib.parse.urlsplit(url).path)
    if not match:
        raise ValueError("Tweet URL needs its original account and status ID")
    return match.group(1), match.group(2)


def resolve_post(item: dict, dest: Path) -> dict:
    url = item.get("url", "")
    author, status = _post_parts(url)
    canonical = f"https://x.com/{author}/status/{status}"
    data = fetch_json_object(
        "https://publish.twitter.com/oembed?"
        + urllib.parse.urlencode(
            {
                "url": canonical,
                "omit_script": "true",
                "dnt": "true",
            }
        )
    )
    author_url = data.get("author_url", "")
    if (
        _host(author_url) not in {"x.com", "twitter.com"}
        or urllib.parse.urlsplit(author_url).path.strip("/").casefold() != author.casefold()
    ):
        raise ValueError("X oEmbed author differs from the original post URL")
    markup = data.get("html", "")
    post_links = re.findall(
        r"https://(?:twitter|x)\.com/[A-Za-z0-9_]+/status/[1-9]\d*", html.unescape(markup)
    )
    if not any(
        _post_parts(link)[0].casefold() == author.casefold() and _post_parts(link)[1] == status
        for link in post_links
    ):
        raise ValueError("X oEmbed does not identify the requested original post")
    paragraph = re.search(r"<p(?:\s[^>]*)?>(.*?)</p>", markup, re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]*>", " ", paragraph.group(1))) if paragraph else ""
    quote = item.get("quote", "")
    if not source_evidence.contains_phrase(quote, text):
        raise ValueError("Tweet excerpt is absent from the original public post")
    account_words = set(
        source_evidence.normalize_text(data.get("author_name", "") + " " + author).split()
    )
    if (
        not set(source_evidence.normalize_text(item["query"]).split())
        - account_words
        - source_evidence.GENERIC_EVENT_WORDS
    ):
        raise ValueError(
            "Tweet query needs a concrete event subject beyond the account/announcement"
        )
    if not source_evidence.contains_phrase(item["query"], text):
        raise ValueError("Original post content does not match the transcript subject")
    if not source_evidence.contains_phrase(item["query"], quote):
        raise ValueError("Tweet source excerpt does not contain the transcript event phrase")
    timestamp = ((int(status) >> 22) + 1288834974657) / 1000
    date = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
    if item.get("date") and item["date"] != date:
        raise ValueError("Original X post date differs from the requested date")
    path = source_capture.capture_page(canonical, dest, quote=quote)
    return {
        "path": path,
        "source_url": canonical,
        "source_label": "@" + author,
        "author": data.get("author_name", author),
        "date": date,
        "quote": text,
        "license": "Copyright retained by original author; no reuse license stated",
        "evidence": "X public oEmbed account, status ID, original text and date",
    }

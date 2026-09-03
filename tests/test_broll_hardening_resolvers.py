"""Regression tests for defects found in the b-roll asset resolvers.

Each test below reproduces a failure that reached working code: a downloaded
photo thrown away at render time, a dead Wikidata pin that looked resolved, a
model response that crashed normalization, a stray sidecar served as an image,
and a truncated HTTP response that escaped the fetch layer.

All offline: `fetch` is a stub serving canned payloads, and urlopen is patched.
"""
from __future__ import annotations

import http.client
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brand_logos  # noqa: E402
import gen_broll  # noqa: E402
import person_photos as pp  # noqa: E402
import wikidata  # noqa: E402


def make_fetch(routes: dict[str, dict]):
    """Serve a canned dict for the first route whose key is in the URL."""
    calls: list[str] = []

    def fetch(url: str) -> bytes | None:
        calls.append(url)
        for needle, payload in routes.items():
            if needle in url:
                return json.dumps(payload).encode("utf-8")
        return None

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


# --------------------------------------------------------------------------- #
# A slide must keep the key that names what it is a picture of
# --------------------------------------------------------------------------- #
def test_person_slide_survives_the_render_time_reverification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model may omit the optional `name` and send only `person`.

    Asset resolution used to pop `person`, so the slide written to
    broll.auto.json named nobody. The render-time re-verification then read an
    empty name, failed to resolve it, and dropped a slide whose photo was
    already on disk, reporting it as "no verified photo".
    """
    short = tmp_path / "short-01-probe"
    monkeypatch.setattr(gen_broll, "_download_person",
                        lambda name, out_dir, **kw: f"assets/broll/person-{gen_broll._slug(name)}.jpg")
    slide = {"type": "person", "start": 5.0, "end": 9.0,
             "person": "Michael Saylor", "role": "Chairman, MicroStrategy"}

    resolved = gen_broll._resolve_assets([dict(slide)], short, dry_run=False)
    assert resolved[0]["person"] == "Michael Saylor"

    kept = gen_broll.verify_person_slides(resolved, short)
    assert [s["src"] for s in kept] == ["assets/broll/person-michaelsaylor.jpg"]


def test_logo_slide_keeps_the_brand_it_was_fetched_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gen_broll, "_download_logo",
                        lambda brand, out_dir: ("assets/broll/logo-ripple.svg", False))
    slide = {"type": "logo", "start": 12.0, "end": 14.2, "brand": "Ripple"}

    resolved = gen_broll._resolve_assets([slide], tmp_path / "short-01-probe", dry_run=False)
    assert resolved[0]["brand"] == "Ripple"
    assert resolved[0]["src"] == "assets/broll/logo-ripple.svg"


# --------------------------------------------------------------------------- #
# Model output is untrusted input
# --------------------------------------------------------------------------- #
def test_normalize_skips_an_entry_that_is_not_a_slide() -> None:
    """A JSON array from the model can hold a bare string or a nested list.

    Those reached `.get("type")` and raised AttributeError, which aborted the
    whole run for the short rather than dropping the one bad entry.
    """
    gaps = [(2.0, 12.0)]
    raw = ["Michael Saylor", ["nested"], None, 7,
           {"type": "text", "start": 3.0, "end": 6.0, "title": "Assets first."}]
    out = gen_broll._normalize(raw, gaps)
    assert [s["type"] for s in out] == ["text"]


# --------------------------------------------------------------------------- #
# A pin that no longer resolves must fail, not resolve to an empty entity
# --------------------------------------------------------------------------- #
def test_missing_entity_stub_is_reported_as_absent() -> None:
    """Wikidata answers a deleted id with {"id": ..., "missing": ""}, which is
    truthy, so callers that test `if entity:` treated it as a live item."""
    fetch = make_fetch({"wbgetentities": {"entities": {
        "Q1234567890": {"id": "Q1234567890", "missing": ""},
        "Q42": {"labels": {"en": {"value": "Douglas Adams"}}, "claims": {}},
    }}})
    got = wikidata.get_entities(fetch, ["Q1234567890", "Q42"])
    assert list(got) == ["Q42"]


def test_a_dead_person_pin_falls_through_to_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pp, "PERSON_QIDS", {"Jane Doe": "Q1234567890"})
    fetch = make_fetch({
        "ids=Q1234567890": {"entities": {"Q1234567890": {"id": "Q1234567890", "missing": ""}}},
        "wbsearchentities": {"search": [{"id": "Q7"}]},
        "wbgetentities": {"entities": {"Q7": {
            "labels": {"en": {"value": "Jane Doe"}},
            "descriptions": {"en": {"value": "American economist"}},
            "sitelinks": {f"w{i}": {} for i in range(9)},
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]},
        }}},
    })
    identity = pp.resolve_identity(fetch, "Jane Doe", "")
    assert identity is not None and identity.qid == "Q7"
    assert not identity.pinned
    assert any("wbsearchentities" in c for c in fetch.calls)


def test_a_dead_brand_pin_falls_through_to_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brand_logos, "BRAND_QIDS", {"Acme": "Q1234567890"})
    fetch = make_fetch({
        "ids=Q1234567890": {"entities": {"Q1234567890": {"id": "Q1234567890", "missing": ""}}},
        "wbsearchentities": {"search": [{"id": "Q9"}]},
        "wbgetentities": {"entities": {"Q9": {
            "labels": {"en": {"value": "Acme Corporation"}},
            "claims": {
                "P154": [{"mainsnak": {"datavalue": {"value": "Acme logo.svg"}}}],
                "P452": [{"mainsnak": {"datavalue": {"value": {"id": "Q1"}}}}],
            },
        }}},
    })
    identity = brand_logos.resolve_brand(fetch, "Acme")
    assert identity is not None and identity.qid == "Q9"
    assert identity.logo == "Acme logo.svg"


# --------------------------------------------------------------------------- #
# The photo cache holds photos
# --------------------------------------------------------------------------- #
def test_cache_lookup_ignores_a_non_image_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A <slug>.json note beside the photo sorts first alphabetically.

    Served as a slide `src` it makes the browser refuse the image and fails the
    whole Remotion render, and the stale-file sweep in _download_person would
    delete it on the next --fresh-photo run.
    """
    monkeypatch.setattr(gen_broll, "PEOPLE_DIR", tmp_path)
    (tmp_path / "michaelsaylor.json").write_text('{"source_url": "..."}', encoding="utf-8")
    (tmp_path / "michaelsaylor.png").write_bytes(b"png-bytes")

    assert [p.name for p in gen_broll._cached_person_photos("michaelsaylor")] == [
        "michaelsaylor.png"]
    assert gen_broll._cached_person_photo("michaelsaylor").suffix == ".png"


# --------------------------------------------------------------------------- #
# The fetch layer promises to return None, never to raise
# --------------------------------------------------------------------------- #
def test_a_truncated_response_returns_none_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """http.client.IncompleteRead is not an OSError, so it escaped the handler
    and crashed the caller mid-download."""
    monkeypatch.setattr(gen_broll, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(gen_broll, "_THROTTLE_SECONDS", 0.0)
    monkeypatch.delenv("SHORTSMITH_BROLL_OFFLINE", raising=False)
    monkeypatch.delenv("SHORTSMITH_BROLL_NOCACHE", raising=False)

    err = http.client.IncompleteRead(partial=b"abc", expected=900)
    with patch("urllib.request.urlopen", side_effect=err) as mock_open:
        assert gen_broll._http_get("https://x.test/asset.svg", max_retries=3) is None
    assert mock_open.call_count == 1

"""Public source switches and generation, with provider/capture boundaries stubbed."""
from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import source_broll as sb  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\noriginal-captured-pixels"
WORDS = [{"word": "Ripple", "start": 2.0, "end": 3.0},
         {"word": "announced", "start": 3.0, "end": 4.0},
         {"word": "payments", "start": 4.0, "end": 5.0}]
GAPS = [(1.0, 6.0)]


@pytest.fixture(autouse=True)
def no_live_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SHORTSMITH_BROLL_SOURCES", raising=False)
    monkeypatch.delenv("SHORTSMITH_BROLL_OFFLINE", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected live process")))
    monkeypatch.setattr(sb.source_capture, "fetch_public", lambda *a: (_ for _ in ()).throw(AssertionError("unexpected live HTTP")))


def manifest(short: Path, **value: object) -> None:
    (short / sb.MANIFEST).write_text(json.dumps(value), encoding="utf-8")


def claim(value: object) -> dict:
    return {"mainsnak": {"datavalue": {"value": value}}}


def entity(name: str, **claims: list) -> dict:
    return {"labels": {"en": {"value": name}}, "claims": claims}


def routes(monkeypatch: pytest.MonkeyPatch, values: dict[str, object]) -> list[str]:
    calls = []

    def fetch(url: str) -> tuple[bytes, str, str]:
        calls.append(url)
        for needle, value in values.items():
            if needle in url:
                if isinstance(value, tuple):
                    return value
                return json.dumps(value).encode(), "application/json", url
        raise ValueError("No matching source route: " + url)

    monkeypatch.setattr(sb.source_capture, "fetch_public", fetch)
    return calls


def capture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls = []

    def write(url: str, dest: Path, **kwargs: object) -> Path:
        calls.append((url, kwargs))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PNG)
        return dest

    monkeypatch.setattr(sb.source_capture, "download_image", write)
    monkeypatch.setattr(sb.source_capture, "capture_page", write)
    return calls


def official(monkeypatch: pytest.MonkeyPatch, *, page: str = "", final: str = "https://ripple.com/news/release") -> list[str]:
    page = page or ('<link rel="canonical" href="https://ripple.com/news/release">'
                    '<meta property="article:published_time" content="2026-10-12T10:00:00Z">'
                    '<meta property="og:image" content="https://cdn.ripple.com/payments.png">'
                    '<article>Ripple announced payments.</article>')
    return routes(monkeypatch, {
        "wbgetentities": {"entities": {"Q1": entity("Ripple", P856=[claim("https://ripple.com/")])}},
        "/news/release": (page.encode(), "text/html", final),
    })


def item(kind: str = "press", **kwargs: object) -> dict:
    return {"kind": kind, "query": "Ripple announced payments",
            "entity_name": "Ripple", "qid": "Q1",
            "url": "https://ripple.com/news/release", "quote": "Ripple announced payments.", **kwargs}


def test_switch_precedence_and_individual_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert sb.enabled_sources(tmp_path) == frozenset()
    monkeypatch.setenv("SHORTSMITH_BROLL_SOURCES", "tweets,images")
    assert sb.enabled_sources(tmp_path) == {"tweets", "images"}
    manifest(tmp_path, enabled=["press"])
    assert sb.enabled_sources(tmp_path) == {"press"}
    assert sb.enabled_sources(tmp_path, "places") == {"places"}
    assert sb.enabled_sources(tmp_path, "all") == set(sb.KINDS)
    assert not sb.enabled_sources(tmp_path, "off")


@pytest.mark.parametrize("value", ["map", "all,press", "off,images", [1], None])
def test_invalid_switch_fails_clearly(value: object) -> None:
    with pytest.raises(ValueError, match="source|sources"):
        sb.parse_sources(value)


@pytest.mark.parametrize("value", [[], {"enabled": ["bad"]}, {"items": {}},
                                   {"items": [{"kind": "press"}]}, {"items": [{"kind": "bad", "query": "Ripple"}]}])
def test_malformed_manifest_fails_clearly(tmp_path: Path, value: object) -> None:
    (tmp_path / sb.MANIFEST).write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        sb.enabled_sources(tmp_path)


def test_explicit_off_bypasses_broken_manifest_and_does_no_work(tmp_path: Path) -> None:
    path = tmp_path / sb.MANIFEST
    path.write_text("broken json", encoding="utf-8")
    assert sb.generate(tmp_path, WORDS, GAPS, sources="off") == []
    assert list(tmp_path.iterdir()) == [path]


def test_enabled_dry_run_does_no_api_capture_or_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert sb.generate(tmp_path, WORDS, GAPS, sources="all", dry_run=True) == []
    assert list(tmp_path.iterdir()) == []


def test_cli_argument_is_shared() -> None:
    parser = argparse.ArgumentParser()
    sb.add_source_argument(parser)
    assert parser.parse_args(["--sources", "places,press"]).sources == "places,press"
    assert parser.parse_args([]).sources is None


@pytest.mark.parametrize("kind", ["press", "images"])
def test_original_official_source_produces_local_attributed_slide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    manifest(tmp_path, enabled=[kind], items=[item(kind)])
    official(monkeypatch)
    captures = capture(monkeypatch)
    slides = sb.generate(tmp_path, WORDS, GAPS)
    assert len(slides) == 1
    slide = slides[0]
    assert (slide["type"], slide["source_kind"], slide["start"], slide["end"]) == ("image", kind, 2.0, 5.5)
    assert slide["src"].startswith("assets/broll/sources/")
    assert slide["source_url"] == "https://ripple.com/news/release"
    assert slide["provenance"]["date"] == "2026-10-12T10:00:00Z"
    assert slide["provenance"]["asset_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert captures[0][0] == ("https://ripple.com/news/release" if kind == "press" else "https://cdn.ripple.com/payments.png")
    (tmp_path / "assets" / "words.json").write_text(json.dumps(WORDS))
    assert sb.filter_slides(slides, tmp_path) == slides
    saved = json.loads((tmp_path / sb.PROVENANCE).read_text())
    assert saved["assets"][0]["slide"] == slide


def test_cached_sources_filter_off_while_legacy_and_manual_remain(tmp_path: Path) -> None:
    legacy = [{"type": "stat"}, {"type": "image", "src": "manual.png"}]
    slides = legacy + [{"source_kind": "press"}, {"source_kind": "unknown"}]
    assert sb.filter_slides(slides, tmp_path, sources="off") == legacy
    assert sb.filter_slides(slides, tmp_path, sources="all") == legacy


def test_modified_cache_is_not_rendered_or_reused_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item()])
    official(monkeypatch)
    capture(monkeypatch)
    slides = sb.generate(tmp_path, WORDS, GAPS)
    (tmp_path / "assets" / "words.json").write_text(json.dumps(WORDS))
    (tmp_path / slides[0]["src"]).write_bytes(PNG + b"changed")
    assert sb.filter_slides(slides, tmp_path) == []
    monkeypatch.setenv("SHORTSMITH_BROLL_OFFLINE", "1")
    assert sb.generate(tmp_path, WORDS, GAPS) == []
    assert "changed since verification" in (tmp_path / sb.REVIEW).read_text()


def test_offline_reuses_only_matching_transcript_verified_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item()])
    calls = official(monkeypatch)
    captures = capture(monkeypatch)
    first = sb.generate(tmp_path, WORDS, GAPS)
    count = len(calls), len(captures)
    monkeypatch.setenv("SHORTSMITH_BROLL_OFFLINE", "true")
    assert sb.generate(tmp_path, WORDS, GAPS) == first
    assert (len(calls), len(captures)) == count
    assert sb.generate(tmp_path, [*WORDS, {"word": "changed", "start": 5, "end": 6}], GAPS) == []


@pytest.mark.parametrize("overrides", [
    {"url": "https://ripple.com.attacker.test/news/release"},
    {"official_domain": "attacker.test"},
    {"quote": "invented source words"},
    {"date": "2020-01-01"},
    {"query": "not in transcript"},
    {"start": float("nan"), "end": 4},
    {"start": 10, "end": 12},
])
def test_unverified_candidates_are_reviewed_without_a_slide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(**overrides)])
    official(monkeypatch)
    captures = capture(monkeypatch)
    assert sb.generate(tmp_path, WORDS, GAPS) == []
    assert not captures
    assert json.loads((tmp_path / sb.REVIEW).read_text())["items"][-1]["status"] == "skipped"


@pytest.mark.parametrize("page,final", [
    ('<article>Ripple announced payments.</article>', "https://ripple.com/news/release"),
    ('<link rel="canonical" href="https://ripple.com/news/other"><article>Ripple announced payments.</article>', "https://ripple.com/news/release"),
    ('<link rel="canonical" href="https://attacker.test/news/release"><article>Ripple announced payments.</article>', "https://attacker.test/news/release"),
    ('<link rel="canonical" href="https://ripple.com/news/release"><script>Ripple announced payments.</script>', "https://ripple.com/news/release"),
])
def test_canonical_redirect_and_visible_text_are_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page: str, final: str) -> None:
    manifest(tmp_path, enabled=["press"], items=[item()])
    official(monkeypatch, page=page, final=final)
    assert sb.generate(tmp_path, WORDS, GAPS) == []


def test_image_must_be_bound_to_verified_original_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["images"], items=[item("images", image_url="https://images.test/unrelated.png")])
    official(monkeypatch)
    assert sb.generate(tmp_path, WORDS, GAPS) == []


def place_routes(monkeypatch: pytest.MonkeyPatch, *, ambiguous: bool = False, license_name: str = "CC BY-SA 4.0") -> list[str]:
    place = entity("New York City", P625=[claim({"latitude": 40.71, "longitude": -74.0})],
                   P18=[claim("NYC.jpg")])
    return routes(monkeypatch, {
        "wbsearchentities": {"search": [{"id": "Q60"}] + ([{"id": "Q61"}] if ambiguous else [])},
        "wbgetentities": {"entities": {"Q60": place, **({"Q61": place} if ambiguous else {})}},
        "imageinfo": {"query": {"pages": {"1": {"imageinfo": [{
            "url": "https://upload.wikimedia.org/NYC.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:NYC.jpg",
            "extmetadata": {"LicenseShortName": {"value": license_name},
                            "Artist": {"value": "<a>Original photographer</a>"}},
        }]}}}},
    })


def test_places_discover_transcript_name_and_use_its_own_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = place_routes(monkeypatch)
    captures = capture(monkeypatch)
    words = [{"text": "New York City", "start": 2, "end": 3}]
    slides = sb.generate(tmp_path, words, GAPS, sources="places")
    assert len(slides) == 1
    assert slides[0]["provenance"]["qid"] == "Q60"
    assert slides[0]["provenance"]["attribution"] == "Original photographer"
    assert captures[0][0] == "https://upload.wikimedia.org/NYC.jpg"
    assert any("NYC.jpg" in call for call in calls)


@pytest.mark.parametrize("ambiguous,license_name", [(True, "CC BY-SA 4.0"), (False, "All rights reserved")])
def test_ambiguous_or_unlicensed_place_is_reviewed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ambiguous: bool, license_name: str) -> None:
    place_routes(monkeypatch, ambiguous=ambiguous, license_name=license_name)
    captures = capture(monkeypatch)
    assert sb.generate(tmp_path, [{"text": "New York City", "start": 2, "end": 3}], GAPS, sources="places") == []
    assert not captures


def test_named_place_cannot_resolve_to_neighboring_entity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["places"], items=[{"kind": "places", "query": "Ripple", "qid": "Q60"}])
    place_routes(monkeypatch)
    assert sb.generate(tmp_path, WORDS, GAPS) == []


def tweet_routes(monkeypatch: pytest.MonkeyPatch, *, author: str = "Ripple", text: str = "Ripple announced payments.") -> None:
    routes(monkeypatch, {"oembed": {
        "author_url": "https://twitter.com/" + author, "author_name": author,
        "html": '<blockquote><p>' + text + '</p><a href="https://twitter.com/Ripple/status/1500000000000000000">Date</a></blockquote>',
    }})


def tweet_item(**kwargs: object) -> dict:
    return {"kind": "tweets", "query": "Ripple announced payments", "url": "https://x.com/Ripple/status/1500000000000000000",
            "quote": "Ripple announced payments.", **kwargs}


def test_original_public_tweet_is_verified_then_captured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["tweets"], items=[tweet_item()])
    tweet_routes(monkeypatch)
    captures = capture(monkeypatch)
    slides = sb.generate(tmp_path, WORDS, GAPS)
    assert len(slides) == 1
    assert slides[0]["source_label"] == "@Ripple"
    assert slides[0]["provenance"]["date"] == "2022-03-05"
    assert captures == [("https://x.com/Ripple/status/1500000000000000000", {"quote": "Ripple announced payments."})]
    assert "X discovery" in (tmp_path / sb.REVIEW).read_text()


@pytest.mark.parametrize("overrides,author,text", [
    ({}, "OtherAuthor", "Ripple announced payments."),
    ({}, "Ripple", "A different original post."),
    ({"url": "https://x.com/Ripple"}, "Ripple", "Ripple announced payments."),
    ({"url": "https://attacker.test/Ripple/status/1500000000000000000"}, "Ripple", "Ripple announced payments."),
    ({"date": "2020-01-01"}, "Ripple", "Ripple announced payments."),
])
def test_false_tweet_candidates_are_not_inserted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict, author: str, text: str) -> None:
    manifest(tmp_path, enabled=["tweets"], items=[tweet_item(**overrides)])
    tweet_routes(monkeypatch, author=author, text=text)
    assert sb.generate(tmp_path, WORDS, GAPS) == []


def test_provider_failure_keeps_explicit_sources_working(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def __init__(self, **kwargs: object) -> None:
            raise RuntimeError("provider unavailable")

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    manifest(tmp_path, enabled=["press"], items=[item()])
    official(monkeypatch)
    capture(monkeypatch)
    assert len(sb.generate(tmp_path, WORDS, GAPS)) == 1
    assert "provider unavailable" in (tmp_path / sb.REVIEW).read_text()


def test_verified_source_takes_priority_over_a_generic_logo_at_the_same_mention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gen_broll

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "words.json").write_text(json.dumps(WORDS), encoding="utf-8")
    (tmp_path / "meta.json").write_text(
        json.dumps({"_shortsmith": {"duration": 8}}), encoding="utf-8")
    manifest(tmp_path, enabled=["press"], items=[item()])
    official(monkeypatch)
    capture(monkeypatch)
    cache = tmp_path / "http-cache"
    cache.mkdir()
    monkeypatch.setattr(gen_broll, "_CACHE_DIR", cache)
    key = hashlib.sha1(b"https://cdn.simpleicons.org/ripple").hexdigest()
    (cache / key).write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><title>Ripple</title></svg>')
    result = gen_broll.generate(tmp_path, heuristic=True, cap=6, dry_run=False)
    slides = json.loads(result.read_text(encoding="utf-8"))
    assert [slide["source_kind"] for slide in slides] == ["press"]


def test_web_discovery_rejects_hallucinated_uncited_source_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        messages: object

        def __init__(self) -> None:
            self.messages = self

        def create(self, **kwargs: object) -> object:
            assert kwargs["tools"][0]["name"] == "web_search"
            payload = {"content": [
                {"type": "text", "text": json.dumps([item(), item(url="https://ripple.com/news/invented")])},
                {"type": "web_search_tool_result", "content": [
                    {"type": "web_search_result", "url": "https://ripple.com/news/release"}]},
            ]}
            return types.SimpleNamespace(model_dump=lambda: payload)

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    official(monkeypatch)
    capture(monkeypatch)
    slides = sb.generate(tmp_path, WORDS, GAPS, sources="press")
    assert [slide["source_url"] for slide in slides] == ["https://ripple.com/news/release"]


def test_cached_source_cannot_escape_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PNG)
    slide = {"type": "image", "source_kind": "press", "src": "../outside.png",
             "source_url": "https://ripple.com/news/release", "source_label": "Ripple",
             "provenance": {"verified": True, "asset_sha256": hashlib.sha256(PNG).hexdigest()}}
    assert sb.filter_slides([slide], tmp_path, sources="press") == []


def test_event_query_can_differ_from_the_official_entity_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(query="announced payments", entity_name="Ripple")])
    official(monkeypatch)
    capture(monkeypatch)
    slide = sb.generate(tmp_path, WORDS, GAPS)[0]
    assert (slide["start"], slide["end"]) == (2.5, 6.0)
    assert slide["date"] == "2026-10-12T10:00:00Z"


def test_cutaway_uses_existing_short_duration_in_a_long_free_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item()])
    official(monkeypatch)
    capture(monkeypatch)
    slide = sb.generate(tmp_path, WORDS, [(0, 90)])[0]
    assert (slide["start"], slide["end"]) == (2.0, 5.5)


def test_explicit_time_must_overlap_real_mention_even_in_same_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(start=50, end=55)])
    official(monkeypatch)
    capture(monkeypatch)
    assert sb.generate(tmp_path, WORDS, [(0, 90)]) == []
    assert "do not overlap" in (tmp_path / sb.REVIEW).read_text()


def test_absent_entity_name_cannot_bind_unrelated_organization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(entity_name="Other Company")])
    official(monkeypatch)
    assert sb.generate(tmp_path, WORDS, GAPS) == []


def test_capitalized_person_name_is_not_guessed_to_be_a_place(tmp_path: Path) -> None:
    words = [{"text": "Jordan said payments are fast", "start": 2, "end": 4}]
    assert sb.generate(tmp_path, words, GAPS, sources="places") == []
    assert json.loads((tmp_path / sb.REVIEW).read_text())["items"] == []


def test_place_location_grammar_finds_exact_named_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    place_routes(monkeypatch)
    capture(monkeypatch)
    slides = sb.generate(tmp_path, [{"text": "We met in New York City", "start": 2, "end": 3}], GAPS, sources="places")
    assert slides[0]["source_label"] == "New York City"


def test_empty_gaps_do_no_generation_work(tmp_path: Path) -> None:
    assert sb.generate(tmp_path, WORDS, [], sources="all") == []
    assert list(tmp_path.iterdir()) == []


def test_explicit_valid_timing_is_kept_inside_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(start=2, end=4)])
    official(monkeypatch)
    capture(monkeypatch)
    slide = sb.generate(tmp_path, WORDS, GAPS)[0]
    assert (slide["start"], slide["end"]) == (2.0, 4.0)


def test_original_capture_failure_is_reviewed_without_a_fake_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item()])
    official(monkeypatch)

    def unavailable(*args: object, **kwargs: object) -> Path:
        raise RuntimeError("Original page cannot render")

    monkeypatch.setattr(sb.source_capture, "capture_page", unavailable)
    assert sb.generate(tmp_path, WORDS, GAPS) == []
    assert "Original page cannot render" in (tmp_path / sb.REVIEW).read_text()


def test_grok_worker_uses_requested_low_cost_research_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, dest = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(json.dumps({"prompt": "Search X (Twitter) for original Ripple posts"}))
    calls = []

    def run(command: list[str], **kwargs: object) -> object:
        calls.append(command)
        return types.SimpleNamespace(returncode=0, stdout="[]")

    monkeypatch.setattr(sb.source_discovery.shutil, "which", lambda name: "grok.exe")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", [str(Path(sb.source_discovery.__file__)), "--grok-worker", str(source), str(dest)])
    runpy.run_path(str(Path(sb.source_discovery.__file__)), run_name="__main__")
    assert calls[0][:6] == ["grok.exe", "--single", "--model", "grok-build", "--reasoning-effort", "low"]
    assert json.loads(dest.read_text()) == {"stdout": "[]", "returncode": 0}


def test_x_discovery_proposes_real_posts_through_hidden_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []

    def run(command: list[str], **kwargs: object) -> object:
        if command[0] == "wscript.exe":
            prompts.append(json.loads(Path(command[-2]).read_text())["prompt"])
            Path(command[-1]).write_text(json.dumps({"returncode": 0, "stdout": json.dumps([tweet_item()])}))
        else:
            prompts.append(command[-1])
        return types.SimpleNamespace(returncode=0, stdout=json.dumps([tweet_item()]))

    monkeypatch.setattr(sb.source_discovery.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(sb.source_discovery.shutil, "which", lambda name: "grok.exe")
    monkeypatch.setattr(subprocess, "run", run)
    tweet_routes(monkeypatch)
    capture(monkeypatch)
    slides = sb.generate(tmp_path, WORDS, GAPS, sources="tweets")
    assert slides[0]["source_url"] == "https://x.com/Ripple/status/1500000000000000000"
    assert "Search X (Twitter)" in prompts[0]
    assert '"word": "Ripple"' in prompts[0]


@pytest.mark.parametrize("query", ["Ripple", "Ripple announced", "the announcement", "press release"])
def test_press_entity_and_generic_anchors_cannot_verify_a_wrong_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    words = [{"text": "Ripple announced payments and the announcement was a press release", "start": 2, "end": 4}]
    manifest(tmp_path, enabled=["press"], items=[item(query=query, quote="Ripple funded a charity concert.")])
    page = ('<link rel="canonical" href="https://ripple.com/news/release">'
            '<article>Ripple funded a charity concert. The announcement was a press release.</article>')
    official(monkeypatch, page=page)
    captures = capture(monkeypatch)
    assert sb.generate(tmp_path, words, GAPS) == []
    assert not captures
    assert "concrete event subject" in (tmp_path / sb.REVIEW).read_text()


def test_source_anchor_requires_whole_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    words = [{"text": "Ripple pay", "start": 2, "end": 4}]
    manifest(tmp_path, enabled=["press"], items=[item(query="Ripple pay", quote="Ripple announced payments.")])
    official(monkeypatch)
    assert sb.generate(tmp_path, words, GAPS) == []


@pytest.mark.parametrize("change", ["text", "times", "ledger", "missing transcript"])
def test_cached_source_filter_requires_current_transcript_and_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    manifest(tmp_path, enabled=["press"], items=[item()])
    official(monkeypatch)
    capture(monkeypatch)
    slides = sb.generate(tmp_path, WORDS, GAPS)
    words_path = tmp_path / "assets" / "words.json"
    words_path.write_text(json.dumps(WORDS))
    assert sb.filter_slides(slides, tmp_path) == slides
    if change == "text":
        words_path.write_text(json.dumps([{**word, "word": "charity"} for word in WORDS]))
    elif change == "times":
        words_path.write_text(json.dumps([{**word, "start": word["start"] + 10, "end": word["end"] + 10} for word in WORDS]))
    elif change == "ledger":
        (tmp_path / sb.PROVENANCE).unlink()
    else:
        words_path.unlink()
    assert sb.filter_slides(slides, tmp_path) == []


def test_overlay_title_is_verified_query_instead_of_model_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(title="Ripple bought the Moon")])
    official(monkeypatch)
    capture(monkeypatch)
    assert sb.generate(tmp_path, WORDS, GAPS)[0]["title"] == "Ripple announced payments"


@pytest.mark.parametrize("official_url,candidate_url", [
    ("https://sites.google.com/view/ripple-company", "https://sites.google.com/view/impostor/news/release"),
    ("https://shared.test/news?company=ripple", "https://shared.test/news?company=impostor"),
    ("https://shared.test/news?company=ripple", "https://shared.test/news?company=ripple&company=impostor"),
    ("https://shared.test/news?company=", "https://shared.test/news?company=impostor"),
    ("https://ripple.com", "https://members.ripple.com/news/release"),
    ("https://sites.google.com/view/ripple-company", "https://sites.google.com/view/ripple-company/%252e%252e/impostor/news/release"),
])
def test_official_identity_keeps_tenant_path_and_query_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, official_url: str, candidate_url: str) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(url=candidate_url)])
    routes(monkeypatch, {"wbgetentities": {"entities": {"Q1": entity("Ripple", P856=[claim(official_url)])}}})
    assert sb.generate(tmp_path, WORDS, GAPS) == []
    assert "not on the entity" in (tmp_path / sb.REVIEW).read_text()


def test_different_canonical_query_cannot_bind_another_article(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(url="https://ripple.com/news/release?id=2")])
    page = ('<link rel="canonical" href="https://ripple.com/news/release?id=1">'
            '<article>Ripple announced payments.</article>')
    official(monkeypatch, page=page, final="https://ripple.com/news/release?id=2")
    assert sb.generate(tmp_path, WORDS, GAPS) == []


def test_cli_rejects_unknown_source_before_work() -> None:
    parser = argparse.ArgumentParser()
    sb.add_source_argument(parser)
    with pytest.raises(SystemExit) as result:
        parser.parse_args(["--sources", "palces"])
    assert result.value.code == 2


def test_tweet_account_name_cannot_verify_a_different_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["tweets"], items=[tweet_item(query="Ripple", quote="We funded a charity concert.")])
    tweet_routes(monkeypatch, text="We funded a charity concert.")
    assert sb.generate(tmp_path, WORDS, GAPS) == []
    assert "concrete event subject" in (tmp_path / sb.REVIEW).read_text()


def test_source_query_must_be_in_the_captured_quote_not_navigation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item(quote="Ripple funded a charity concert.")])
    page = ('<link rel="canonical" href="https://ripple.com/news/release">'
            '<nav>Ripple announced payments.</nav><article>Ripple funded a charity concert.</article>')
    official(monkeypatch, page=page)
    assert sb.generate(tmp_path, WORDS, GAPS) == []


def test_official_root_website_needs_no_trailing_slash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, enabled=["press"], items=[item()])
    official(monkeypatch)
    page_fetch = sb.source_capture.fetch_public

    def fetch(url: str) -> tuple[bytes, str, str]:
        if "wbgetentities" in url:
            return json.dumps({"entities": {"Q1": entity("Ripple", P856=[claim("https://ripple.com")])}}).encode(), "application/json", url
        return page_fetch(url)

    monkeypatch.setattr(sb.source_capture, "fetch_public", fetch)
    capture(monkeypatch)
    assert len(sb.generate(tmp_path, WORDS, GAPS)) == 1

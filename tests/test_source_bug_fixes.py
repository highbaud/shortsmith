"""Regressions for source context, timing, and recovery after skipped discovery."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import apply_remotion as ar  # noqa: E402
import source_broll as sb  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nsource fixture pixels"


@pytest.fixture(autouse=True)
def no_live_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "SHORTSMITH_BROLL_OFFLINE", "SHORTSMITH_BROLL_SOURCES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sb.source_capture, "fetch_public", lambda *_: pytest.fail("Unexpected HTTP"))


def words(text: str) -> list[dict]:
    return [{"text": token, "start": 2 + i / 4, "end": 2 + (i + 1) / 4}
            for i, token in enumerate(text.split())]


def save_manifest(root: Path, items: list[dict]) -> None:
    (root / sb.MANIFEST).write_text(json.dumps({"enabled": ["press", "images"], "items": items}))


def candidate(entity: str = "Ripple", **overrides: object) -> dict:
    return {"kind": "press", "query": "announced payments", "entity_name": entity,
            "qid": "Q1", "url": "https://source.test/news/release",
            "quote": entity + " announced payments.", **overrides}


def official(monkeypatch: pytest.MonkeyPatch, *, label: str = "Ripple",
             aliases: tuple[str, ...] = (), quote: str = "Ripple announced payments.") -> None:
    entity = {"labels": {"en": {"value": label}},
              "aliases": {"en": [{"value": alias} for alias in aliases]},
              "claims": {"P856": [{"mainsnak": {"datavalue": {"value": "https://source.test/"}}}]}}
    page = ('<link rel="canonical" href="https://source.test/news/release">'
            '<meta property="og:image" content="https://source.test/reference.png">'
            '<article>' + quote + '</article>').encode()

    def fetch(url: str) -> tuple[bytes, str, str]:
        if "wbgetentities" in url:
            return json.dumps({"entities": {"Q1": entity}}).encode(), "application/json", url
        assert url == "https://source.test/news/release"
        return page, "text/html", url

    def capture(_url: str, dest: Path, **_kwargs: object) -> Path:
        dest.write_bytes(PNG)
        return dest

    monkeypatch.setattr(sb.source_capture, "fetch_public", fetch)
    monkeypatch.setattr(sb.source_capture, "capture_page", capture)
    monkeypatch.setattr(sb.source_capture, "download_image", capture)


@pytest.mark.parametrize("kind", ["press", "images"])
def test_source_company_must_own_the_transcript_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    save_manifest(tmp_path, [candidate("Acme", kind=kind)])
    official(monkeypatch, label="Acme", quote="Acme announced payments.")
    assert sb.generate(tmp_path, words("Ripple announced payments while Acme denied plans"), [(1, 8)]) == []
    assert "subject" in (tmp_path / sb.REVIEW).read_text()


def test_a_later_matching_company_does_not_verify_the_first_query_mention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_manifest(tmp_path, [candidate("Acme")])
    official(monkeypatch, label="Acme", quote="Acme announced payments.")
    transcript = words("Ripple announced payments then later Acme announced payments")
    assert sb.generate(tmp_path, transcript, [(1, 8)]) == []


@pytest.mark.parametrize("query", ["announced payments", "Ripple Labs announced payments"])
def test_source_claim_accepts_wikidata_aliases_without_joining_unrelated_clauses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str,
) -> None:
    save_manifest(tmp_path, [candidate("Ripple Labs", query=query, quote="Ripple announced payments.")])
    official(monkeypatch, aliases=("Ripple Labs",))
    slides = sb.generate(tmp_path, words("Ripple Labs announced payments while Acme denied plans"), [(1, 8)])
    assert len(slides) == 1
    assert slides[0]["source_label"] == "Ripple"


def test_old_official_cache_cannot_bypass_the_new_subject_claim_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = candidate("Acme")
    save_manifest(tmp_path, [item])
    transcript = words("Ripple announced payments while Acme denied plans")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/words.json").write_text(json.dumps(transcript))
    (tmp_path / "assets/old.png").write_bytes(PNG)
    digest = hashlib.sha256(PNG).hexdigest()
    transcript_hash = sb._transcript_hash(transcript)
    slide = {"type": "image", "source_kind": "press", "src": "assets/old.png",
             "title": item["query"], "start": 2.25, "end": 5.75,
             "source_url": item["url"], "source_label": "Acme",
             "provenance": {"verified": True, "asset_sha256": digest,
                            "transcript_sha256": transcript_hash}}
    (tmp_path / sb.PROVENANCE).write_text(json.dumps({
        "version": 1, "transcript_sha256": transcript_hash,
        "assets": [{"candidate": item, "slide": slide, "sha256": digest}],
    }))
    assert sb.filter_slides([slide], tmp_path) == []
    official(monkeypatch, label="Acme", quote="Acme announced payments.")
    assert sb.generate(tmp_path, transcript, [(1, 8)]) == []


def test_cached_official_anchor_is_rechecked_after_gap_retiming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_manifest(tmp_path, [candidate("Acme", start=0, end=10)])
    transcript = words("Ripple announced payments then later Acme announced payments")
    for index, word in enumerate(transcript[-3:]):
        word.update(start=8 + index / 4, end=8.25 + index / 4)
    official(monkeypatch, label="Acme", quote="Acme announced payments.")
    first = sb.generate(tmp_path, transcript, [(4, 10)])
    assert [(slide["start"], slide["end"]) for slide in first] == [(4, 10)]
    monkeypatch.setattr(sb.source_capture, "fetch_public", lambda *_: pytest.fail("Cache needs no HTTP"))
    assert sb.generate(tmp_path, transcript, [(0, 6)]) == []


def test_entity_only_official_image_remains_a_valid_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_manifest(tmp_path, [candidate(kind="images", query="Ripple", quote="Ripple")])
    official(monkeypatch, quote="Ripple")
    assert len(sb.generate(tmp_path, words("We discussed Ripple today"), [(1, 8)])) == 1


def test_final_source_interval_must_still_overlap_its_real_mention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_manifest(tmp_path, [candidate(start=0, end=100)])
    official(monkeypatch)
    assert sb.generate(tmp_path, words("Ripple announced payments"), [(40, 60)]) == []
    assert "overlap" in (tmp_path / sb.REVIEW).read_text()


@pytest.fixture
def render_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "renders").mkdir()
    (tmp_path / "assets/words.json").write_text(json.dumps(words("The payment experiment begins")))
    (tmp_path / "renders/final.mp4").write_bytes(b"base")
    (tmp_path / "meta.json").write_text(json.dumps({"_shortsmith": {"duration": 8}}))
    save_manifest(tmp_path, [])

    def render(project: Path, **_kwargs: object) -> Path:
        out = project / "renders/final_remotion.mp4"
        out.write_bytes(b"render")
        return out

    monkeypatch.setattr(ar.render_remotion, "render", render)
    return tmp_path


def test_discovery_retries_after_a_key_is_added_then_caches_the_complete_result(
    render_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sb.source_discovery, "discover_web", lambda *_: calls.append(True) or [])
    ar.apply_remotion(render_project)
    ar.apply_remotion(render_project)
    assert calls == []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    ar.apply_remotion(render_project)
    assert calls == [True]
    ar.apply_remotion(render_project)
    assert calls == [True]


def test_transient_discovery_failure_retries_without_forcing_the_render(
    render_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def discover(*_args: object) -> list:
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("Provider temporarily unavailable")
        return []

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    monkeypatch.setattr(sb.source_discovery, "discover_web", discover)
    ar.apply_remotion(render_project)
    ar.apply_remotion(render_project)
    assert len(calls) == 2
    ar.apply_remotion(render_project)
    assert len(calls) == 2


def test_offline_discovery_retries_once_back_online(
    render_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    monkeypatch.setenv("SHORTSMITH_BROLL_OFFLINE", "1")
    monkeypatch.setattr(sb.source_discovery, "discover_web", lambda *_: calls.append(True) or [])
    ar.apply_remotion(render_project)
    ar.apply_remotion(render_project)
    assert calls == []
    monkeypatch.delenv("SHORTSMITH_BROLL_OFFLINE")
    ar.apply_remotion(render_project)
    assert calls == [True]
    ar.apply_remotion(render_project)
    assert calls == [True]


def test_legacy_skipped_discovery_retries_after_key_recovery(
    render_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(sb.source_discovery, "discover_web", lambda *_: calls.append(True) or [])
    ar.apply_remotion(render_project)
    # This is the exact review shape written before recovery metadata existed.
    (render_project / sb.REVIEW).write_text(json.dumps({"version": 1, "items": [{
        "stage": "web discovery", "status": "skipped",
        "reason": "ANTHROPIC_API_KEY is not set; explicit items still work",
    }]}))
    ar.apply_remotion(render_project)
    assert calls == []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    ar.apply_remotion(render_project)
    assert calls == [True]


@pytest.mark.parametrize("has_old_review", [False, True])
def test_whole_generation_failure_is_retryable_with_a_missing_or_old_review(
    render_project: Path, monkeypatch: pytest.MonkeyPatch, has_old_review: bool,
) -> None:
    generate = ar.gen_broll.generate
    calls = []

    def unavailable(*args: object, **kwargs: object) -> Path | None:
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("Temporary generation failure before writing the review")
        return generate(*args, **kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    monkeypatch.setattr(sb.source_discovery, "discover_web", lambda *_: [])
    if has_old_review:
        ar.apply_remotion(render_project)
    monkeypatch.setattr(ar.gen_broll, "generate", unavailable)
    ar.apply_remotion(render_project, force=has_old_review)
    assert (render_project / "renders/final_remotion.mp4").exists()
    ar.apply_remotion(render_project)
    assert len(calls) == 2
    ar.apply_remotion(render_project)
    assert len(calls) == 2


def test_unknown_completion_retries_once_but_no_free_gaps_is_a_complete_result(
    render_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    monkeypatch.setattr(sb.source_discovery, "discover_web", lambda *_: [])
    ar.apply_remotion(render_project)
    (render_project / sb.REVIEW).write_text("broken JSON")
    calls = []

    def no_gaps(*_args: object, **_kwargs: object) -> None:
        calls.append(True)
        return None

    monkeypatch.setattr(ar.gen_broll, "generate", no_gaps)
    ar.apply_remotion(render_project)
    assert calls == [True]
    ar.apply_remotion(render_project)
    assert calls == [True]


def test_an_x_retry_reuses_completed_web_search_including_empty_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / sb.MANIFEST).write_text(json.dumps({"enabled": ["press", "tweets"], "items": []}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    web_calls, tweet_calls = [], []

    def tweets(*_args: object) -> list:
        tweet_calls.append(True)
        raise RuntimeError("Grok CLI temporarily unavailable")

    monkeypatch.setattr(sb.source_discovery, "discover_web", lambda *_: web_calls.append(True) or [])
    monkeypatch.setattr(sb.source_discovery, "discover_tweets", tweets)
    transcript = words("The payment experiment begins")
    sb.generate(tmp_path, transcript, [(1, 8)])
    assert sb.should_retry(tmp_path, {"press", "tweets"})
    sb.generate(tmp_path, transcript, [(1, 8)])
    sb.generate(tmp_path, transcript, [(1, 8)])
    assert len(web_calls) == 1
    assert len(tweet_calls) == 3
    # A changed transcript is a new discovery request.
    sb.generate(tmp_path, words("Another payment experiment begins"), [(1, 8)])
    assert len(web_calls) == 2


def test_completed_search_preserves_candidates_and_is_bound_to_stage_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / sb.MANIFEST).write_text(json.dumps({"enabled": ["press", "tweets"], "items": []}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    calls = []

    def discover(_words: list[dict], kinds: frozenset[str]) -> list[dict]:
        calls.append(kinds)
        return [candidate(kind="press" if "press" in kinds else "images")]

    def tweets(*_args: object) -> list:
        raise RuntimeError("X temporarily unavailable")

    monkeypatch.setattr(sb.source_discovery, "discover_web", discover)
    monkeypatch.setattr(sb.source_discovery, "discover_tweets", tweets)
    official(monkeypatch)
    transcript = words("Ripple announced payments")
    assert sb.generate(tmp_path, transcript, [(40, 60)]) == []
    # The first search completed but its candidate had no slot. Preserve it.
    assert len(sb.generate(tmp_path, transcript, [(1, 8)])) == 1
    assert calls == [{"press"}]
    (tmp_path / sb.MANIFEST).write_text(json.dumps({"enabled": ["images", "tweets"], "items": []}))
    assert len(sb.generate(tmp_path, transcript, [(1, 8)])) == 1
    assert calls == [{"press"}, {"images"}]

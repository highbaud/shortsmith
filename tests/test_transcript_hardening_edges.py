"""Edge-case hardening for the transcript path: names -> transcribe -> align -> scaffold.

Every case here is a shape that reached one of those steps and crashed it, or
was written to disk as a value that reads as a timing but is not one.
"""
from __future__ import annotations

import json
import random
import subprocess

import pytest

from shortsmith import align, names, reframe, scaffold, transcribe
from shortsmith.config import Config

# ---------------------------------------------------------------------------
# names.fix_words
# ---------------------------------------------------------------------------


def test_merge_without_any_end_leaves_the_key_absent() -> None:
    """A merged word must never carry `"end": null`, because captions.py does
    `float(w["end"])` on it."""
    fixed = names.fix_words([{"word": "Garling", "start": 1.0},
                             {"word": "house", "start": 1.5}])
    assert len(fixed) == 1
    assert fixed[0]["text"] == "Garlinghouse"
    assert "end" not in fixed[0]
    assert json.dumps(fixed).count("null") == 0


def test_merge_keeps_the_first_end_when_only_the_second_is_missing() -> None:
    fixed = names.fix_words([{"word": "Garling", "start": 1.0, "end": 1.4},
                             {"word": "house", "start": 1.5}])
    assert fixed[0]["end"] == 1.4


def test_merge_takes_the_second_end_when_the_first_has_none() -> None:
    fixed = names.fix_words([{"word": "Garling", "start": 1.0},
                             {"word": "house", "start": 1.5, "end": 1.9}])
    assert fixed[0]["end"] == 1.9


def test_empty_word_list_is_returned_unchanged() -> None:
    assert names.fix_words([]) == []


def test_word_without_text_or_timings_survives() -> None:
    assert names.fix_words([{}]) == [{}]


_TOKENS = (
    "ginsler", "Ginsler's,", "sailor", "Sailor", "michael", "brad", "garling",
    "house", "House", "mc", "caleb", "jed", "bet", "david", "patrick", "xrpl",
    "hbar", "the", "", "   ", "...", '"quoted"', "(paren)", "Diamond", "jamie",
    "sheriff", "peter", "larson", "chris", "McCaleb", "Garling",
)


def test_fix_words_holds_its_invariants_over_random_word_lists() -> None:
    rng = random.Random(20260903)
    for _ in range(4000):
        source = [
            {"text": rng.choice(_TOKENS), "start": round(i * 0.5, 3),
             "end": round(i * 0.5 + 0.4, 3)}
            for i in range(rng.randint(0, 6))
        ]
        snapshot = json.dumps(source)
        fixed = names.fix_words(source)

        assert json.dumps(source) == snapshot, "input was mutated"
        assert len(fixed) <= len(source), "a correction added a word"
        for word in fixed:
            assert word["start"] <= word["end"], f"inverted timing in {word}"
        blank_in = sum(1 for w in source if not names.word_text(w).strip())
        blank_out = sum(1 for w in fixed if not names.word_text(w).strip())
        assert blank_out <= blank_in, "a correction emptied a word"
        assert names.fix_words(fixed) == fixed, "correcting twice is not stable"


# ---------------------------------------------------------------------------
# transcribe: reusing a sibling transcript
# ---------------------------------------------------------------------------


def test_reuse_picks_the_longest_matching_transcript_tag(tmp_path) -> None:
    """Two videos in one folder: the short slug is a substring of the long one,
    so both transcripts match. The specific one has to win, whatever order the
    directory lists them in."""
    video = tmp_path / "xrp-deaton-interview.mp4"
    video.write_bytes(b"")
    (tmp_path / "transcript-xrp.json").write_text(
        json.dumps([{"text": "wrong", "start": 0.0, "end": 0.4}]), encoding="utf-8"
    )
    (tmp_path / "transcript-xrp-deaton.json").write_text(
        json.dumps([{"text": "right", "start": 0.0, "end": 0.4}]), encoding="utf-8"
    )

    words = transcribe.transcribe(video, tmp_path / "out" / "transcript.json", Config())

    assert [w["text"] for w in words] == ["right"]


def test_reuse_ignores_a_transcript_for_another_video(tmp_path) -> None:
    video = tmp_path / "solana-panel.mp4"
    video.write_bytes(b"")
    (tmp_path / "transcript-xrp.json").write_text("[]", encoding="utf-8")

    # No match means it would run Whisper; assert it got that far instead of
    # loading the wrong file.
    with pytest.raises((RuntimeError, ImportError, ModuleNotFoundError)):
        transcribe.transcribe(video, tmp_path / "out" / "transcript.json", Config())


# ---------------------------------------------------------------------------
# align: resolving the clip a manifest points at
# ---------------------------------------------------------------------------


def test_align_falls_back_to_raw_path_when_earlier_steps_were_skipped(
    tmp_path, monkeypatch
) -> None:
    """`--from-step 6` loads manifests that steps 4 and 5 never wrote to, so
    raw_path is the only path on them."""
    clip = tmp_path / "short-01.mp4"
    clip.write_bytes(b"")
    seen: list[str] = []

    def fake_transcribe(src, out, cfg, *, reuse_existing=True):
        seen.append(str(src))
        out.write_text("[]", encoding="utf-8")
        return []

    monkeypatch.setattr(align.transcribe, "transcribe", fake_transcribe)
    cfg = Config()
    cfg.align_engine = "faster-whisper"

    manifests = align.align_all([{"rank": 1, "raw_path": str(clip)}], cfg)

    assert seen == [str(clip)]
    assert manifests[0]["words_path"] == str(clip.with_suffix(".words.json"))


def test_align_respells_names_in_the_alignment_it_wrote(tmp_path, monkeypatch) -> None:
    clip = tmp_path / "short-01.mp4"
    clip.write_bytes(b"")
    words_out = clip.with_suffix(".words.json")

    def fake_transcribe(src, out, cfg, *, reuse_existing=True):
        out.write_text(json.dumps(
            [{"text": "michael", "start": 0.0, "end": 0.4},
             {"text": "sailor", "start": 0.5, "end": 0.9}]
        ), encoding="utf-8")
        return []

    monkeypatch.setattr(align.transcribe, "transcribe", fake_transcribe)
    cfg = Config()
    cfg.align_engine = "faster-whisper"

    align.align_all([{"rank": 1, "raw_path": str(clip)}], cfg)

    written = json.loads(words_out.read_text(encoding="utf-8"))
    assert [w["text"] for w in written] == ["michael", "Saylor"]


# ---------------------------------------------------------------------------
# scaffold: manifests and clip specs that arrive malformed
# ---------------------------------------------------------------------------


def test_missing_words_path_yields_no_words_instead_of_reading_the_cwd() -> None:
    assert scaffold._load_clip_words({}, 1) == []
    assert scaffold._load_clip_words({"words_path": ""}, 1) == []
    assert scaffold._load_clip_words({"words_path": None}, 1) == []


def test_words_path_that_is_a_directory_yields_no_words(tmp_path) -> None:
    assert scaffold._load_clip_words({"words_path": str(tmp_path)}, 1) == []


def test_words_path_is_read_when_it_points_at_a_file(tmp_path) -> None:
    path = tmp_path / "short-01.words.json"
    path.write_text(json.dumps([{"text": "hi", "start": 0.0, "end": 0.3}]),
                    encoding="utf-8")
    assert scaffold._load_clip_words({"words_path": str(path)}, 1) == [
        {"text": "hi", "start": 0.0, "end": 0.3}
    ]


@pytest.mark.parametrize("hook", [
    "Don't be exit liquidity.",          # a bare string, not an object
    ["Don't be exit liquidity."],        # a list
    42,
])
def test_a_hook_that_is_not_an_object_is_skipped(hook) -> None:
    assert scaffold._build_hook({"hook": hook}, 30.0) is None


@pytest.mark.parametrize("duration", [None, "soon", {}, []])
def test_a_hook_duration_that_is_not_a_number_falls_back_to_the_default(duration) -> None:
    built = scaffold._build_hook(
        {"hook": {"text": "Don't be exit liquidity.", "duration": duration}}, 30.0
    )
    assert built is not None
    assert built["duration"] == pytest.approx(2.6)


def test_accent_entries_that_are_not_strings_do_not_crash() -> None:
    cfg = Config()
    out = scaffold._build_callouts(
        {"callouts": [{"local_start": 1, "text": "800 T", "accent": [800]}]},
        1, 30.0, cfg,
    )
    assert 'class="em-gold"' in out[0]["html"]

    hook = scaffold._build_hook(
        {"hook": {"text": "800 T", "accent": [800], "color": "gold"}}, 30.0
    )
    assert hook is not None
    assert '<span class="em-gold">800</span>' in hook["html"]


# ---------------------------------------------------------------------------
# reframe: why a reframe failed has to reach the log
# ---------------------------------------------------------------------------


def test_ffmpeg_reason_carries_the_captured_stderr() -> None:
    err = subprocess.CalledProcessError(
        1, ["ffmpeg"], output=b"", stderr=b"Invalid too big or non positive size"
    )
    reason = reframe._ffmpeg_reason(err)
    assert "Invalid too big or non positive size" in reason

    text_err = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="No such file")
    assert "No such file" in reframe._ffmpeg_reason(text_err)


def test_ffmpeg_reason_of_an_ordinary_exception_is_its_message() -> None:
    assert reframe._ffmpeg_reason(RuntimeError("Cannot open clip")) == "Cannot open clip"

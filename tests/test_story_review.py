"""Public contract tests for source-grounded story review, without a provider."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from shortsmith.story_review import build_review_input, source_digest, validate_reviews


def transcript(text: str) -> list[dict]:
    return [
        {"text": token, "start": float(index), "end": index + 0.5}
        for index, token in enumerate(text.split())
    ]


def test_payload_has_full_source_and_playback_order_without_generated_claims() -> None:
    words = transcript("A plan. That works. More context. The end.")
    clips = [
        {
            "segments": [[6, 7.5], [2, 3.5]],
            "viral_score": 10,
            "hook_text": "Invented title",
            "reasoning": "Invented proof",
        }
    ]
    before = deepcopy((clips, words))

    payload = json.loads(build_review_input(clips, words))

    assert payload["source_sentences"] == [
        {"sentence_id": 0, "start": 0.0, "end": 1.5, "text": "A plan."},
        {"sentence_id": 1, "start": 2.0, "end": 3.5, "text": "That works."},
        {"sentence_id": 2, "start": 4.0, "end": 5.5, "text": "More context."},
        {"sentence_id": 3, "start": 6.0, "end": 7.5, "text": "The end."},
    ]
    assert payload["clips"][0]["clip_id"] == 0
    assert [segment["text"] for segment in payload["clips"][0]["segments"]] == [
        "The end.",
        "That works.",
    ]
    assert "Invented" not in json.dumps(payload)
    assert "viral_score" not in json.dumps(payload)
    assert (clips, words) == before


def issue(**changes: object) -> dict:
    return {
        "type": "missing_setup",
        "explanation": "The pronoun needs its omitted subject.",
        "segment_id": 0,
        "selected_quote": "That works",
        "context_quote": "A plan",
        "context_sentence_id": 0,
        **changes,
    }


def review(decision: str = "reject", issues: list | None = None, **changes: object) -> dict:
    return {
        "clip_id": 0,
        "decision": decision,
        "reason": "Check the complete story.",
        "issues": [issue()] if issues is None else issues,
        **changes,
    }


def test_real_local_evidence_is_normalized_with_source_times_without_mutation() -> None:
    words = transcript("A plan. That works. It needs care.")
    clips = [{"segments": [[2, 3.5]], "hook_text": "Made up"}]
    raw = [review()]
    before = deepcopy((clips, words, raw))

    report = validate_reviews(raw, clips, words)[0]

    assert report["decision"] == "reject"
    assert report["validation_errors"] == []
    assert report["issues"] == [
        {
            **issue(),
            "selected_start": 2.0,
            "selected_end": 3.5,
            "context_start": 0.0,
            "context_end": 1.5,
            "side": "before",
        }
    ]
    assert (clips, words, raw) == before


@pytest.mark.parametrize(
    "kind, words, selected",
    [
        ("missing_setup", "That works.", "That works"),
        ("weak_ending", "The answer is", "answer is"),
    ],
)
def test_recording_boundary_can_prove_incomplete_story_without_omitted_context(
    kind: str,
    words: str,
    selected: str,
) -> None:
    source = transcript(words)
    clips = [{"segments": [[0, source[-1]["end"]]]}]
    raw = [
        review(
            issues=[
                issue(
                    type=kind,
                    selected_quote=selected,
                    context_quote="",
                    context_sentence_id=None,
                )
            ]
        )
    ]

    report = validate_reviews(raw, clips, source)[0]

    assert report["decision"] == "reject"
    assert report["issues"][0]["context_quote"] == ""
    assert report["issues"][0]["context_start"] is None


def test_digest_tracks_spoken_text_and_timing_in_source_order() -> None:
    source = [{"text": "Hi.", "start": 0, "end": 0.5, "confidence": 0.9}]
    alias = [{"word": "Hi.", "start": "0.0", "end": "0.5"}]

    digest = source_digest(source)

    assert len(digest) == 64
    assert digest == source_digest(alias)
    assert digest != source_digest([{**source[0], "text": "Bye."}])
    assert digest != source_digest([{**source[0], "end": 0.6}])


@pytest.mark.parametrize(
    "change, error",
    [
        ({"selected_quote": "hat works"}, "selected_quote"),
        ({"selected_quote": "that works"}, "selected_quote"),
        ({"selected_quote": "Made up"}, "selected_quote"),
        ({"selected_quote": "That works. It"}, "selected_quote"),
        ({"context_quote": "plan. That", "context_sentence_id": 0}, "context_quote"),
        ({"context_quote": "That works", "context_sentence_id": 1}, "context_quote"),
        ({"context_sentence_id": 99}, "context_quote"),
        ({"context_sentence_id": True}, "context_quote"),
        ({"segment_id": True}, "segment_id"),
        ({"segment_id": 8}, "segment_id"),
        ({"type": "other"}, "type"),
        ({"explanation": ""}, "explanation"),
        ({"context_start": 1.0}, "context_start"),
        ({"selected_end": float("nan")}, "selected_end"),
        ({"suggestion": []}, "suggestion"),
    ],
)
def test_unproved_issue_never_rejects(change: dict, error: str) -> None:
    words = transcript("A plan. That works. It needs care.")

    report = validate_reviews(
        [review(issues=[issue(**change)])], [{"segments": [[2, 3.5]]}], words
    )[0]

    assert report["decision"] == "needs_review"
    assert any(error in message for message in report["validation_errors"])


def test_distant_context_and_retained_context_cannot_prove_rejection() -> None:
    words = transcript("A plan. Unrelated thought. That works. It needs care.")
    raw = [review(issues=[issue(context_quote="A plan", context_sentence_id=0)])]

    distant = validate_reviews(raw, [{"segments": [[4, 5.5]]}], words)[0]
    retained = validate_reviews(raw, [{"segments": [[4, 5.5], [0, 1.5]]}], words)[0]

    assert distant["decision"] == retained["decision"] == "needs_review"


def test_a_quote_across_reordered_seams_is_not_original_speech() -> None:
    words = transcript("A plan. That works. More context. The end.")
    clips = [{"segments": [[6, 7.5], [2, 3.5]]}]
    raw = [
        review(
            issues=[
                issue(
                    selected_quote="The end. That works",
                    context_quote="More context",
                    context_sentence_id=2,
                )
            ]
        )
    ]

    assert validate_reviews(raw, clips, words)[0]["decision"] == "needs_review"


@pytest.mark.parametrize(
    "raw, error",
    [
        ([], "missing"),
        ([review(), review()], "duplicate"),
        ([review("pass", []), review(clip_id=4)], "unknown"),
        ([review("pass", []), None], "clip_id"),
        ([review(clip_id=True)], "clip_id"),
        ([review(clip_id="0")], "clip_id"),
        ({"clip_id": 0}, "list"),
        ([review("pass", [issue()])], "unresolved"),
        ([review("reject", [])], "requires"),
        ([review("pass", [], reason="")], "reason"),
        ([{**review("pass", []), "issues": None}], "list"),
        ([review("maybe", [])], "decision"),
    ],
)
def test_malformed_or_unresolved_review_is_explicitly_needs_review(raw: object, error: str) -> None:
    report = validate_reviews(raw, [{"segments": [[2, 3.5]]}], transcript("A plan. That works."))[0]

    assert report["decision"] == "needs_review"
    assert any(error in message for message in report["validation_errors"])


def test_pass_requires_one_valid_review_with_no_issues() -> None:
    clips = [{"start": 0, "end": 1.5}]
    source = transcript("Complete thought.")

    reports = validate_reviews([review("pass", [])], clips, source)

    assert reports == [
        {
            "clip_id": 0,
            "decision": "pass",
            "reason": "Check the complete story.",
            "issues": [],
            "validation_errors": [],
        }
    ]


@pytest.mark.parametrize("kind", ["missing_setup", "weak_ending", "changed_meaning"])
def test_empty_context_in_middle_of_source_cannot_prove_rejection(kind: str) -> None:
    source = transcript("A plan. That works. It needs care.")
    raw = [review(issues=[issue(type=kind, context_quote="", context_sentence_id=None)])]

    report = validate_reviews(raw, [{"segments": [[2, 3.5]]}], source)[0]

    assert report["decision"] == "needs_review"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        False,
        {},
        {"segments": []},
        {"segments": "0,2"},
        {"segments": [[0]]},
        {"segments": [[True, 2]]},
        {"segments": [[0, float("nan")]]},
        {"segments": [[0, float("inf")]]},
        {"segments": [[-1, 2]]},
        {"segments": [[2, 1]]},
        {"segments": [[9, 10]]},
        {"start": False, "end": 2, "segments": [[0, 1.5]]},
    ],
)
def test_bad_clip_input_is_never_approved_or_silently_dropped(bad: object) -> None:
    source = transcript("Complete thought.")

    with pytest.raises(ValueError, match="clip 0"):
        build_review_input([bad], source)
    reports = validate_reviews([review("pass", [])], [bad], source)

    assert len(reports) == 1
    assert reports[0]["decision"] == "needs_review"
    assert any("invalid clip" in message for message in reports[0]["validation_errors"])


@pytest.mark.parametrize(
    "source",
    [
        None,
        [None],
        [{}],
        [{"text": []}],
        [{"text": "Hi", "start": True, "end": 1}],
        [{"text": "Hi", "start": 0, "end": "nan"}],
        [{"text": "Hi", "start": 0, "end": "infinity"}],
        [{"text": "Hi", "start": 2, "end": 1}],
        [{"text": "Hi", "start": "no", "end": 1}],
        [{"text": "Hi", "start": 2, "end": 3}, {"text": "there", "start": 1, "end": 2}],
    ],
)
def test_bad_source_input_is_explicitly_unreviewable(source: object) -> None:
    clips = [{"segments": [[0, 4]]}]

    with pytest.raises(ValueError, match="source"):
        build_review_input(clips, source)
    report = validate_reviews([review("pass", [])], clips, source)[0]

    assert report["decision"] == "needs_review"
    assert any("invalid source transcript" in message for message in report["validation_errors"])


def test_partial_boundary_speech_cannot_be_called_omitted() -> None:
    source = transcript("A plan. That works. More context.")
    clips = [{"segments": [[1.25, 3.5]]}]

    payload = json.loads(build_review_input(clips, source))
    report = validate_reviews([review()], clips, source)[0]

    assert payload["clips"][0]["segments"][0]["partial_boundary_words"] == [
        {"text": "plan.", "start": 1.0, "end": 1.5},
    ]
    assert report["decision"] == "needs_review"


def test_changed_meaning_requires_the_omitted_qualification() -> None:
    source = transcript("In a simulation. This always works. Real tests failed.")
    raw = [
        review(
            issues=[
                issue(
                    type="changed_meaning",
                    explanation="The retained claim applies only to a simulation.",
                    selected_quote="This always works",
                    context_quote="In a simulation",
                    context_sentence_id=0,
                )
            ]
        )
    ]

    report = validate_reviews(raw, [{"segments": [[3, 5.5]]}], source)[0]

    assert report["decision"] == "reject"
    assert report["issues"][0]["context_end"] == 2.5


def test_missing_payoff_uses_the_omitted_next_sentence() -> None:
    source = transcript("Why did it fail? Here is why. The valve broke.")
    raw = [
        review(
            issues=[
                issue(
                    type="weak_ending",
                    explanation="The clip promises an answer but cuts before it.",
                    selected_quote="Here is why",
                    context_quote="The valve broke",
                    context_sentence_id=2,
                )
            ]
        )
    ]

    report = validate_reviews(raw, [{"segments": [[0, 6.5]]}], source)[0]

    assert report["decision"] == "reject"
    assert report["issues"][0]["side"] == "after"


def test_quoted_evidence_must_relate_to_a_boundary_sentence_of_its_segment() -> None:
    source = transcript("A plan. That works. Completely unrelated subject.")
    raw = [review(issues=[issue(selected_quote="unrelated subject")])]

    report = validate_reviews(raw, [{"segments": [[2, 6.5]]}], source)[0]

    assert report["decision"] == "needs_review"


def test_no_clips_needs_no_transcript_and_drops_nothing() -> None:
    assert validate_reviews([], [], []) == []
    assert json.loads(build_review_input([], [])) == {"source_sentences": [], "clips": []}

"""Shared pipeline dictionary shapes; these annotations do not filter JSON data."""
from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

# JSON segments stay two-item lists in source playback order.
type Segment = list[float]


class TranscriptWord(TypedDict):
    """Timed speech uses text, or the supported legacy word key."""

    start: float
    end: float
    text: NotRequired[str]
    word: NotRequired[str]


class CaptionBand(TypedDict):
    top: float
    bottom: float


class ClipCallout(TypedDict):
    local_start: float
    text: str
    duration: NotRequired[float]
    style: NotRequired[str]
    subline: NotRequired[str]
    accent: NotRequired[list[str]]
    eyebrow: NotRequired[str]
    color: NotRequired[str]


class ClipHook(TypedDict):
    text: str
    overline: NotRequired[str]
    accent: NotRequired[list[str] | str]
    color: NotRequired[str]
    duration: NotRequired[float]


class ReviewIssue(TypedDict):
    type: str
    explanation: str
    segment_id: int
    selected_quote: str
    selected_start: float
    selected_end: float
    context_quote: str
    context_sentence_id: int | None
    context_start: float | None
    context_end: float | None
    side: str
    suggestion: NotRequired[str]


class StoryReview(TypedDict):
    clip_id: int
    decision: str
    reason: str
    issues: list[ReviewIssue]
    validation_errors: list[str]
    reviewed_segments: NotRequired[list[Segment]]
    reviewed_seam_xfade_seconds: NotRequired[float]
    reviewed_speech_segments: NotRequired[list[Segment]]
    retained_speech_digest: NotRequired[str]
    source_digest: NotRequired[str]
    cut_segment_durations: NotRequired[list[float]]


class ClipSpec(TypedDict):
    """Clip-selection fields shared by the cutter, story gate, and scaffold."""

    rank: int
    start: float
    end: float
    segments: list[Segment]
    slug: str
    hook_start: NotRequired[float]
    hook_end: NotRequired[float]
    hook_text: NotRequired[str]
    viral_score: NotRequired[int]
    reasoning: NotRequired[str]
    instagram_caption: NotRequired[str]
    callouts: NotRequired[list[ClipCallout]]
    hook: NotRequired[ClipHook | None]
    story_review: NotRequired[StoryReview]
    caption_band: NotRequired[CaptionBand]
    layout: NotRequired[str]
    layout_preset: NotRequired[str]
    speakers: NotRequired[list[str]]


class ClipManifest(TypedDict):
    """Resume input needs rank/raw_path; later steps add their available output."""

    rank: int
    raw_path: str
    slug: NotRequired[str]
    requested_segments: NotRequired[list[Segment]]
    snapped_segments: NotRequired[list[Segment]]
    duration: NotRequired[float]
    segment_durations: NotRequired[list[float]]
    seam_xfade_seconds: NotRequired[float]
    story_review: NotRequired[StoryReview]
    cleaned_path: NotRequired[str]
    clean_cuts: NotRequired[list[tuple[float, float]] | list[Segment]]
    words_after_clean: NotRequired[list[TranscriptWord]]
    enhanced_path: NotRequired[str]
    words_path: NotRequired[str]
    alignment_failed: NotRequired[bool]
    vertical_path: NotRequired[str]
    caption_band: NotRequired[CaptionBand]
    layout: NotRequired[str]
    layout_preset: NotRequired[str]
    multicam: NotRequired[bool]


class CleaningPlan(TypedDict):
    local_words: list[TranscriptWord]
    cuts: list[tuple[float, float]]
    retained_segments: list[Segment]


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def require_words(raw: object, source: object) -> list[TranscriptWord]:
    """Persisted words after checking the list root and each start/end; ValueError otherwise."""
    if not isinstance(raw, list):
        raise ValueError(f"Invalid words in {source}: expected a list")
    for index, word in enumerate(raw):
        if not isinstance(word, dict) or not (_is_number(word.get("start")) and _is_number(word.get("end"))):
            raise ValueError(f"Invalid words in {source}: entry {index} needs numeric start and end")
    return raw


def require_clips(raw: object, source: object) -> list[ClipSpec]:
    """Persisted clips after checking the list root and each integer rank.

    Rank is the one key every reader indexes by. Resumed steps and render
    inputs read only the fields their stage needs, so the rest are not required.
    """
    if not isinstance(raw, list):
        raise ValueError(f"Invalid clips in {source}: expected a list")
    for index, clip in enumerate(raw):
        rank = clip.get("rank") if isinstance(clip, dict) else None
        if not isinstance(rank, int) or isinstance(rank, bool):
            raise ValueError(f"Invalid clips in {source}: entry {index} needs an integer rank")
    return raw


def current_media(manifest: ClipManifest) -> Path:
    """Media before reframing: enhanced output, cleaned output, or the raw cut."""
    return Path(manifest.get("enhanced_path") or manifest.get("cleaned_path")
                or manifest["raw_path"])

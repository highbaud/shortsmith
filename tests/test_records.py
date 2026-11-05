"""Boundary validators for persisted words and clips reject wrong JSON shapes."""
from __future__ import annotations

import pytest

from shortsmith.records import require_clips, require_words

CLIP = {"rank": 1, "start": 0.0, "end": 5.0, "segments": [[0.0, 5.0]], "slug": "intro"}


def test_valid_words_and_clips_pass_through_unchanged() -> None:
    words = [{"text": "Hi", "start": 0, "end": 0.4}]
    assert require_words(words, "words.json") is words
    assert require_clips([CLIP], "clips.json") == [CLIP]


@pytest.mark.parametrize("raw", [{}, "words", [1], [{"text": "Hi", "start": "0", "end": 1}],
                                 [{"text": "Hi", "start": True, "end": 1}]])
def test_malformed_words_raise_with_the_source(raw: object) -> None:
    with pytest.raises(ValueError, match="words.json"):
        require_words(raw, "words.json")


@pytest.mark.parametrize("raw", [{}, [None], [{**CLIP, "rank": "1"}], [{**CLIP, "rank": True}],
                                 [{k: v for k, v in CLIP.items() if k != "rank"}]])
def test_malformed_clips_raise_with_the_source(raw: object) -> None:
    with pytest.raises(ValueError, match="clips.json"):
        require_clips(raw, "clips.json")

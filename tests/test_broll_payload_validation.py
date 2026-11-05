"""A slide that would crash the Remotion render must be dropped before it ships.

Two shapes reached the composition intact and took the whole render down with an
exit 1, losing every cutaway in the short rather than the one bad slide: a `list`
with no `items` (`.map` of undefined) and a `logo`/`person` with no `src`
(`staticFile(undefined)`). Python only ever checked `start` and `end`. Slides are
LLM-written or hand-authored, so the payload was never guaranteed.

remotion/src/Short.tsx drops the same shapes as a second line of defense. These
tests pin the Python side, where the operator actually sees the message.
"""
from __future__ import annotations

import pytest

from shortsmith.rendering import render_remotion

DURATION = 30.0


def _validate(slide: dict) -> list[dict]:
    return render_remotion._validate_broll([slide], [], DURATION)


def _slide(**over) -> dict:
    return {"type": "text", "start": 1.0, "end": 3.0, "title": "Fine", **over}


@pytest.mark.parametrize("slide,missing", [
    ({"type": "list", "items": []}, "items"),
    ({"type": "list"}, "items"),
    ({"type": "list", "items": "Multifamily"}, "items"),
    ({"type": "logo", "brand": "Ripple"}, "src"),
    ({"type": "person", "person": "Jed McCaleb"}, "src"),
    ({"type": "text"}, "title"),
    ({"type": "stat"}, "value"),
])
def test_a_slide_that_would_crash_the_render_is_dropped(slide, missing, capsys):
    assert _validate(_slide(**slide, title=slide.get("title"))) == []
    assert missing in capsys.readouterr().out


def test_a_logo_in_badge_mode_still_needs_src():
    """The staticFile throw happens before the badge branch, so badges crash too."""
    assert _validate(_slide(type="logo", mode="badge", brand="Ripple")) == []


@pytest.mark.parametrize("slide", [
    {"type": "text", "title": "Assets first."},
    {"type": "list", "items": ["Multifamily", "Not a Lambo"]},
    {"type": "logo", "src": "logos/ripple.png"},
    {"type": "person", "src": "people/mccaleb.jpg"},
    {"type": "stat", "value": "$2B"},
    {"type": "stat", "to": 70},
])
def test_a_renderable_slide_survives(slide):
    kept = _validate({**slide, "start": 1.0, "end": 3.0})
    assert kept == [{**slide, "start": 1.0, "end": 3.0}]


def test_a_stat_counting_to_zero_is_kept():
    """`to: 0` is falsy but renders. Guarding on truthiness alone would drop it."""
    assert _validate({"type": "stat", "to": 0, "start": 1.0, "end": 3.0}) != []


def test_an_unknown_slide_type_is_dropped(capsys):
    assert _validate(_slide(type="carousel")) == []
    assert "not a slide type" in capsys.readouterr().out


class TestGenerationTimeIdentity:
    """The other half of the contract, one stage earlier.

    `_resolve_assets` looks a slide up by `brand`/`person` (falling back to
    `name`). A slide carrying neither resolves to "" and is dropped much later
    with `no verified photo for ''`, which reads as a missing photo rather than
    a missing name. `src` cannot be required here because it does not exist
    until the asset is fetched.
    """

    @staticmethod
    def _norm(slide: dict) -> list[dict]:
        from shortsmith.rendering import gen_broll
        return gen_broll._normalize([{"start": 1.0, "end": 3.0, **slide}], [(0.0, 30.0)])

    @pytest.mark.parametrize("slide,missing", [
        ({"type": "person", "role": "CEO, Ripple"}, "person or name"),
        ({"type": "logo", "mode": "badge"}, "brand or name"),
        ({"type": "text", "eyebrow": "The rule"}, "title"),
        ({"type": "list", "title": "Where it went"}, "items"),
        ({"type": "list", "title": "x", "items": []}, "items"),
    ])
    def test_a_slide_that_cannot_resolve_is_dropped(self, slide, missing, capsys):
        assert self._norm(slide) == []
        assert missing in capsys.readouterr().out

    @pytest.mark.parametrize("slide", [
        {"type": "person", "person": "Jed McCaleb"},
        {"type": "person", "name": "Jed McCaleb"},
        {"type": "logo", "brand": "Ripple"},
        {"type": "logo", "name": "Ripple"},
        {"type": "text", "title": "Assets first."},
        {"type": "list", "items": ["Multifamily"]},
    ])
    def test_a_resolvable_slide_survives(self, slide):
        assert len(self._norm(slide)) == 1

    def test_src_is_not_required_before_the_asset_is_fetched(self):
        """Requiring `src` here would drop every valid auto slide."""
        assert len(self._norm({"type": "person", "person": "Chris Larsen"})) == 1


def test_a_string_rank_from_the_model_does_not_lose_the_batch():
    """The sort using `rank` sits outside the per-clip try, so one string rank
    raised there and lost every clip in the batch, not just the bad one.

    `rank` is only reached when `viral_score` ties, because the sort key is a
    tuple and comparison stops at the first differing element. Equal scores here
    are what make this test exercise the bug at all.
    """
    from shortsmith.find_clips import _common
    words = [{"text": "w", "start": 0.0, "end": 60.0}]
    clips = [
        {"start": 0.0, "end": 20.0, "viral_score": 8, "rank": "2", "hook_text": "a"},
        {"start": 20.0, "end": 40.0, "viral_score": 8, "rank": 1, "hook_text": "b"},
        {"start": 40.0, "end": 55.0, "viral_score": 8, "rank": None, "hook_text": "c"},
    ]
    out = _common.normalize_clips(clips, words)
    assert len(out) == 3
    assert [c["rank"] for c in out] == [1, 2, 3]


def test_rank_still_orders_a_tie():
    """Coercing must not flatten the tiebreak it exists for."""
    from shortsmith.find_clips import _common
    words = [{"text": "w", "start": 0.0, "end": 60.0}]
    clips = [
        {"start": 0.0, "end": 20.0, "viral_score": 8, "rank": 9, "hook_text": "late"},
        {"start": 20.0, "end": 40.0, "viral_score": 8, "rank": "1", "hook_text": "early"},
    ]
    out = _common.normalize_clips(clips, words)
    assert [c["hook_text"] for c in out] == ["early", "late"]


@pytest.mark.parametrize("bad_callouts", [None, "manual callout", {}, [None, "bad", 4]])
def test_bad_optional_callouts_do_not_drop_the_clip(bad_callouts):
    from shortsmith.find_clips import _common

    clips = [{"start": 0, "end": 10, "callouts": bad_callouts}]
    words = [{"text": "w", "start": 0.0, "end": 10.0}]
    out = _common.normalize_clips(clips, words)
    assert len(out) == 1
    assert out[0]["callouts"] == []


def test_bad_callout_keeps_valid_neighbors_and_reports_the_error(caplog):
    from shortsmith.find_clips import _common

    valid = {"local_start": 2, "text": "$1M", "style": "bigstat"}
    clips = [{"start": 0, "end": 10, "callouts": [None, valid, {"text": "bad"}]}]
    words = [{"text": "w", "start": 0.0, "end": 10.0}]
    out = _common.normalize_clips(clips, words)
    assert len(out) == 1
    assert [c["text"] for c in out[0]["callouts"]] == ["$1M"]
    assert "expected an object" in caplog.text
    assert "local_start" in caplog.text


@pytest.mark.parametrize("field", ["local_start", "duration"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_callouts_are_dropped_at_normalization(field, value, caplog):
    from shortsmith.find_clips import _common

    callout = {"local_start": 2, "duration": 1, "text": "bad", field: value}
    clips = [{"start": 0, "end": 10, "callouts": [callout]}]
    out = _common.normalize_clips(clips, [{"text": "w", "start": 0.0, "end": 10.0}])
    assert len(out) == 1
    assert out[0]["callouts"] == []
    assert "timing must be finite" in caplog.text

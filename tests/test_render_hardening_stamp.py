"""Regression tests for render-stamp correctness.

Each one pins a way the stamp used to be wrong: a stale video shipping because
an input change was invisible to the digest, or a re-render being skipped on a
record we cannot actually read.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import apply_remotion as ar  # noqa: E402
import gen_broll  # noqa: E402
import render_remotion  # noqa: E402
import render_stamp  # noqa: E402


@pytest.fixture
def short(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    src = tmp_path / "src"
    proj = src / "short-01-topic"
    (proj / "assets").mkdir(parents=True)
    (proj / "renders").mkdir()
    (proj / "assets" / "words.json").write_text("[]", encoding="utf-8")
    (src / "_clips.json").write_text(json.dumps([{"rank": 1}]), encoding="utf-8")
    base = proj / "renders" / "final.mp4"
    base.write_bytes(b"A" * 4096)

    rendered: list[dict] = []

    def fake_render(project_dir: Path, **kwargs) -> Path:
        out = project_dir / "renders" / "final_remotion.mp4"
        out.write_bytes(b"mp4")
        rendered.append(kwargs)
        return out

    monkeypatch.setattr(render_remotion, "_hyperframes_renders", lambda p: [base])
    monkeypatch.setattr(render_remotion, "render", fake_render)
    monkeypatch.setattr(gen_broll, "generate", lambda *a, **k: None)
    people = tmp_path / "people"
    people.mkdir()
    monkeypatch.setattr(gen_broll, "PEOPLE_DIR", people)
    monkeypatch.setattr(gen_broll, "PEOPLE_MANIFEST", people / "people.json")
    monkeypatch.setattr(gen_broll, "MANUAL_DIR", people / "manual")
    monkeypatch.setattr(render_stamp, "code_files", lambda root=None: [])
    ar.reset_stats()
    return {"proj": proj, "base": base, "rendered": rendered}


def stamp_of(short: dict) -> dict:
    return render_stamp.compute_stamp(short["proj"], base=short["base"],
                                      style="xrp-revolution", platform="generic",
                                      captions=True)


def test_a_same_size_base_swapped_within_one_second_changes_the_stamp(short: dict) -> None:
    """int(st_mtime) rounded the base's mtime to a whole second, so a same-size
    base replaced inside that second read as unchanged and shipped stale."""
    before = stamp_of(short)
    st = short["base"].stat()
    short["base"].write_bytes(b"B" * 4096)
    # Same whole second, 1 ms later: only sub-second resolution can see it.
    os.utime(short["base"], ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert short["base"].stat().st_size == st.st_size
    assert int(short["base"].stat().st_mtime) == int(st.st_mtime)
    assert render_stamp.changed_inputs(before, stamp_of(short)) == ["base"]


def test_a_stamp_from_an_older_version_is_not_current(short: dict) -> None:
    """Bumping STAMP_VERSION exists to force a re-render; a digest match under
    the old version must not defeat it."""
    out = short["proj"] / "renders" / "final_remotion.mp4"
    out.write_bytes(b"mp4")
    current = stamp_of(short)
    render_stamp.write_stamp(short["proj"], {**current, "version": current["version"] - 1})
    assert not render_stamp.is_current(short["proj"], current, out)


def test_an_unreadable_stamp_re_renders_instead_of_falling_back_to_mtimes(
    short: dict,
) -> None:
    """A corrupt stamp is not the same as no stamp: the short WAS stamped, so
    the weaker legacy mtime rule must not rescue it."""
    ar.apply_remotion(short["proj"])
    assert len(short["rendered"]) == 1
    render_stamp.stamp_path(short["proj"]).write_text("{ truncated", encoding="utf-8")
    assert render_stamp.has_stamp(short["proj"])
    assert render_stamp.read_stamp(short["proj"]) is None

    ar.apply_remotion(short["proj"])
    assert len(short["rendered"]) == 2
    assert ar.RUN_STATS["legacy_skipped"] == 0


def test_a_short_that_was_never_stamped_is_still_legacy(short: dict) -> None:
    """The counterpart: no stamp file at all keeps the old mtime rule, so an
    unscoped finalize does not rebuild the whole library."""
    (short["proj"] / "renders" / "final_remotion.mp4").write_bytes(b"old")
    assert not render_stamp.has_stamp(short["proj"])
    ar.apply_remotion(short["proj"])
    assert short["rendered"] == []
    assert ar.RUN_STATS["legacy_skipped"] == 1


@pytest.mark.parametrize("rel", [
    "shortsmith/config.py",          # punch/vfx/yunet tunables reach the frame
    "shortsmith/sfx.py",             # vfx.py imports its word matchers
    "templates/styles/xrp-revolution/style.json",   # the b-roll palette
])
def test_render_inputs_outside_the_scripts_are_covered_by_the_code_digest(rel: str) -> None:
    root = render_stamp.SHORTSMITH_ROOT
    target = (root / rel).resolve()
    assert target.exists(), f"{rel} moved; update CODE_GLOBS"
    assert target in {p.resolve() for p in render_stamp.code_files(root)}


def test_a_covered_file_changing_changes_the_code_digest(tmp_path: Path) -> None:
    """Proves the glob-to-digest path end to end on a throwaway tree."""
    (tmp_path / "shortsmith").mkdir()
    (tmp_path / "templates" / "styles" / "brandy").mkdir(parents=True)
    cfg = tmp_path / "shortsmith" / "config.py"
    style = tmp_path / "templates" / "styles" / "brandy" / "style.json"
    cfg.write_text("PUNCH = 6.0\n", encoding="utf-8")
    style.write_text('{"colors": {"gold": "#f5c542"}}', encoding="utf-8")

    before = render_stamp.code_digest(tmp_path)
    cfg.write_text("PUNCH = 9.0\n", encoding="utf-8")
    after_cfg = render_stamp.code_digest(tmp_path)
    style.write_text('{"colors": {"gold": "#00ff00"}}', encoding="utf-8")
    after_style = render_stamp.code_digest(tmp_path)

    assert before != after_cfg != after_style
    assert before != after_style

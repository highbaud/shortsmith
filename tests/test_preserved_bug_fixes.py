"""Four defects the refactor pass identified but correctly did not fix in place.

A refactor pass must preserve behavior, so it reports suspected bugs rather than
changing them. These are those reports, fixed and pinned.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import add_sfx  # noqa: E402
import calibrate  # noqa: E402
import render_remotion  # noqa: E402


class TestAddSfxSkipsStagedTemps:
    """A crashed render leaves `renders/_base.mp4` or `_sfx_tmp.mp4` behind. When
    one of those was the newest file, add_sfx mixed the sound effects onto the
    uncaptioned staged copy and shipped it. finalize.best_render already skipped
    them; add_sfx was the only reader that did not."""

    def _project(self, tmp_path, monkeypatch, names):
        root = tmp_path / "auto-shorts"
        proj = root / "work-slug" / "short-01-hook"
        renders = proj / "renders"
        renders.mkdir(parents=True)
        for i, name in enumerate(names):
            f = renders / name
            f.write_bytes(b"x")
            import os
            os.utime(f, (1_000_000 + i * 10, 1_000_000 + i * 10))
        monkeypatch.setattr(add_sfx, "AUTO_SHORTS_ROOT", root)
        monkeypatch.setattr(add_sfx, "KIT_RENDERS", tmp_path / "kit-renders")
        return proj

    def test_a_newer_staged_temp_is_not_picked(self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch, ["final_remotion.mp4", "_base.mp4"])
        found = add_sfx.find_render("work-slug", 1)
        assert found is not None
        assert found[1].name == "final_remotion.mp4", "picked the staged temp"

    def test_a_real_render_is_still_picked(self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch, ["_base.mp4", "final_remotion.mp4"])
        found = add_sfx.find_render("work-slug", 1)
        assert found is not None and found[1].name == "final_remotion.mp4"


def test_an_unreadable_broll_auto_json_does_not_escape_the_merge(tmp_path, monkeypatch):
    """The three sibling JSON readers in render_remotion catch OSError. This one
    caught only JSONDecodeError, so a present-but-unreadable file raised out."""
    short = tmp_path / "short-01-hook"
    short.mkdir()
    auto = short / "broll.auto.json"
    auto.mkdir()          # a directory reads as OSError, not a decode error
    merged = render_remotion._merge_broll(short, None)
    assert merged == []


class TestCalibrateJsonGuards:
    def test_a_ledger_that_parses_as_a_list_is_rejected(self, tmp_path, monkeypatch, capsys):
        """`.items()` on a list raised AttributeError. Four sibling readers in
        this module already shape-check what they parsed."""
        ledger = tmp_path / "scheduled_ledger.json"
        ledger.write_text(json.dumps([{"slug": "a"}]), encoding="utf-8")
        monkeypatch.setattr(calibrate, "LEDGER", ledger)
        assert calibrate.load_ledger() == []
        assert "expected an object" in capsys.readouterr().out

    def test_a_valid_ledger_still_loads(self, tmp_path, monkeypatch):
        ledger = tmp_path / "scheduled_ledger.json"
        # The key must carry a `short-NN-<slug>` segment, which is what the real
        # ledger stores and what _SHORT_SLUG_RE matches. A key without one is
        # skipped, so a lazier fixture here would pass whether or not the guard
        # above worked.
        ledger.write_text(json.dumps({
            "jc": {"source-video__short-03-grow-vs-maintain-wealth": [
                {"date": "2026-06-02T09:00:00", "via": "plan"}]}
        }), encoding="utf-8")
        monkeypatch.setattr(calibrate, "LEDGER", ledger)
        rows = calibrate.load_ledger()
        assert len(rows) == 1
        assert rows[0]["slug"] == "grow-vs-maintain-wealth"
        assert rows[0]["brand"] == "jc"
        assert rows[0]["date"]

    def test_a_missing_ledger_is_still_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(calibrate, "LEDGER", tmp_path / "absent.json")
        assert calibrate.load_ledger() == []


def test_a_malformed_single_analytics_file_exits_cleanly(tmp_path, monkeypatch, capsys):
    """The directory branch caught a decode error; the single-file branch had no
    guard at all, so it raised a raw traceback instead of a message."""
    bad = tmp_path / "analytics.json"
    bad.write_text("{ truncated", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["calibrate.py", "--analytics", str(bad)])
    with pytest.raises(SystemExit) as exc:
        raise SystemExit(calibrate.main())
    assert exc.value.code == 1
    assert "not readable JSON" in capsys.readouterr().out

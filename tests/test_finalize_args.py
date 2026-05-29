"""Argument-handling tests for scripts/finalize.py.

We can't run the real Phase 0 / 1 / 2 (they need Remotion, ffmpeg, and
rendered shorts on disk), so we mock the phase functions and verify the CLI
flags route correctly.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import finalize  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_sfx_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the SFX pack is loaded with one file per slot — sufficient for
    the arg-routing tests; the actual phase functions are mocked anyway.
    """
    monkeypatch.setattr(finalize.sfx, "load_sfx_map", lambda: {"swipe-in": [Path("x.wav")]})


def _run(*args) -> int:
    with patch.object(sys, "argv", ["finalize.py", *args]):
        return finalize.main()


def test_default_runs_all_three_phases() -> None:
    with patch.object(finalize, "phase0_remotion", return_value=0) as p0, \
         patch.object(finalize, "phase1_sfx", return_value=0) as p1, \
         patch.object(finalize, "phase2_consolidate", return_value=0) as p2:
        rc = _run()
    assert rc == 0
    p0.assert_called_once()
    p1.assert_called_once()
    p2.assert_called_once()


def test_skip_remotion_skips_phase_0() -> None:
    with patch.object(finalize, "phase0_remotion") as p0, \
         patch.object(finalize, "phase1_sfx", return_value=0) as p1, \
         patch.object(finalize, "phase2_consolidate", return_value=0) as p2:
        rc = _run("--skip-remotion")
    assert rc == 0
    p0.assert_not_called()
    p1.assert_called_once()
    p2.assert_called_once()


def test_skip_sfx_skips_phase_1_and_does_not_require_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If sfx is skipped, an empty pack must NOT be an error.
    monkeypatch.setattr(finalize.sfx, "load_sfx_map", lambda: {})
    with patch.object(finalize, "phase0_remotion", return_value=0) as p0, \
         patch.object(finalize, "phase1_sfx") as p1, \
         patch.object(finalize, "phase2_consolidate", return_value=0) as p2:
        rc = _run("--skip-sfx")
    assert rc == 0
    p0.assert_called_once()
    p1.assert_not_called()
    p2.assert_called_once()


def test_skip_both_runs_only_consolidate() -> None:
    with patch.object(finalize, "phase0_remotion") as p0, \
         patch.object(finalize, "phase1_sfx") as p1, \
         patch.object(finalize, "phase2_consolidate", return_value=0) as p2:
        rc = _run("--skip-remotion", "--skip-sfx")
    assert rc == 0
    p0.assert_not_called()
    p1.assert_not_called()
    p2.assert_called_once()


def test_missing_pack_without_skip_sfx_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finalize.sfx, "load_sfx_map", lambda: {})
    rc = _run()  # no skip flag — must error
    assert rc == 1


def test_offline_sets_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """--offline must set SHORTSMITH_BROLL_OFFLINE for the Phase 0 fetcher."""
    import os

    # Patch the real environment dict for the duration of this test so the
    # side effect doesn't leak into other tests.
    fake_env = dict(os.environ)
    fake_env.pop("SHORTSMITH_BROLL_OFFLINE", None)
    monkeypatch.setattr(os, "environ", fake_env)

    with patch.object(finalize, "phase0_remotion", return_value=0), \
         patch.object(finalize, "phase1_sfx", return_value=0), \
         patch.object(finalize, "phase2_consolidate", return_value=0):
        _run("--offline")

    assert fake_env.get("SHORTSMITH_BROLL_OFFLINE") == "1"

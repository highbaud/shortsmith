"""Every shipped module must import.

CI used to check this against a hand-written list of module names, which went
stale the moment a module was added: `shortsmith.names`, `shortsmith.layouts`,
`shortsmith.gallery` and everything under `scripts/` were invisible to it. An
import error in one of those surfaced only when a render run crashed.

Discovery replaces the list. `shortsmith/` is walked from disk because the whole
package is tracked; `scripts/` is read from `git ls-files` because a working
checkout also holds untracked local one-off scripts, which are nobody's contract
and routinely carry machine paths.

The imports run in one subprocess so a module that sets global state on import
cannot leak into the rest of the suite.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Imported for its side effect of being importable, so the child process needs
# the same two entries on sys.path that the pipeline scripts get at runtime.
CHILD = r"""
import importlib, sys
sys.path.insert(0, {root!r})
sys.path.insert(0, {scripts!r})
failed = []
for mod in {mods!r}:
    try:
        importlib.import_module(mod)
    except BaseException as exc:          # noqa: BLE001 - report, do not mask
        failed.append(f"{{mod}}: {{type(exc).__name__}}: {{exc}}")
if failed:
    print("\n".join(failed))
    sys.exit(1)
"""


def _tracked_scripts() -> list[str]:
    """Module names for tracked `scripts/*.py`, or [] when git cannot answer."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "scripts/*.py"],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return sorted(Path(line).stem for line in out.stdout.split() if line)


def _package_modules() -> list[str]:
    """Dotted module names for every file in the shortsmith package."""
    mods = []
    for path in sorted((ROOT / "shortsmith").rglob("*.py")):
        rel = path.relative_to(ROOT)
        if "__pycache__" in rel.parts:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            mods.append(".".join(parts))
    return mods


def test_every_shipped_module_imports() -> None:
    mods = _package_modules() + _tracked_scripts()
    assert len(mods) > 20, f"discovery found only {len(mods)} modules, expected the full tree"

    code = CHILD.format(root=str(ROOT), scripts=str(ROOT / "scripts"), mods=mods)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    assert proc.returncode == 0, "modules failed to import:\n" + proc.stdout + proc.stderr


def test_importing_a_module_has_no_side_effects() -> None:
    """Importing must not print. A module that prints on import is doing work.

    `scripts/build_ledger.py` (untracked) rewrites a JSON file at import time.
    Tracked code must not, or a plain `import` mutates the checkout.
    """
    mods = _package_modules() + _tracked_scripts()
    code = CHILD.format(root=str(ROOT), scripts=str(ROOT / "scripts"), mods=mods)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    if proc.returncode != 0:
        pytest.skip("import failure is reported by the other test")
    assert proc.stdout.strip() == "", f"module printed on import:\n{proc.stdout}"

"""Importing command modules must leave the caller's logging setup intact."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", [
    "scripts.batch_pipeline", "scripts.batch_transcribe", "scripts.build_sfx_index",
    "scripts.build_sfx_pack", "scripts.ingest_premade", "scripts.redo_existing",
    "scripts.redo_outdated", "scripts.render_all", "scripts.reprocess_all",
])
def test_script_import_preserves_unconfigured_root_logger(module: str) -> None:
    code = """
import importlib, json, logging, sys
root = logging.getLogger()
root.handlers.clear()
root.setLevel(logging.WARNING)
before = [len(root.handlers), root.level]
importlib.import_module(sys.argv[1])
if sys.argv[1] == "scripts.batch_transcribe":
    assert "numpy" not in sys.modules
    assert "faster_whisper" not in sys.modules
print(json.dumps([before, [len(root.handlers), root.level]]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, module], capture_output=True, text=True, check=True,
    )
    before, after = json.loads(result.stdout)
    assert after == before

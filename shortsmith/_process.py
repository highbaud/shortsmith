"""Find Windows tools without the implicit current-directory executable search."""
from __future__ import annotations

import os
import sys
from pathlib import Path, PureWindowsPath


def find_windows_path_file(*names: str) -> Path:
    """Find an exact file name in explicit absolute PATH directories only."""
    if not names or any(not name or PureWindowsPath(name).name != name for name in names):
        raise ValueError("Expected tool file names without directory components")
    current = Path.cwd().resolve()
    for entry in os.get_exec_path():
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        directory = directory.resolve()
        if directory == current:
            continue
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()
    raise FileNotFoundError("Tool not found in absolute PATH directories: " + ", ".join(names))


def resolve_executable(name: str) -> str:
    """Resolve native Windows tools; keep normal PATH behavior on Unix."""
    if sys.platform != "win32":
        return name
    suffix = PureWindowsPath(name).suffix.lower()
    if suffix and suffix not in {".exe", ".com"}:
        raise ValueError(f"A native Windows executable is required: {name}")
    names = (name,) if suffix else (name + ".exe", name + ".com")
    executable = find_windows_path_file(*names)
    if executable.suffix.lower() not in {".exe", ".com"}:
        raise ValueError(f"A native Windows executable is required: {executable}")
    return str(executable)

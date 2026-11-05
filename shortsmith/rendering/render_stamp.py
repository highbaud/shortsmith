"""Fingerprint render inputs and check whether an output is current."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict, TypeGuard

from shortsmith._files import atomic_write
from shortsmith.config import REPO_ROOT
from shortsmith.records import ClipSpec, TranscriptWord

from . import broll_assets, mentions, project_inputs, source_broll

SHORTSMITH_ROOT = REPO_ROOT

STAMP_NAME = "final_remotion.stamp.json"

STAMP_VERSION = 1

CODE_GLOBS = (
    "shortsmith/rendering/*.py",
    "scripts/capture_source.mjs",
    "shortsmith/vfx.py",
    "shortsmith/layouts.py",
    "shortsmith/gallery.py",
    "shortsmith/names.py",
    "shortsmith/sfx.py",
    "shortsmith/config.py",
    "templates/layouts/*.json",
    "templates/styles/*/style.json",
    "remotion/src/**/*.ts",
    "remotion/src/**/*.tsx",
    "remotion/package.json",
)


type StampInput = str | bool | list[str]


class ComputedStamp(TypedDict):
    version: int
    digest: str
    inputs: dict[str, StampInput]


class StoredStamp(TypedDict):
    digest: object
    version: NotRequired[object]
    inputs: NotRequired[dict[str, object]]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _file_digest(path: Path) -> str:
    try:
        return _digest(path.read_bytes())
    except OSError:
        return ""


def code_files(root: Path = SHORTSMITH_ROOT) -> list[Path]:
    out: list[Path] = []
    for pattern in CODE_GLOBS:
        out.extend(sorted(root.glob(pattern)))
    return out


def code_digest(root: Path = SHORTSMITH_ROOT) -> str:
    parts = []
    for p in code_files(root):
        label = p.relative_to(root).as_posix() if p.is_relative_to(root) else p.name
        parts.append(f"{label}={_file_digest(p)}")
    return _digest("\n".join(parts).encode("utf-8"))


def people_digest(words: Sequence[TranscriptWord]) -> str:
    """The photo state of every person the words mention.

    Scoped to the people this short can show, so dropping in a manual photo
    of David Schwartz re-renders the shorts that name him and no others.
    """
    manifest = broll_assets._read_manifest()
    parts: list[str] = []
    for name, _role, _t in mentions.find_person_mentions(words):
        slug = broll_assets._slug(name)
        manual = broll_assets._manual_person_photo(slug)
        cached = broll_assets._cached_person_photo(slug)
        parts.append(
            json.dumps(
                {
                    "name": name,
                    "manifest": manifest.get(name),
                    "manual": f"{manual.name}:{_file_digest(manual)}" if manual else "",
                    "cached": f"{cached.name}:{_file_digest(cached)}" if cached else "",
                },
                sort_keys=True,
            )
        )
    return _digest("\n".join(parts).encode("utf-8"))


def _file_stat(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    # Nanoseconds, not int(st_mtime): whole-second mtimes let a same-size base
    # render swapped inside one second read as unchanged, and a stamp that says
    # "current" for a base it never saw ships a stale video.
    return f"{st.st_size}:{st.st_mtime_ns}"


def source_assets_digest(project_dir: Path, enabled: frozenset[str]) -> str:
    """Include the active source slides and their local pixels in cache decisions."""
    entries = []
    root = project_dir.resolve()
    for name in ("broll.auto.json", "broll.json"):
        try:
            slides = json.loads((project_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(slides, list):
            continue
        for slide in slides:
            if not isinstance(slide, dict) or slide.get("source_kind") not in enabled:
                continue
            asset = (root / str(slide.get("src", ""))).resolve()
            pixels = _file_digest(asset) if asset.is_relative_to(root) else ""
            entries.append({"slide": slide, "pixels": pixels})
    return _digest(json.dumps(entries, sort_keys=True).encode("utf-8"))


def compute_stamp(
    project_dir: Path,
    *,
    base: Path,
    style: str,
    platform: str,
    captions: bool,
    sources: str | None = None,
) -> ComputedStamp:
    project_dir = Path(project_dir)
    words_path = project_dir / "assets" / "words.json"
    try:
        words = json.loads(words_path.read_text(encoding="utf-8")) if words_path.exists() else []
    except (json.JSONDecodeError, OSError):
        words = []
    clip: ClipSpec | dict = project_inputs.clip_for(project_dir) or {}
    enabled = source_broll.enabled_sources(project_dir, sources)
    inputs: dict[str, StampInput] = {
        "base": _file_stat(base),
        "words": _file_digest(words_path),
        "clip": _digest(json.dumps(clip, sort_keys=True, ensure_ascii=False).encode("utf-8")),
        "manual_broll": _file_digest(project_dir / "broll.json"),
        "people": people_digest(words if isinstance(words, list) else []),
        "code": code_digest(),
        "style": style,
        "platform": platform,
        "captions": captions,
        "source_options": sorted(enabled),
        "source_spec": _file_digest(project_dir / "broll.sources.json"),
        "source_provenance": _file_digest(project_dir / "broll.sources.provenance.json"),
        "source_assets": source_assets_digest(project_dir, enabled),
    }
    payload = json.dumps(inputs, sort_keys=True).encode("utf-8")
    return {"version": STAMP_VERSION, "digest": _digest(payload), "inputs": inputs}


def stamp_path(project_dir: Path) -> Path:
    return Path(project_dir) / "renders" / STAMP_NAME


def has_stamp(project_dir: Path) -> bool:
    """True when a stamp file exists, readable or not.

    read_stamp() cannot tell "never stamped" from "stamp unreadable", and the
    two deserve opposite treatment: the first is a short that predates stamps,
    the second is a short that was stamped and whose record we lost, which must
    re-render rather than fall back to the mtime rule.
    """
    return stamp_path(project_dir).exists()


def _is_stored_stamp(data: object) -> TypeGuard[StoredStamp]:
    """Check parsed JSON without filtering fields from older stamp versions."""
    return (
        isinstance(data, dict)
        and "digest" in data
        and ("inputs" not in data or isinstance(data["inputs"], dict))
    )


def read_stamp(project_dir: Path) -> StoredStamp | None:
    path = stamp_path(project_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if _is_stored_stamp(data) else None


def write_stamp(project_dir: Path, stamp: Mapping[str, object]) -> Path:
    path = stamp_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(stamp, indent=2, sort_keys=True))
    return path


def changed_inputs(prior: StoredStamp | None, current: ComputedStamp) -> list[str]:
    """Which inputs differ between a recorded stamp and the current one."""
    if not prior:
        return ["no stamp"]
    if prior.get("version") != current.get("version"):
        return ["stamp version"]
    before = prior.get("inputs") or {}
    after = current.get("inputs") or {}
    return [key for key in after if before.get(key) != after[key]]


def is_current(project_dir: Path, current: ComputedStamp, output: Path) -> bool:
    """True when the output exists and was rendered from exactly these inputs."""
    if not Path(output).exists():
        return False
    prior = read_stamp(project_dir)
    if not prior:
        return False
    # Version first: bumping STAMP_VERSION exists to force a re-render, so a
    # matching digest under an older version must not read as current.
    return prior.get("version") == current.get("version") and prior.get("digest") == current.get(
        "digest"
    )

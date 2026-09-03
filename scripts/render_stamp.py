"""Decide whether a short's Remotion render is current.

apply_remotion used to compare one pair of mtimes: final_remotion.mp4 against
the Hyperframes base render. That misses everything else a render reads. A
resolver fix, a caption change, a new manual photo, a corrected transcript:
none of them touch the base, so none of them re-rendered anything, and the
July 31 batch re-shipped a photo of the wrong person because of it.

The stamp is a digest of every input the render depends on: the base render,
the clip spec, the words, the hand-authored b-roll list, the photo state of
every person the words mention, the render code, and the style / platform /
captions switches. It is written beside the output after a successful render
and compared before the next one. Different digest, different render. A short
with no stamp at all was rendered before stamps existed; the caller decides how
to treat that (apply_remotion keeps the old mtime rule for it, so an unscoped
finalize does not rebuild the whole library by surprise).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_broll  # noqa: E402
from render_remotion import _clip_for  # noqa: E402

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
STAMP_NAME = "final_remotion.stamp.json"
STAMP_VERSION = 1

# Everything whose text decides what a render looks like. A change to any of
# these re-renders every short on the next pass (that is the point).
CODE_GLOBS = (
    "scripts/render_remotion.py", "scripts/render_stamp.py",
    "scripts/apply_remotion.py", "scripts/gen_broll.py",
    "scripts/person_photos.py", "scripts/brand_logos.py", "scripts/wikidata.py",
    "shortsmith/vfx.py", "shortsmith/layouts.py", "shortsmith/gallery.py",
    "shortsmith/names.py", "templates/layouts/*.json",
    "remotion/src/*.ts", "remotion/src/*.tsx", "remotion/package.json",
)


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


def people_digest(words: list[dict]) -> str:
    """The photo state of every person the words mention.

    Scoped to the people this short can show, so dropping in a manual photo
    of David Schwartz re-renders the shorts that name him and no others.
    """
    manifest = gen_broll._read_manifest()
    parts: list[str] = []
    for name, _role, _t in gen_broll.find_person_mentions(words):
        slug = gen_broll._slug(name)
        manual = gen_broll._manual_person_photo(slug)
        cached = gen_broll._cached_person_photo(slug)
        parts.append(json.dumps({
            "name": name,
            "manifest": manifest.get(name),
            "manual": f"{manual.name}:{manual.stat().st_size}" if manual else "",
            "cached": f"{cached.name}:{cached.stat().st_size}" if cached else "",
        }, sort_keys=True))
    return _digest("\n".join(parts).encode("utf-8"))


def _file_stat(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    return f"{st.st_size}:{int(st.st_mtime)}"


def compute_stamp(project_dir: Path, *, base: Path, style: str, platform: str,
                  captions: bool) -> dict:
    project_dir = Path(project_dir)
    words_path = project_dir / "assets" / "words.json"
    try:
        words = json.loads(words_path.read_text(encoding="utf-8")) if words_path.exists() else []
    except (json.JSONDecodeError, OSError):
        words = []
    clip = _clip_for(project_dir) or {}
    inputs = {
        "base": _file_stat(base),
        "words": _file_digest(words_path),
        "clip": _digest(json.dumps(clip, sort_keys=True, ensure_ascii=False).encode("utf-8")),
        "manual_broll": _file_digest(project_dir / "broll.json"),
        "people": people_digest(words if isinstance(words, list) else []),
        "code": code_digest(),
        "style": style,
        "platform": platform,
        "captions": captions,
    }
    payload = json.dumps(inputs, sort_keys=True).encode("utf-8")
    return {"version": STAMP_VERSION, "digest": _digest(payload), "inputs": inputs}


def stamp_path(project_dir: Path) -> Path:
    return Path(project_dir) / "renders" / STAMP_NAME


def read_stamp(project_dir: Path) -> dict | None:
    path = stamp_path(project_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) and "digest" in data else None


def write_stamp(project_dir: Path, stamp: dict) -> Path:
    path = stamp_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stamp, indent=2, sort_keys=True), encoding="utf-8")
    return path


def changed_inputs(prior: dict | None, current: dict) -> list[str]:
    """Which inputs differ between a recorded stamp and the current one."""
    if not prior:
        return ["no stamp"]
    if prior.get("version") != current.get("version"):
        return ["stamp version"]
    before = prior.get("inputs") or {}
    after = current.get("inputs") or {}
    return [key for key in after if before.get(key) != after[key]]


def is_current(project_dir: Path, current: dict, output: Path) -> bool:
    """True when the output exists and was rendered from exactly these inputs."""
    if not Path(output).exists():
        return False
    prior = read_stamp(project_dir)
    return bool(prior) and prior.get("digest") == current.get("digest")

"""Named layout presets — saved multi-speaker frame formats.

A layout preset is the whole recipe for how a source frame becomes a 1080x1920
short: how big each speaker's square is, where the caption band sits, how far the
composition is inset from the platform's UI chrome, and how the panels are
dressed (border, backdrop). Presets live as JSON under `templates/layouts/` so a
format that has been tuned on real footage can be reused verbatim on the next
video instead of being re-derived by hand.

Select one with `--layout-preset <name>`, `SHORTSMITH_LAYOUT_PRESET`, or per clip
with `"layout_preset": "<name>"` in clips.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from .gallery import Rect, StackLayout, stack_layout

LAYOUTS_DIR = Path(__file__).parent.parent / "templates" / "layouts"

DEFAULT_PRESET = "two-speaker-stack"


@dataclass(frozen=True)
class LayoutSpec:
    """A resolved layout preset. Frozen: a preset describes a saved format, so
    nothing downstream should be able to mutate it mid-run."""

    name: str = DEFAULT_PRESET
    description: str = ""

    # --- Composition (px on the 1080x1920 canvas) ---
    panel: int = 705
    band: int | None = None       # None = whatever the margins leave over
    top_margin: int = 30
    bottom_margin: int = 200

    # --- Where each speaker's face sits inside their own square ---
    # Separate targets per panel so both faces lean toward the middle of the
    # frame, away from the top notch and the bottom caption overlay.
    face_height_frac: float = 0.30
    face_target_y_top: float = 0.48
    face_target_y_bottom: float = 0.38
    match_face_size: bool = True

    # --- Source interpretation ---
    order: str = "lr"             # "lr" = left tile on top, "rl" flips
    dark_threshold: float = 25.0
    sample_every: int = 0         # 0 = derive from cfg.reframe_sample_every

    # Explicit webcam tiles, left to right, as [x, y, w, h] in SOURCE pixels.
    # Brightness detection reads the tiles off a frame, which is the right
    # default but fails on sources where a speaker's dark clothing or a dark
    # studio backdrop drags a tile's mean below `dark_threshold` — the tile then
    # measures short, or is missed entirely, and the run aborts. Pinning the
    # geometry that was measured once off the real footage removes that whole
    # class of failure. None = detect.
    tiles: tuple | None = None

    # --- Name badge the gallery app burns into each tile ---
    avoid_badge: bool = True
    badge_w_frac: float = 0.32
    badge_h_frac: float = 0.16

    # --- Panel dressing ---
    border_color: str = "0xD4AF37@0.85"
    border_width: int = 3
    bg_blur: int = 25
    bg_dim: float = 0.30

    def layout(self) -> StackLayout:
        """The concrete panel/band geometry this preset describes."""
        return stack_layout(
            panel=self.panel,
            band=self.band,
            top_margin=self.top_margin,
            bottom_margin=self.bottom_margin,
        )

    def face_target_y(self, index: int) -> float:
        """Face placement for panel `index` (0 = top, 1 = bottom)."""
        return self.face_target_y_top if index == 0 else self.face_target_y_bottom

    def pinned_tiles(self, frame_w: int, frame_h: int) -> list[Rect] | None:
        """The preset's explicit tiles for a `frame_w` x `frame_h` source.

        None when the preset does not pin any, meaning the caller should detect
        them. Pinned tiles are measured against one specific source geometry, so
        a frame they do not fit is an error rather than something to clamp — a
        silently clamped tile would frame the wrong part of the picture for the
        whole batch.
        """
        if not self.tiles:
            return None
        out: list[Rect] = []
        for i, t in enumerate(self.tiles):
            if len(t) != 4:
                raise ValueError(
                    f"layout preset {self.name!r}: tiles[{i}] must be "
                    f"[x, y, w, h], got {list(t)!r}"
                )
            x, y, w, h = (int(v) for v in t)
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > frame_w or y + h > frame_h:
                raise ValueError(
                    f"layout preset {self.name!r}: tiles[{i}] {[x, y, w, h]} "
                    f"does not fit a {frame_w}x{frame_h} frame"
                )
            out.append(Rect(x, y, w, h))
        if len(out) != 2:
            raise ValueError(
                f"layout preset {self.name!r}: need exactly 2 tiles, got {len(out)}"
            )
        return out


def preset_path(name: str) -> Path:
    return LAYOUTS_DIR / f"{name}.json"


def list_presets() -> list[str]:
    if not LAYOUTS_DIR.is_dir():
        return []
    return sorted(p.stem for p in LAYOUTS_DIR.glob("*.json"))


def load_preset(name: str | None = None) -> LayoutSpec:
    """Load a saved layout preset by name.

    Unknown keys in the file are ignored rather than fatal, so a preset written
    against a newer shortsmith still loads. A missing file is an error: silently
    falling back to defaults would render a whole batch in the wrong format.
    """
    name = name or DEFAULT_PRESET
    path = preset_path(name)
    if not path.exists():
        available = ", ".join(list_presets()) or "none"
        raise FileNotFoundError(
            f"Layout preset {name!r} not found at {path} (available: {available})"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(LayoutSpec)}
    data = {k: v for k, v in raw.items() if k in known}
    if data.get("tiles") is not None:
        # JSON gives lists; a frozen spec should not hand out mutable geometry.
        data["tiles"] = tuple(tuple(t) for t in data["tiles"])
    spec = LayoutSpec(**data)
    spec.layout()  # fail fast on a preset whose numbers do not fit the frame
    return spec

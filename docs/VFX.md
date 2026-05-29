# Visual transitions (VFX) layer

Capcut-style overlay primitives rendered in Remotion — glare sweeps,
zoom-punches, and flashes that hit on the same beats as the audio SFX.

## Pipeline position

```
Hyperframes render (final.mp4)
     ↓
Remotion captions + b-roll + VFX  ←—  YOU ARE HERE (Phase 10)
     ↓
final_remotion.mp4
     ↓
SFX overlay (final_sfx.mp4)
```

VFX is rendered alongside captions and b-roll in the same Remotion pass,
so there's no extra phase, no extra ffmpeg step, no extra encode loss.

## The three primitives

| Effect | Duration | What it does |
|---|---|---|
| `glare` | 280ms | Diagonal light streak sweeps left → right across the frame. Bell-curve opacity ease. Tinted with the slot color (white / gold / red). Uses `mix-blend-mode: screen` so it brightens highlights without washing out shadows. |
| `zoom-punch` | 220ms | Scale-bump on the base video container: 1.00 → 1.04 → 1.00, bell-curve eased. Anchored at the face zone (40% from top, matching `face_target_y`). |
| `flash` | 90ms | Full-frame color tint that snaps in (35% of duration) then trails out. Same screen-blend mode as glare — tints rather than washes. |

Effects can stack. The hook slam fires **all three** simultaneously by default,
which is what gives it that "Capcut intro" punch. Overlapping zoom-punches take
the max scale (not the sum) so stacked hooks don't compound into a noticeable
zoom.

## Triggers (the 4 high-impact slots)

| Slot | When it fires | Default effects | Tint |
|---|---|---|---|
| `hook-impact` | Once at t≈0 (opening slam) | glare + zoom-punch + flash | `#ffffff` white |
| `ding` | Each `bigstat` callout whose text has a number or `$` | glare | `#f5c842` gold |
| `cash-register` | First money word in the clip | glare + flash | `#f5c842` gold |
| `wrong-answer` | First negative-outcome word (crashed / scammed / rugged / ...) | flash + zoom-punch | `#ff3653` red |

Same trigger taxonomy as the SFX system — VFX piggybacks on the same
`Config.sfx_semantic_mode` so `sparing` / `every` / `off` apply identically.

## Configuration

| Env var / config field | Default | Purpose |
|---|---|---|
| `SHORTSMITH_VFX` | `on` | `off` / `0` / `false` / `no` disables the entire VFX layer |
| `SHORTSMITH_VFX_INTENSITY` | `1.0` | Global 0..1 multiplier on opacity + scale-bump |
| `Config.vfx_triggers` | (table above) | `dict[slot -> list[effect_name]]` |
| `Config.vfx_colors` | (table above) | `dict[slot -> hex color]` |

**Per-slot tweaks** — override the dicts to taste. Example: heavier
ding accent (add flash on bigstats too), or replace red for wrong-answer
with a paler tint:

```python
cfg.vfx_triggers["ding"] = ["glare", "flash"]
cfg.vfx_colors["wrong-answer"] = "#ff9d99"
```

**Going louder** — `vfx_intensity > 1.0` is allowed (e.g. 1.3 for
extra-pop) but the effects are tuned for `1.0`; values past `1.5` look
overdone on most footage.

## Trigger logic & timing

Pure Python lives in `shortsmith/vfx.py:plan_vfx_events()`. It reads the same
`clip` spec (`hook`, `callouts`) and aligned `words.json` that drives SFX, so
audio + visual punctuation always land on the same frame.

The render path: `scripts/render_remotion.py` calls `plan_vfx_events()`,
serializes the list as `vfxEvents` in the Remotion props JSON, and the
`<Short>` component reads it from `ShortProps` and renders one `<Sequence>`
per glare/flash event plus a single `useZoomPunchScale(events)` hook that
applies the scale bump to the base video container.

## Effect implementations

All three live in `remotion/src/VFX.tsx`:

- **`<Glare>`** — single absolutely-positioned `<div>` with a CSS linear
  gradient. Position interpolates `left: -40% → 140%` over the duration.
  Skewed `-18°` for the cinematic angled-streak feel. `filter: blur(18px)`
  softens the edges. Mix-blend-mode `screen` ensures dark areas stay dark.
- **`useZoomPunchScale(events)`** — hook that iterates all events,
  evaluates `sin(progress * π)` for each active one, returns
  `1 + max(per-event bumps)`. Used by `<Short>` to `transform: scale()` the
  `<OffthreadVideo>` container.
- **`<Flash>`** — single `<AbsoluteFill>` with `backgroundColor: color`,
  opacity ramping `0 → 0.42 * intensity → 0` with a front-loaded envelope
  (peaks at 35% progress so it reads as a *snap* rather than a *swell*).

No external deps, no canvas, no WebGL — plain CSS animated by Remotion's
`useCurrentFrame()`. Render time impact: ~negligible (each effect is one
extra DOM node per active frame).

## When NOT to use it

- **Talking-head clips with no visual punch points** — if the clip has no
  hook callout, no bigstats, no money or negative-outcome keywords, VFX
  emits nothing and the renderer no-ops. (Verify by checking the
  `vfx=N` count in the render log.)
- **Footage with existing motion graphics** — if the underlying clip
  already has heavy Capcut-style work baked in, layering glares on top
  can compete visually. Disable per-clip with `cfg.vfx_enabled = False`.

## Disabling for one clip

```bash
SHORTSMITH_VFX=off uv run python scripts/apply_remotion.py path/to/short-NN-slug/
```

Or in code:

```python
cfg = Config()
cfg.vfx_enabled = False
```

The Remotion render then skips the entire VFX overlay — captions + b-roll
still render normally.

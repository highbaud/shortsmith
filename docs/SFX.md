# Sound effects layer

Post-render audio overlay. Mixes a curated SFX pack on top of the speech audio of each finished short.

## Pipeline position

```
Hyperframes render (final.mp4)
     ↓
Remotion captions + b-roll (final_remotion.mp4)
     ↓
SFX overlay  ←—  YOU ARE HERE
     ↓
final_sfx.mp4
```

Non-destructive — writes `<project>/renders/final_sfx.mp4` beside the input. Re-runnable. Driven by `scripts/add_sfx.py` (per-project) or `scripts/finalize.py` Phase 1 (everything).

## The pack

Lives at `assets/sfx/pack/` with `pack.json` mapping each slot to one or more variant files (the mixer rotates through variants for variety so identical hits never repeat).

| Slot | When it fires | Variants (shipped) | Default level |
|---|---|---|---|
| `hook-impact` | Once at t=0, on the opening slam | 4 | -9 dBFS (peak), 0.7 × sfx_gain |
| `swipe-in` | At each callout's `local_start` | 7 | 0.55 × sfx_gain |
| `swipe-out` | At each callout's end (opt-in via `sfx_swipe_out=True`) | 3 | 0.45 × sfx_gain |
| `cash-register` | First time a money word is spoken in the clip | 1 | 0.85 × sfx_gain |
| `ding` | On each `bigstat` callout whose text has a number or `$` | 6 | 0.7 × sfx_gain |
| `whoosh` | Fallback for generic transitions | 3 | 0.55 × sfx_gain |

Variants rotate per call (see `_VariantRotation` in `shortsmith/sfx.py`)
so a clip with 5 callouts cycles through 5 different swipe-in sounds.

Global gain: `sfx_gain = 0.7` by default (`SHORTSMITH_SFX_GAIN`).

All slots are optional — if a slot has no file in the pack, it's silently skipped. So you can ship with just `swipe-in` populated and the rest gets passed through cleanly.

## Building the pack

The pack lives at `assets/sfx/pack/` as level-normalized files. Build it from raw drops in `assets/sfx/`:

```bash
# Drop your one-shots into assets/sfx/ with these base names:
#   swipe-in.wav         swipe-out.wav      hook-impact.wav
#   cash-register.wav    ding.wav           whoosh.wav
# Variants are fine — call them swipe-in-2.wav, swipe-in-3.wav, etc.

uv run python scripts/build_sfx_pack.py
```

## Discovering what's available (index)

To survey every raw drop without listening to each file, run:

```bash
uv run python scripts/build_sfx_index.py
```

It writes `assets/sfx/index.json` (structured) + `assets/sfx/CATALOG.md`
(human-readable). For each file: duration, peak dBFS, channels, an auto
category (whoosh / whip / ding / impact / money / magic / click / pop / ui /
riser / camera / error / beep / unknown), and a list of slot suggestions.

Use this when deciding which raw drops to promote into `CURATION` inside
`scripts/build_sfx_pack.py`. The CATALOG groups files by their best-fit slot
so you can see at a glance which slots are over- or under-served.

The builder:
- Normalizes peak to **-9 dBFS** so everything sits at a consistent level.
- Trims leading silence so the hit lands exactly on the beat.
- Resamples to 48 kHz mono.
- Writes the rebuilt pack to `assets/sfx/pack/` and updates `pack.json`.

## Approved levels

Per the user's listening test on Jake Claver content (2026-05-28):

- SFX peak: -9 dBFS (normalized)
- Final mix sits 10–16 dB under speech
- Output limiter: -0.3 dBFS ceiling
- Mode: `sparing` (cash-register on FIRST money word only, ding on bigstat only)

These defaults are intentional. Raising them makes the SFX compete with the voice; lowering further makes them inaudible on phone speakers.

## Trigger modes

`SHORTSMITH_SFX_SEMANTIC`:
- `sparing` (default) — cash-register on first money word only, ding on bigstat number callouts only, swipes on every callout.
- `every` — cash-register on every money word, ding on every bigstat callout. Use sparingly; very dense.
- `off` — only structural triggers (hook-impact + swipe-in). No semantic SFX.

## Money-word detection

`shortsmith/config.py` ships with this default list (configurable):

```python
money_keywords = [
    "money", "cash", "dollar", "dollars", "rich", "wealth", "wealthy", ...
]
```

Plus regex matches for `$<n>`, `<n>K`, `<n>M`, `<n>B`, `<n>%` — covers most cited numbers without false-firing on the word "percent" alone.

## CLI

```bash
# Apply to one project
uv run python scripts/add_sfx.py path/to/auto-shorts/<source>/short-NN-<hook>/

# Apply to everything (idempotent — skips up-to-date shorts)
uv run python scripts/finalize.py
```

## Tests

Unit tests live at `tests/test_sfx.py`. Cover `plan_events` — the pure trigger logic that turns a clip spec + words.json into a list of (timestamp, slot) tuples. 9 tests, no audio required.

# Remotion captions + b-roll layer

Step 10 of the pipeline. Takes the Hyperframes base render and wraps it with word-level karaoke captions and AI-selected b-roll cutaways.

## Pipeline position

```
Hyperframes render (final.mp4)
     ↓
Remotion layer  ←—  YOU ARE HERE
     ↓
final_remotion.mp4
     ↓
SFX overlay → final_sfx.mp4
```

Driven by `scripts/apply_remotion.py` (per-project) or `scripts/finalize.py` Phase 0 (everything).

## What the Remotion project does

The Remotion project at `remotion/` is a React/Remotion 4.0 composition that renders 1080×1920 at 30 fps. It composes three layers:

1. **Base video** — the Hyperframes `final.mp4` plays full-frame.
2. **B-roll cutaways** — at timestamps from `broll.auto.json`, a full-screen card with a logo / photo / stat replaces the base. Timed to land in the FREE GAPS between Hyperframes overlays (slam hook and callouts), so the speaker face cam is never hidden behind a cutaway.
3. **Word-level karaoke captions** — driven by `assets/words.json`, each word highlights as it's spoken (~20 ms accurate thanks to WhisperX forced alignment in step 6).

Output: `<project>/renders/final_remotion.mp4`.

## B-roll engine (`scripts/gen_broll.py`)

The b-roll picker reads the clip transcript and proposes cutaways that match what's being said. Two engines:

**Claude engine** (default when `ANTHROPIC_API_KEY` is set) — reads the transcript + free gaps and proposes stat / text / list / logo / person slides. The system prompt is at [`prompts/gen_broll.md`](../prompts/gen_broll.md).

**Heuristic fallback** — regex on transcript for dollar amounts / percentages → stat slides; small curated map of crypto/tech brands and persons → logo/person slides. No API call. Trigger with `--heuristic`.

### Asset sourcing

Every asset is **public-domain or CC-licensed** and downloaded into the project's `assets/broll/` at generation time:

| Slide type | Source order |
|---|---|
| Logo | Simple Icons (`cdn.simpleicons.org/<slug>`) → vectorlogo.zone SVG fallback |
| Person photo | Wikimedia Commons search → Openverse (Flickr + CC libs) → Wikipedia REST lead image |
| Stat / text / list | Generated on the fly in the Remotion composition (no asset fetch) |

Person photos are **shuffled across sources** so the same person doesn't always pick the same image — pass `--photo-seed` for reproducibility.

### Network politeness

Public APIs get hit responsibly:

- **On-disk cache** at `.cache/broll-fetch/<sha1>.<ext>` — every successful URL response is stored once. A 1000-clip reprocess hits each public asset URL exactly once.
- **Polite throttle** — minimum 0.5 s between live fetches with jitter (~2 req/s steady-state).
- **Exponential backoff** on 429 / 503 (1 s, 2 s, 4 s + jitter, up to 3 retries).
- **Identifying User-Agent** — `shortsmith/0.5 (+https://github.com/highbaud/shortsmith)`. Wikimedia explicitly asks for this; bot UAs get rate-limited harder.

### CLI flags

```bash
# Dry-run: print proposed slides without downloading
uv run python scripts/gen_broll.py path/to/short/

# Force the heuristic engine (no API call)
uv run python scripts/gen_broll.py path/to/short/ --heuristic

# Use only the on-disk cache — no live network at all
uv run python scripts/gen_broll.py path/to/short/ --offline

# Bypass the cache — every URL re-fetches
uv run python scripts/gen_broll.py path/to/short/ --no-cache

# Reproducible photo picks
uv run python scripts/gen_broll.py path/to/short/ --photo-seed 42
```

Or via env: `SHORTSMITH_BROLL_OFFLINE=1`, `SHORTSMITH_BROLL_NOCACHE=1`.

### Output

Writes `<project>/broll.auto.json`. This is **merged** with any hand-authored `<project>/broll.json` at render time (manual wins on overlap), so editing the auto output by hand is safe — re-running regenerates only `broll.auto.json`.

## CLI (Remotion render)

```bash
# Apply to one project
uv run python scripts/apply_remotion.py path/to/auto-shorts/<source>/short-NN-<hook>/

# Apply to everything (Phase 0 of finalize)
uv run python scripts/finalize.py
```

## Skip Remotion entirely

If you don't want captions / b-roll on a given run:

```bash
uv run python scripts/finalize.py --skip-remotion
```

SFX (Phase 1) falls through to the Hyperframes base render. Consolidation (Phase 2) picks up whichever final exists.

## Install requirements

- **Node 18+** (`npm` + `npx`)
- One-time: `cd remotion && npm install` (~600 MB; `setup.sh` does this automatically if `npm` is on PATH)
- For Claude b-roll picker: `ANTHROPIC_API_KEY` (uses the same key as step 2 clip selection)

## Tests

Unit tests at `tests/test_gen_broll_http.py` cover the network layer (8 tests: cache hits, offline, nocache, retry-on-429, fail-fast on 404, etc.). No real HTTP required.

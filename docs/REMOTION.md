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

**Heuristic fallback** — regex on transcript for dollar amounts / percentages → stat slides; small curated map of crypto/tech brands and persons → logo/person slides. No API call. Trigger with `--heuristic`. People are matched on the full name, on surname aliases where the word is unambiguous (`PERSON_ALIASES`: Trump, Gensler, Buffett, Elon, CZ...), and on ASR mishearings (`ASR_VARIANTS`: "Sailor", "Larson") when the transcript capitalizes them or the first name precedes them. A surname after someone else's first name ("Barron Trump") does not count, and possessives ("Gensler's") do not hide a mention.

**Split-stack shorts** keep their b-roll. Cutaways play full-frame as usual. A logo badge moves off its usual upper-center spot (the top speaker's face is there) to a mark-only tile on the blurred backdrop beside the top square: `render_remotion._logo_badge_anchor` derives the spot from the layout preset and passes it to Remotion as `logoBadgeAnchor`; a preset with no backdrop beside the squares drops the badges instead. Person slides are never proposed for anyone in the clip spec's `speakers` list (`gen_broll.drop_on_camera_people`).

### Asset sourcing

Every asset is **public-domain or CC-licensed** and downloaded into the project's `assets/broll/` at generation time:

| Slide type | Source order |
|---|---|
| Logo | Simple Icons (title-verified) → Wikidata P154 → vectorlogo.zone (unverified, last) |
| Person photo | Wikidata entity → its P18 portrait / Commons "depicts" (P180) / its Commons category |
| Stat / text / list | Generated on the fly in the Remotion composition (no asset fetch) |

#### Brand logos are source-verified

Simple Icons is the primary source and is accurate (an audit of all 41 curated brands found zero wrong marks), but it only covers 27 of them, and vectorlogo.zone answers for just 2 more. The other 12 (BlackRock, JPMorgan, Vanguard, Berkshire, Anchorage, Starlink…) silently produced no slide at all.

[`scripts/brand_logos.py`](../scripts/brand_logos.py) adds a verified middle tier, Wikidata's P154 "logo image", taking coverage to 34 of 41 with every mark verified. Guards, each of which caught a real wrong logo during the audit:

- The Simple Icons `<title>` is checked against the brand, so an upstream slug reassignment can't serve someone else's icon.
- Wikidata labels match on a **word boundary**: "JPMorgan" → "JPMorgan Chase" is fine, "Quant" → "Quantico" (the TV series) is not, and neither is "XDC" → "XDCAM".
- The entity must carry company properties (industry, HQ, legal form…), which is what stops "Kraken" resolving to the Colombian metal band of that name.
- P154 claims are read **current-first** by Wikidata rank, not statement order. Otherwise Microsoft resolves to its 1980 wordmark.
- Common-noun brands (`swift`, `circle`, `flare`, `quant`, `anchorage`, `xdc`) never resolve by search at all; only a pinned QID can supply them.

The remaining 7 have no verified mark anywhere and correctly render no slide.

```bash
uv run python scripts/gen_broll.py --audit-brands
```

#### Person photos are identity-verified

Photos are **never** chosen by searching for a name. Searching Commons for "David Schwartz" returns a photo of Anna Schwartz, and `/wiki/David_Schwartz` is an American composer. Neither is Ripple's CTO. So [`scripts/person_photos.py`](../scripts/person_photos.py) resolves the name to a **Wikidata human entity** first (pinned QID where we have one, else `wbsearchentities` filtered to `P31=Q5` and scored against the slide's `role` hint), and only accepts images bound to that entity.

Candidates are ranked to prefer the entity's designated portrait (P18) and solo shots, rejecting signatures, graves, plaques, logos and group photos. **If identity can't be established, the slide is dropped.** A missing cutaway is invisible. A stranger's face is not.

Verified photos are cached repo-wide in `assets/people/` (not per-short), so a person looks identical in every short and one correction sticks everywhere. [`assets/people/people.json`](../assets/people/people.json) is the audit trail: name → QID, Commons filename, and why it was chosen.

The renderer re-verifies too. `render_remotion._merge_broll` hands every auto person slide to `gen_broll.verify_person_slides()`, which resolves the name again (cache first, Wikidata otherwise) and rewrites `src` to the verified file, or drops the slide. Without this, a `broll.auto.json` written before verification existed kept pointing at its keyword-search photo, and a re-render baked it in again: the July 31 batch shipped Anna Schwartz as David Schwartz. Manual `broll.json` slides are not touched.

```bash
uv run python scripts/gen_broll.py --audit-people
```

Prints the resolved identity and chosen photo for every curated person, with alternates. Run it after editing `PERSON_QIDS`. To force a specific Commons file for one person, add it to `PERSON_PHOTO_OVERRIDES`. `--fresh-photo` re-picks past the cache; `--photo-seed` makes the pick reproducible.

To supply your own photo (a person with no free portrait, or a better one than Commons has), save it as `assets/people/manual/<slug>.<jpg|png|webp>` with `<slug>` = the name in lower case, letters and digits only (`davidschwartz.jpg`, `jedmccaleb.png`). `gen_broll._download_person` checks that folder before the cache and before Wikidata, so it wins even against `--fresh-photo`, and the audit lists it as `[manual]`. The manifest records `{"origin": "manual", "file": ...}`. The folder is gitignored with the rest of `assets/people/`; its licensing is the operator's call. Put a `<slug>.json` beside the photo with `source_url`, `image_url`, `license` and `added`, and those fields are copied into the manifest and printed by the audit.

An unpinned name is ranked by notability, not by label. `resolve_identity` asks Wikidata for each candidate's sitelinks (how many Wikimedia projects have a page for it) and scores +1 per five of them, capped at +10; an exact label match is worth one point and the role hint three per matched token. A human with fewer than five sitelinks is eligible only when the role hint matched, so "Michael Saylor" can no longer resolve to a substitute teacher whose label happens to be exact.

Captions and b-roll read the same corrected words. Whisper is prompted with the glossary in `shortsmith/names.py` (`Config.whisper_initial_prompt`, also handed to the WhisperX worker as `WHISPERX_INITIAL_PROMPT`), and `names.fix_words` respells the mishearings that survive, at transcription and again after alignment.

### When a short re-renders

`apply_remotion` decides with a render stamp (`scripts/render_stamp.py`), not a file date. After a render it writes `renders/final_remotion.stamp.json`, a digest of every input: the base render, the clip spec, `words.json`, the manual `broll.json`, the photo state of each person the words name (manifest entry, cached file, manual file), the render code (`render_remotion.py`, `gen_broll.py`, the resolvers, the layout presets, `remotion/src/*`), and the style / platform / captions switches. Before the next render it recomputes the digest; a match skips, a mismatch re-renders and prints which inputs changed. A short with no stamp was rendered before stamps existed and keeps the old rule (newer than its base) until `--force-remotion`; `finalize` reports how many it left alone. A b-roll generation failure no longer passes in silence: it is printed with a `!!` prefix, the render proceeds with the previous list, and the phase summary counts it.

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

- **Node 22+** (`npm` + `npx`; Hyperframes requires 22+)
- One-time: `cd remotion && npm install` (~600 MB; `setup.sh` does this automatically if `npm` is on PATH)
- For Claude b-roll picker: `ANTHROPIC_API_KEY` (uses the same key as step 2 clip selection)

## Tests

All offline, no real HTTP required:

| File | Covers |
|---|---|
| `tests/test_gen_broll_http.py` | the network layer: cache hits, offline, nocache, retry-on-429, fail-fast on 404 |
| `tests/test_person_photos.py` | identity resolution, candidate ranking, the fail-closed contract |
| `tests/test_brand_logos.py` | source order and every wrong-logo guard, named as regressions |
| `tests/test_gen_broll_person_cache.py` | the repo-wide photo cache, and that remote filenames never build local paths |

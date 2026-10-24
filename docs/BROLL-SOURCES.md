# Optional source B-roll

Run these commands from the ShortSmith folder. New sources start off. Existing
people, logos, captions, and sound effects keep their current behavior.

## Choose what runs

`--sources` accepts `all`, `off`, or a comma-separated list:

| Name | What it adds |
| --- | --- |
| `places` | Photos bound to an exact Wikidata place with coordinates and a Commons image license. |
| `images` | Reference images from a checked official page. |
| `press` | Screenshots of original official press or news releases. |
| `tweets` | Screenshots of original public X posts. |

```powershell
# Replace SOURCE_SLUG with the folder name of the source batch.
uv run python scripts/finalize.py --slug SOURCE_SLUG --sources "places,images"
uv run python scripts/finalize.py --slug SOURCE_SLUG --sources "press,tweets"
uv run python scripts/finalize.py --slug SOURCE_SLUG --sources all
uv run python scripts/finalize.py --slug SOURCE_SLUG --sources off
```

The same flag works in `gen_broll.py`, `apply_remotion.py`, and
`render_remotion.py`. `--sources` needs the Remotion phase, so it cannot be used
with `finalize.py --skip-remotion`.

For a saved default, add this to `.env`:

```text
SHORTSMITH_BROLL_SOURCES=places,images,press
```

For a choice on a single short, save `broll.sources.json` inside its project folder:

```json
{
  "enabled": ["places", "images"],
  "items": []
}
```

Order of control: the command flag, then the short's file, then `.env`. An
explicit `--sources off` also works if the short's source file is broken.

Off means no new source search, download, or screenshot. Disabled source slides
are removed from the render even if they exist in an older `broll.auto.json`.
Changing the selected sources invalidates the render cache.

## Automatic discovery

Place names can be found in the transcript without a model call. Web discovery
for places, images, and releases uses `ANTHROPIC_API_KEY` and
`SHORTSMITH_CLAUDE_MODEL`. It uses Anthropic web search and can use paid API credits.
X discovery uses the installed Grok CLI with `grok-build` and low reasoning effort.
On Windows it uses the existing hidden launcher, so console windows do not open.

Missing tools, missing keys, blocked pages, or uncertain source matches are
reported. The normal captions and existing B-roll can still render.
Incomplete source searches are retried when their provider is available again.
Completed searches can use their cached result. Offline runs defer new searches
until an online run.
A retry of one source stage reuses other completed searches, including searches
with no results. The saved results are bound to the transcript and source kinds.

The source must match words in the transcript. Official page checks use the
organization's Wikidata website, the final URL, the canonical page URL, and the
source text. The official host and any tenant path or query must match. A
different subdomain needs its own official website record. Places must have the
exact name or alias, coordinates, and an image
linked to that place. X checks use the original post ID, author, text, and date.

Screenshots show the original page or post. A deleted post, a login page, or a
browser challenge is skipped. Nothing signs in to your accounts. Page capture
uses the browser already installed for Remotion; it does not download a browser.

Each HTTP response is limited to 66,355,200 bytes. Raster images and screenshot
crops are limited to 8,294,400 pixels before decoding or capture. The pixel limit
allows twice the 1080x1920 capture size in each axis, including a 3840x2160 image.
The byte limit is one such frame with four 16-bit color channels. Responses that
use compression despite the requested identity encoding are rejected.
PNG, JPEG, GIF, BMP, TIFF, and WebP headers are checked before image decoding.
Page images with unsupported or invalid headers, or excess size, are blocked;
other page content can still be captured. A blocked main document stops capture.

## Supply a source yourself

Add an item to `broll.sources.json` when you know the correct source. This avoids
having to find it again. The item still has to pass the same source checks.

```json
{
  "enabled": ["places"],
  "items": [
    {"kind": "places", "query": "Eiffel Tower", "qid": "Q243"}
  ]
}
```

Here `query` must occur in the short's transcript. The screenshot or photo is
placed near that phrase in a free gap. You can supply `start` and `end` in seconds
to choose its timing. Other useful fields are `url`, `image_url`, `quote`, `date`,
`entity_name`, `official_domain`, and `license`.

For an official image or release, `entity_name` is the organization name as spoken
in the transcript. `query` can be the longer phrase about the product or event.
Press and X queries need event words that also occur in the source. The company
or account name alone is not enough. Automatic card titles use the checked
transcript phrase.

For official event images and releases, the source quote must include the
company and its claim in the same order as the spoken claim. For example, if
`query` is `announced payments` and `entity_name` is `Acme`, use a source quote
that includes `Acme announced payments`. Checked aliases can stand for the same
company. A different company elsewhere in the clip cannot verify that event.
The final slide time must overlap the chosen mention after fitting a free gap.

## Check the result

Each short keeps:

- `broll.sources.provenance.json`: source links, dates, checks, image hashes, and credits.
- `broll.sources.review.json`: skipped candidates and any reuse rights that need review.
- `assets/broll/sources/`: the captured pages and downloaded image files.

After a successful render, `renders/source-credits.json` lists only source slides
used in that video. Finalize copies it next to the final video as
`<video-name>.sources.json`.

An exact source match does not establish reuse rights. A license supplied by a
model is kept separate from a license found on the source.

`--offline` skips all new discovery and capture. It can reuse local verified
images if the transcript, word times, local source record, and image hashes still
match. A dry run plans the source
step without calling paid providers, fetching pages, or writing source files.

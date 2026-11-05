# Commands

Run these commands from the repository root. The file paths are stable launch
paths. Reusable rendering and batch logic belongs in `shortsmith/rendering/`.
Python code imports that package directly.

| Task | Commands |
| --- | --- |
| Process sources and retry jobs | `batch_pipeline.py`, `batch_transcribe.py`, `ingest_premade.py`, `reprocess_all.py`, `redo_existing.py`, `redo_outdated.py`, `render_all.py` |
| Render and finish shorts | `apply_remotion.py`, `gen_broll.py`, `render_remotion.py`, `finalize.py`, `add_sfx.py` |
| Build sound assets | `build_sfx_index.py`, `build_sfx_pack.py` |
| Prepare and check delivery files | `clean_captions.py`, `make_metadata.py`, `make_thumbnails.py`, `verify_deliverables.py`, `build_metricool_payloads.py` |
| Inspect transcripts and performance | `format_transcripts.py`, `calibrate.py` |
| Check the local code and setup | `smoke_test.py`, `check_no_tokens.py` |

`capture_source.mjs` is the browser worker used by
`shortsmith.rendering.source_capture`. It is not a separate publishing command.

See [the architecture guide](../docs/ARCHITECTURE.md) for the pipeline and
[the main README](../README.md) for command options. Ignored local campaign files
are separate from these shipped commands.

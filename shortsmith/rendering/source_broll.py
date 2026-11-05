"""Coordinate source settings, verified caches, and provenance records."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shortsmith._files import atomic_write
from shortsmith.records import TranscriptWord

from . import source_discovery, source_evidence, source_resolution

KINDS = ("places", "images", "press", "tweets")

MANIFEST = "broll.sources.json"

PROVENANCE = "broll.sources.provenance.json"

REVIEW = "broll.sources.review.json"

log = logging.getLogger(__name__)


def parse_sources(value: str | list[str]) -> frozenset[str]:
    """Parse source switches, rejecting spelling errors instead of ignoring them."""
    if isinstance(value, str):
        parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    elif isinstance(value, list) and all(isinstance(part, str) for part in value):
        parts = [part.strip().lower() for part in value]
    else:
        raise ValueError("B-roll sources enabled must be a CSV string or a list of names")
    if parts == ["all"]:
        return frozenset(KINDS)
    if not parts or parts == ["off"]:
        return frozenset()
    unknown = set(parts) - set(KINDS)
    if unknown:
        raise ValueError(
            f"Unknown B-roll source(s): {', '.join(sorted(unknown))}; use {','.join(KINDS)}, all, or off"
        )
    return frozenset(parts)


def add_source_argument(parser: Any) -> None:
    parser.add_argument(
        "--sources",
        default=None,
        metavar="KINDS",
        type=_source_argument,
        help="New B-roll sources: all, off, or comma-separated places,images,press,tweets. "
        "Overrides saved per-short settings and SHORTSMITH_BROLL_SOURCES. Default off.",
    )


def _source_argument(value: str) -> str:
    try:
        parse_sources(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _manifest(short_dir: Path) -> dict:
    path = short_dir / MANIFEST
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if "enabled" in value:
        parse_sources(value["enabled"])
    items = value.get("items", [])
    if not isinstance(items, list):
        raise ValueError(f"{path}: items must be a list")
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise ValueError(f"{path}: items[{index}] needs a valid kind: {','.join(KINDS)}")
        if not isinstance(item.get("query"), str) or not item["query"].strip():
            raise ValueError(f"{path}: items[{index}] needs a nonempty query")
    return value


def enabled_sources(short_dir: Path, override: str | None = None) -> frozenset[str]:
    """Explicit switch > per-short manifest > environment > off.

    Explicit off is an emergency stop and does not read even a broken manifest.
    """
    if override is not None and not parse_sources(override):
        return frozenset()
    manifest = _manifest(short_dir)
    value = (
        override
        if override is not None
        else manifest.get("enabled", os.environ.get("SHORTSMITH_BROLL_SOURCES", "off"))
    )
    return parse_sources(value)


def filter_slides(slides: list[dict], short_dir: Path, *, sources: str | None = None) -> list[dict]:
    enabled = enabled_sources(short_dir, sources)
    records = []
    transcript = ""
    if enabled and any(
        isinstance(slide, dict) and slide.get("source_kind") in enabled for slide in slides
    ):
        try:
            words = json.loads((short_dir / "assets" / "words.json").read_text(encoding="utf-8"))
            transcript = source_evidence.transcript_digest(words)
            records = _load_provenance_records(short_dir, transcript)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.warning("Source B-roll transcript evidence is unavailable: %s", exc)
    kept = []
    for slide in slides:
        if not isinstance(slide, dict):
            kept.append(slide)
            continue
        if "source_kind" not in slide:
            kept.append(slide)
        elif slide.get("source_kind") in enabled:
            try:
                if slide.get("provenance", {}).get("transcript_sha256") != transcript or not any(
                    record.get("slide") == slide for record in records if isinstance(record, dict)
                ):
                    raise ValueError(
                        "Source slide has no matching current transcript/provenance record"
                    )
                kept.append(
                    _validated_cached_slide(
                        {"slide": slide, "sha256": slide.get("provenance", {}).get("asset_sha256")},
                        short_dir,
                    )
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("Cached source B-roll skipped: %s", exc)
    return kept


def _key(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _raster(path: Path) -> bool:
    data = path.read_bytes()
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def _load_provenance_records(short_dir: Path, transcript: str) -> list[dict]:
    try:
        data = json.loads((short_dir / PROVENANCE).read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("transcript_sha256") != transcript:
            return []
        records = data.get("assets", [])
        return records if isinstance(records, list) else []
    except (OSError, ValueError, AttributeError):
        return []


def _validated_cached_slide(record: dict, short_dir: Path) -> dict:
    slide = record["slide"]
    path = (short_dir / slide["src"]).resolve()
    if not path.is_relative_to(short_dir.resolve()) or not path.is_file() or not _raster(path):
        raise ValueError("Cached source file is missing or outside the project")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError("Cached source file changed since verification")
    if (
        slide.get("source_kind") not in KINDS
        or slide.get("provenance", {}).get("verified") is not True
    ):
        raise ValueError("Cached source has no verification evidence")
    if slide.get("type") != "image" or not slide.get("source_url") or not slide.get("source_label"):
        raise ValueError("Cached source is missing its original attribution")
    if slide.get("source_kind") in {"press", "images"}:
        proof = slide["provenance"]
        names = proof.get("entity_names")
        if (
            not proof.get("context_anchor")
            or not isinstance(proof.get("context_quote"), str)
            or not isinstance(names, list)
            or not names
            or any(not isinstance(name, str) or not name for name in names)
        ):
            raise ValueError("Cached official source needs current subject/claim verification")
    return dict(slide)


def _place_mentions(words: Sequence[TranscriptWord]) -> list[dict]:
    # A capitalized name alone can be a person or company. Require location
    # grammar before proposing it without a semantic discovery provider.
    text = source_evidence.transcript_text(words)
    proper_name = r"[A-Z][a-z]+(?:[ '-][A-Z][a-z]+)*"
    names = re.findall(
        r"\b(?:in|from|to|at|near|across|through|visiting|visit|visited|inside|outside|around)\s+("
        + proper_name
        + r")\b",
        text,
    )
    if re.fullmatch(proper_name, text.strip()):
        names.append(text.strip())
    return [{"kind": "places", "query": name} for name in dict.fromkeys(names)]


def _discovery_state(short_dir: Path, transcript: str) -> dict:
    try:
        data = json.loads((short_dir / REVIEW).read_text(encoding="utf-8"))
        if data.get("transcript_sha256") == transcript and data.get("discovery_version") == 1:
            value = data.get("discovery", {})
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _discover(
    words: Sequence[TranscriptWord], enabled: frozenset[str], review: list[dict], previous: dict
) -> tuple[list[dict], dict]:
    items = _place_mentions(words) if "places" in enabled else []
    found, stages = source_discovery.discover_stages(words, enabled, previous, review)
    return items + found, stages


def should_retry(short_dir: Path, enabled: frozenset[str]) -> bool:
    """Retry incomplete requested work after recovery; leave complete searches cached."""
    if not enabled or os.environ.get("SHORTSMITH_BROLL_OFFLINE", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    try:
        review = json.loads((short_dir / REVIEW).read_text(encoding="utf-8"))
        if (
            not isinstance(review, dict)
            or review.get("version") != 1
            or not isinstance(review.get("items"), list)
        ):
            return True
        if review.get("offline"):
            return True
        for item in review.get("items", []):
            if not isinstance(item, dict):
                return True
            if item.get("status") != "skipped":
                continue
            stage = item.get("stage")
            kinds = item.get(
                "kinds",
                list(enabled - {"tweets"})
                if stage == "web discovery"
                else ["tweets"]
                if stage == "X discovery"
                else [],
            )
            if not enabled.intersection(kinds):
                continue
            retry_when = item.get("retry_when")
            if retry_when is None and stage in {"web discovery", "X discovery"}:
                retry_when = (
                    "web available"
                    if "ANTHROPIC_API_KEY is not set" in item.get("reason", "")
                    else "online"
                )
            if retry_when == "online" or (
                retry_when == "web available" and os.environ.get("ANTHROPIC_API_KEY")
            ):
                return True
    except (OSError, ValueError, AttributeError, TypeError):
        return True  # There is no evidence that the enabled source stages completed.
    return False


def record_no_free_gaps(short_dir: Path) -> None:
    """No source discovery is needed when the video has no cutaway slot."""
    _write_json(
        short_dir / REVIEW,
        {"version": 1, "offline": False, "items": [], "complete": "no free gaps"},
    )


def _write_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def _resolve_source_slide(item: dict, short_dir: Path, timing: tuple[float, float], words: Sequence[TranscriptWord]) -> dict:
    kind = item["kind"]
    dest = short_dir / "assets" / "broll" / "sources" / (kind + "-" + _key(item) + ".png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = (
        source_resolution.resolve_place(item, dest)
        if kind == "places"
        else source_resolution.resolve_post(item, dest)
        if kind == "tweets"
        else source_resolution.resolve_official_source(item, dest, words, timing)
    )
    path = Path(result.pop("path")).resolve()
    if not path.is_relative_to(short_dir.resolve()) or not _raster(path):
        raise ValueError("Resolved source is not a local project raster image")
    return {
        "type": "image",
        "source_kind": kind,
        "start": timing[0],
        "end": timing[1],
        "src": path.relative_to(short_dir.resolve()).as_posix(),
        "title": item["query"],
        "source_label": result["source_label"],
        "source_url": result["source_url"],
        "date": result.get("date", ""),
        "attribution": result.get("attribution", ""),
        "license": result.get("license", ""),
        "provenance": {
            **result,
            "verified": True,
            "asset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "retrieved_at": datetime.now(UTC).isoformat(),
        },
    }


def generate(
    short_dir: Path,
    words: Sequence[TranscriptWord],
    gaps: list[tuple[float, float]],
    *,
    sources: str | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Resolve new slides; failed/uncertain sources are recorded for review.

    Offline only reuses transcript-bound, hash-checked local verified assets.
    Dry run prints the enabled plan and returns no unverified slides.
    """
    enabled = enabled_sources(short_dir, sources)
    if not enabled or not gaps:
        return []
    manifest = _manifest(short_dir)
    items = [item for item in manifest.get("items", []) if item["kind"] in enabled]
    if dry_run:
        print(
            f"  source B-roll plan: {','.join(sorted(enabled))}; {len(items)} explicit candidates; discovery and verification deferred"
        )
        return []
    transcript = source_evidence.transcript_digest(words)
    records = _load_provenance_records(short_dir, transcript)
    offline = os.environ.get("SHORTSMITH_BROLL_OFFLINE", "").lower() in {"1", "true", "yes", "on"}
    review: list[dict] = []
    discovery = _discovery_state(short_dir, transcript)
    if not offline:
        found, discovery = _discover(words, enabled, review, discovery)
        items += found
    items += [
        record["candidate"]
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("candidate"), dict)
        and record["candidate"].get("kind") in enabled
    ]
    cached = {
        _key(record["candidate"]): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("candidate"), dict)
    }
    slides: list[dict] = []
    written: list[dict] = []
    seen: set[str] = set()
    for item in items:
        try:
            if not isinstance(item, dict) or item.get("kind") not in enabled:
                raise ValueError("Invalid discovered source candidate")
            if item.get("_discovery_error"):
                raise ValueError(item["_discovery_error"])
            key = _key(item)
            if key in seen:
                continue
            seen.add(key)
            timing = source_evidence.resolve_source_timing(item, words, gaps)
            if any(timing[0] < s["end"] and timing[1] > s["start"] for s in slides):
                continue
            if key in cached:
                try:
                    slide = _validated_cached_slide(cached[key], short_dir)
                except ValueError:
                    if offline:
                        raise
                    slide = _resolve_source_slide(item, short_dir, timing, words)
                else:
                    if item["kind"] in {"press", "images"}:
                        proof = slide["provenance"]
                        source_evidence.validate_claim_anchor(
                            item, proof["context_quote"], proof["entity_names"], words, timing
                        )
                    slide = {**slide, "start": timing[0], "end": timing[1]}
            elif offline:
                raise ValueError("Offline: no verified local source cache")
            else:
                slide = _resolve_source_slide(item, short_dir, timing, words)
            slide = {
                **slide,
                "provenance": {**slide["provenance"], "transcript_sha256": transcript},
            }
            slides.append(slide)
            written.append(
                {
                    "candidate": item,
                    "slide": slide,
                    "sha256": hashlib.sha256((short_dir / slide["src"]).read_bytes()).hexdigest(),
                }
            )
            if slide["provenance"].get("license_basis") in {"unknown", "user supplied"}:
                review.append(
                    {
                        "candidate": item,
                        "status": "rights review",
                        "reason": "Source match verified; reuse rights are not confirmed by the source",
                    }
                )
        except Exception as exc:
            log.warning("Source B-roll skipped: %s", exc)
            review.append({"candidate": item, "status": "skipped", "reason": str(exc)})
    preserved = [
        record
        for record in records
        if isinstance(record, dict)
        and _key(record.get("candidate", {})) not in {_key(r["candidate"]) for r in written}
    ]
    try:
        _write_json(
            short_dir / PROVENANCE,
            {"version": 1, "transcript_sha256": transcript, "assets": preserved + written},
        )
        _write_json(
            short_dir / REVIEW,
            {
                "version": 1,
                "offline": offline,
                "items": review,
                "transcript_sha256": transcript,
                "discovery_version": 1,
                "discovery": discovery,
            },
        )
    except OSError as exc:
        log.warning("Source B-roll provenance could not be saved: %s", exc)
        return []
    return slides

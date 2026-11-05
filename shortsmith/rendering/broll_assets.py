"""Resolve, verify, cache, and audit brand and person assets."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import random
import re
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shortsmith._files import atomic_write
from shortsmith.config import REPO_ROOT

from . import brand_logos, mentions, person_photos, source_capture, wikidata

UA = "shortsmith/0.5 (+https://github.com/highbaud/shortsmith)"

_CACHE_DIR = REPO_ROOT / ".cache" / "broll-fetch"

_THROTTLE_SECONDS = 0.5

_LAST_FETCH_AT = 0.0  # module-level monotonic clock of last network attempt


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _cache_path_for(url: str) -> Path:
    """Stable on-disk cache key. Suffix preserves extension for inspection."""
    h = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()
    # Keep an extension hint when the URL has one so debug viewers can preview.
    ext = ""
    lower = url.lower().split("?", 1)[0]
    for candidate in (".svg", ".png", ".jpg", ".jpeg", ".webp", ".json"):
        if lower.endswith(candidate):
            ext = candidate
            break
    return _CACHE_DIR / f"{h}{ext}"


def _http_get(url: str, *, max_retries: int = 3) -> bytes | None:
    """Best-effort HTTP GET with on-disk cache, polite throttle, and retry.

    - Cache: every successful response is stored at .cache/broll-fetch/<sha1>.<ext>
      and short-circuits future fetches. Run with SHORTSMITH_BROLL_NOCACHE=1 to
      bypass; delete the directory to invalidate.
    - Throttle: minimum 0.5s between live calls (with jitter). Cached hits skip
      the throttle entirely.
    - Retry: exponential backoff on 429 and 503 (1s, 2s, 4s + jitter), up to
      max_retries. Other HTTP errors fail immediately.
    - Offline: SHORTSMITH_BROLL_OFFLINE=1 disables all live network and returns
      None for any uncached URL.
    """
    global _LAST_FETCH_AT

    # 1. Local cache hit.
    cache_path = _cache_path_for(url)
    if cache_path.exists() and not os.environ.get("SHORTSMITH_BROLL_NOCACHE"):
        try:
            return cache_path.read_bytes()
        except OSError:
            pass  # Fall through to refetch.

    # 2. Offline mode — never hit the network.
    if os.environ.get("SHORTSMITH_BROLL_OFFLINE"):
        return None

    # 3. Polite throttle.
    elapsed = time.monotonic() - _LAST_FETCH_AT
    if elapsed < _THROTTLE_SECONDS:
        time.sleep(_THROTTLE_SECONDS - elapsed + random.uniform(0.0, 0.2))

    # 4. Live fetch with retry on rate-limit / unavailable.
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    data = r.read()
                    _LAST_FETCH_AT = time.monotonic()
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write(cache_path, data)
                    except OSError:
                        pass  # cache write is best-effort
                    return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < max_retries - 1:
                wait = (2**attempt) + random.uniform(0.0, 1.0)
                print(f"    HTTP {e.code} from {url[:60]}; backing off {wait:.1f}s")
                time.sleep(wait)
                continue
            print(f"    fetch failed {url}: HTTP {e.code}")
            break
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            print(f"    fetch failed {url}: {e}")
            break

    _LAST_FETCH_AT = time.monotonic()
    return None


def _download_logo(brand: str, out_dir: Path) -> tuple[str, bool] | None:
    """Return (relative_src, monochrome) or None.

    Source order and the reasoning behind it live in `brand_logos`. Everything
    is returned as full-color (monochrome=False); LogoCard sits the mark on a
    white tile so even dark or brand-colored logos stay visible on the dark
    gradient.
    """
    result = brand_logos.resolve_logo(_http_get, brand)
    if not result:
        return None
    path = out_dir / f"logo-{_slug(brand)}{result.ext}"
    atomic_write(path, result.data)
    print(f"    {brand} -> {result.source}: {result.detail}")
    return (f"assets/broll/{path.name}", False)


def _is_image(raw: bytes) -> bool:
    return (
        raw[:2] == b"\xff\xd8"  # JPEG
        or raw[:8] == b"\x89PNG\r\n\x1a\n"  # PNG
        or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")  # WEBP
    )


def _is_usable_photo(raw: bytes) -> bool:
    """A decodable image header at least person_photos.MIN_WIDTH pixels wide.

    Commons metadata width can be missing, so the downloaded bytes are checked
    against the same floor the candidate ranking uses.
    """
    if not _is_image(raw):
        return False
    try:
        width, _ = source_capture._raster_dimensions(raw)
    except ValueError:
        return False
    return width >= person_photos.MIN_WIDTH


PEOPLE_DIR = REPO_ROOT / "assets" / "people"

PEOPLE_MANIFEST = PEOPLE_DIR / "people.json"

MANUAL_DIR = PEOPLE_DIR / "manual"

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def _read_manifest() -> dict[str, dict]:
    if not PEOPLE_MANIFEST.exists():
        return {}
    try:
        data = json.loads(PEOPLE_MANIFEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_manifest(name: str, entry: dict) -> None:
    """Append one person to the audit trail. Best-effort; never fails a render."""
    try:
        manifest = _read_manifest()
        manifest[name] = entry
        PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write(
            PEOPLE_MANIFEST,
            json.dumps(dict(sorted(manifest.items())), ensure_ascii=False, indent=2) + "\n",
        )
    except OSError:
        pass


def _image_hits(directory: Path, slug: str) -> list[Path]:
    """Photos for `slug` in `directory`, sorted by name.

    Image suffixes only. A <slug>.json note dropped beside a photo would
    otherwise sort first and become the slide `src`, and the stale-file sweep
    in `_download_person` would delete it.
    """
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob(f"{slug}.*") if p.suffix.lower() in IMAGE_SUFFIXES)


def _cached_person_photo(slug: str) -> Path | None:
    hits = _cached_person_photos(slug)
    return hits[0] if hits else None


def _manual_person_photo(slug: str) -> Path | None:
    hits = _image_hits(MANUAL_DIR, slug)
    return hits[0] if hits else None


_PROVENANCE_KEYS = ("source_url", "image_url", "license", "added")


def _manual_provenance(photo: Path) -> dict[str, str]:
    """Where a manual photo came from, read from the <slug>.json beside it."""
    sidecar = photo.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print(f"    ! unreadable provenance sidecar {sidecar.name}; ignoring it")
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(data[k]) for k in _PROVENANCE_KEYS if data.get(k)}


def _cached_person_photos(slug: str) -> list[Path]:
    return _image_hits(PEOPLE_DIR, slug)


def _download_person(
    name: str, out_dir: Path, seed: int | None = None, role_hint: str = "", fresh: bool = False
) -> str | None:
    """Return a short-relative path to a photo VERIFIED to be of `name`, or None.

    Identity is resolved through Wikidata before any image is considered (see
    person_photos), so a name that can't be pinned to a human (or a human with
    no free portrait) yields None and the caller drops the slide. Guessing is
    worse than saying nothing: a stranger's face under a real name is a
    credibility problem the viewer notices and we don't.
    """
    slug = _slug(name)
    manual = _manual_person_photo(slug)
    if manual is not None:
        dest = out_dir / f"person-{slug}{wikidata.file_extension(manual.name)}"
        shutil.copyfile(manual, dest)
        entry = {"origin": "manual", "file": f"manual/{manual.name}", **_manual_provenance(manual)}
        if _read_manifest().get(name) != entry:
            _record_manifest(name, entry)
        print(f"    {name}: manual photo {manual.name}")
        return f"assets/broll/{dest.name}"

    cached = None if fresh else _cached_person_photo(slug)
    if cached is None:
        identity, candidates = person_photos.resolve_photo_candidates(
            _http_get, name, role_hint, seed
        )
        if not identity:
            print(f"    no Wikidata person matches {name!r} (role hint: {role_hint!r})")
            return None
        if not candidates:
            print(f"    {identity.summary()}: no verified free photo")
            return None

        for cand in candidates:
            raw = _http_get(cand.url)
            if raw and _is_usable_photo(raw):
                PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
                cached = PEOPLE_DIR / f"{slug}{wikidata.file_extension(cand.title)}"
                # Clear any prior photo for this person first. A re-pick with a
                # different extension would otherwise leave both files, and the
                # cache lookup, which globs and takes the first, would keep
                # serving the stale one, silently undoing --fresh-photo.
                for stale in _cached_person_photos(slug):
                    if stale != cached:
                        stale.unlink(missing_ok=True)
                atomic_write(cached, raw)
                _record_manifest(
                    name,
                    {
                        "qid": identity.qid,
                        "label": identity.label,
                        "description": identity.description,
                        "commons_file": cand.title,
                        "origin": cand.origin,
                        "score": cand.score,
                        "cached_as": cached.name,
                    },
                )
                print(f"    {identity.qid} {identity.label} -> {cand.title} ({cand.origin})")
                break
        else:
            print(f"    {identity.summary()}: {len(candidates)} candidate(s), none downloadable")
            return None

    dest = out_dir / f"person-{slug}{cached.suffix}"
    if dest.resolve() != cached.resolve():
        shutil.copyfile(cached, dest)
    return f"assets/broll/{dest.name}"


def verify_person_slides(
    slides: list[dict], short_dir: Path, resolve: Callable[..., str | None] | None = None
) -> list[dict]:
    """Re-resolve every person slide's photo through the verified path instead
    of trusting its `src`.

    Slides written before identity verification existed still point at photos
    picked by keyword search (a June short's "David Schwartz" was a photo of
    Anna Schwartz), and a re-render used to bake them in again. The name is
    looked up afresh: the repo-wide cache answers for a known person, an
    unknown one is resolved and cached now, and a person with no verified photo
    loses the slide. `src` is rewritten to the verified file, so a stale copy
    in the short's own assets can never reach the screen. Every other slide
    type passes through untouched.
    """
    resolve = resolve or _download_person
    out_dir = short_dir / "assets" / "broll"
    kept: list[dict] = []
    for slide in slides:
        if slide.get("type") != "person":
            kept.append(slide)
            continue
        name = slide_identity(slide, "person")
        out_dir.mkdir(parents=True, exist_ok=True)
        src = resolve(name, out_dir, role_hint=slide.get("role") or "") if name else None
        if not src:
            print(f"  ! no verified photo for {name!r}; dropping the person slide")
            continue
        if src != slide.get("src"):
            print(f"  person slide {name!r}: {slide.get('src')!r} -> {src!r} (verified)")
        kept.append({**slide, "src": src})
    return kept


_IDENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "text": ("title",),
    "logo": ("brand", "name"),
    "person": ("person", "name"),
}


def missing_identity(slide: dict, kind: str) -> str:
    """The content key this slide lacks, or "" when it can be resolved."""
    if kind == "list":
        items = slide.get("items")
        return "" if isinstance(items, list) and items else "items"
    keys = _IDENTITY_KEYS.get(kind, ())
    if any(slide.get(k) for k in keys):
        return ""
    return " or ".join(keys)


def slide_identity(slide: dict, kind: str) -> Any:
    """What this slide is ABOUT: the first `_IDENTITY_KEYS` value it carries,
    else "". The lookup order is the table's, so the brand/person key wins over
    the shared `name` fallback and every caller agrees on the same answer.

    Returns the payload verbatim, `Any` rather than `str`: the slide came from
    an LLM or a hand-written JSON file, so the value is whatever was in it.
    """
    return next((slide[k] for k in _IDENTITY_KEYS.get(kind, ()) if slide.get(k)), "")


def resolve_assets(
    slides: list[dict],
    short_dir: Path,
    dry_run: bool,
    photo_seed: int | None = None,
    fresh_photo: bool = False,
) -> list[dict]:
    out_dir = short_dir / "assets" / "broll"
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    resolved: list[dict] = []
    for s in slides:
        if s["type"] == "logo":
            # Read, never pop: the key is the only identity a slide with no
            # `name` carries, and broll.auto.json is re-read at render time.
            brand = slide_identity(s, "logo")
            if dry_run:
                s["src"] = f"(would fetch logo: {brand})"
                resolved.append(s)
                continue
            got = _download_logo(brand, out_dir)
            if not got:
                print(f"  ! logo not found for {brand!r}; dropping slide")
                continue
            s["src"], s["monochrome"] = got
            resolved.append(s)
        elif s["type"] == "person":
            person = slide_identity(s, "person")
            if dry_run:
                s["src"] = f"(would fetch photo: {person})"
                resolved.append(s)
                continue
            src = _download_person(
                person, out_dir, seed=photo_seed, role_hint=s.get("role") or "", fresh=fresh_photo
            )
            if not src:
                print(f"  ! no verified photo for {person!r}; dropping slide")
                continue
            s["src"] = src
            resolved.append(s)
        else:
            resolved.append(s)
    return resolved


def audit_people(names: list[str] | None = None) -> int:
    """Print the resolved identity + chosen photo for every curated person.

    This is the eyeball pass: one table showing exactly which human each name
    resolves to and which file would be shown, so a wrong pin is visible before
    it reaches a render. Returns the number of people with no usable photo.
    """
    roster = (
        [(n, "") for n in names]
        if names
        else sorted({(name, role) for name, role in mentions.KNOWN_PEOPLE.values()})
    )
    unusable = 0
    for name, role in roster:
        manual = _manual_person_photo(_slug(name))
        if manual is not None:
            print(
                f"\n{name} [manual]\n  -> {manual.relative_to(PEOPLE_DIR).as_posix()}"
                "  (operator-supplied; beats every other source)"
            )
            prov = _manual_provenance(manual)
            if prov:
                print(
                    f"     source: {prov.get('source_url', '?')}\n"
                    f"     license: {prov.get('license', 'not recorded')}"
                )
            else:
                print("     no provenance recorded (add <slug>.json with source_url and license)")
            continue
        identity, candidates = person_photos.resolve_photo_candidates(_http_get, name, role)
        if not identity:
            print(f"\n{name}\n  UNRESOLVED: no Wikidata human matches (role hint: {role!r})")
            unusable += 1
            continue
        pin = " [pinned]" if identity.pinned else " [searched]"
        print(f"\n{name}{pin}\n  {identity.summary()}")
        if not candidates:
            print("  NO VERIFIED PHOTO: slides for this person will be dropped")
            unusable += 1
            continue
        top = candidates[0]
        print(f"  -> {top.title}  ({top.origin}, score {top.score}, {top.width}px)")
        if top.reasons:
            print(f"     {', '.join(top.reasons)}")
        for alt in candidates[1:4]:
            print(f"     alt [{alt.score}] {alt.origin}: {alt.title}")
    print(f"\n{len(roster)} people, {unusable} with no usable photo.")
    return unusable


def audit_brands(brands: list[str] | None = None) -> int:
    """Print which source answers for every curated brand. Returns the miss count."""
    roster = brands or sorted(set(mentions.KNOWN_BRANDS.values()))
    missing: list[str] = []
    unverified: list[str] = []
    for brand in roster:
        result = brand_logos.resolve_logo(_http_get, brand)
        if not result:
            print(f"  {brand:<22} NO LOGO: slides for this brand will be dropped")
            missing.append(brand)
            continue
        mark = "" if result.is_verified else "   (unverified source)"
        print(f"  {brand:<22} {result.source:<14} {result.detail}{mark}")
        if not result.is_verified:
            unverified.append(brand)
    print(
        f"\n{len(roster)} brands, {len(missing)} with no logo, "
        f"{len(unverified)} from an unverified source."
    )
    if missing:
        print("  no logo: " + ", ".join(missing))
    return len(missing)

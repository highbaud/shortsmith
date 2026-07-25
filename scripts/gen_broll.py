"""Generate auto b-roll cutaway slides for a scaffolded short.

Reads the short's word-level transcript (`assets/words.json`) and the Hyperframes
overlay windows (derived the same way `render_remotion.py` does), then proposes
full-frame b-roll slides that land in the FREE GAPS between overlays.

Two engines:
  * Claude (default when ANTHROPIC_API_KEY is set) — reads the transcript + free
    gaps and proposes stat/text/list/logo/person slides.
  * Heuristic fallback (no key, or --heuristic) — regexes the transcript for
    dollar amounts / percentages -> stat slides, and a small curated map of
    crypto/tech brands & people -> logo/person slides.

For `logo` and `person` slides it downloads the asset into `assets/broll/`:
  * logo  -> a mark whose BRAND is verified, via `brand_logos.py`: a
             title-checked Simple Icons mark, else the Wikidata entity's own
             P154 logo, else (unverified, last) vectorlogo.zone. All sit on a
             white tile at render time so dark marks stay visible.
  * person -> a Creative Commons photo whose subject is VERIFIED, via
             `person_photos.py`: the name is resolved to a Wikidata human
             entity first, and only images bound to that entity (P18 / Commons
             "depicts" / the entity's own category) are eligible. If identity
             can't be established the slide is dropped rather than guessed at.
             Verified photos are cached repo-wide in `assets/people/` so a
             person looks the same in every short; `--fresh-photo` re-picks and
             `--photo-seed` makes the pick reproducible.

Writes `<short>/broll.auto.json`. This is MERGED with any hand-authored
`<short>/broll.json` at render time (manual wins on overlap), so editing the
auto output by hand is safe — re-running regenerates only broll.auto.json.

Usage:
    uv run python scripts/gen_broll.py <short-folder> [options]

Options:
    --heuristic        Force the no-API heuristic engine.
    --max N            Cap the number of slides (default 6).
    --dry-run          Print the proposed slides; don't download or write.
    --fresh-photo      Ignore the repo-wide photo cache and re-pick.
    --audit-people     Resolve every curated person to a Wikidata entity + photo
                       and print the table. Takes no short folder.
    --audit-brands     Same, for brand logos. Takes no short folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Reuse the exact overlay-window derivation the renderer uses, so the free gaps
# we author into match what Hyperframes actually rendered.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import brand_logos  # noqa: E402
import person_photos  # noqa: E402
import wikidata  # noqa: E402
from render_remotion import _overlay_windows, _pick_base, _probe_duration  # noqa: E402

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "gen_broll.md"
GAP_MARGIN = 0.2          # keep slides this far inside each free gap edge
MIN_GAP = 2.4             # ignore free gaps shorter than this (no room for a slide)
# Wikimedia's UA policy asks for a real contact URL; the repo URL is a stable
# pointer back to the project.
UA = "shortsmith/0.5 (+https://github.com/highbaud/shortsmith)"

# --- Network politeness ---
# Cache successful fetches on disk so identical URLs never hit the network twice.
_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "broll-fetch"
# Minimum interval between outbound requests. Wikidata, Wikimedia Commons and
# Simple Icons tolerate ~5 req/s easily but we stay polite at ~2 req/s with
# jitter so a 1000-clip reprocess never trips a rate limit.
_THROTTLE_SECONDS = 0.5
_LAST_FETCH_AT = 0.0  # module-level monotonic clock of last network attempt


# --------------------------------------------------------------------------- #
# Duration / free-gap math
# --------------------------------------------------------------------------- #
def _duration(short_dir: Path) -> float:
    meta = short_dir / "meta.json"
    if meta.exists():
        try:
            d = json.loads(meta.read_text(encoding="utf-8")).get("_shortsmith", {}).get("duration")
            if d:
                return float(d)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return _probe_duration(_pick_base(short_dir, "auto"))


def _free_gaps(overlays: list[dict], duration: float) -> list[tuple[float, float]]:
    """Complement of the overlay windows within [0, duration], minus margins."""
    spans = sorted(((w["start"], w["end"]) for w in overlays), key=lambda x: x[0])
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in spans:
        if a - cursor >= MIN_GAP:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if duration - cursor >= MIN_GAP:
        gaps.append((cursor, duration))
    # apply inner margins
    out = []
    for a, b in gaps:
        a2, b2 = a + GAP_MARGIN, b - GAP_MARGIN
        if b2 - a2 >= MIN_GAP - 2 * GAP_MARGIN:
            out.append((round(a2, 2), round(b2, 2)))
    return out


def _gap_for(t: float, gaps: list[tuple[float, float]]) -> tuple[float, float] | None:
    for a, b in gaps:
        if a <= t <= b:
            return (a, b)
    return None


def _fit_into_gap(start: float, end: float, gaps: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Clamp a desired [start,end] into whichever gap contains its midpoint."""
    mid = (start + end) / 2
    g = _gap_for(mid, gaps) or _gap_for(start, gaps) or _gap_for(end, gaps)
    if not g:
        return None
    ga, gb = g
    dur = min(max(end - start, 2.0), 4.5, gb - ga)
    s = max(ga, min(start, gb - dur))
    return (round(s, 2), round(s + dur, 2))


# --------------------------------------------------------------------------- #
# Transcript helpers
# --------------------------------------------------------------------------- #
def _load_words(short_dir: Path) -> list[dict]:
    p = short_dir / "assets" / "words.json"
    if not p.exists():
        sys.exit(f"No transcript at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _transcript_with_markers(words: list[dict]) -> str:
    parts: list[str] = []
    last = -10.0
    buf: list[str] = []
    for w in words:
        txt = w.get("text") or w.get("word") or ""
        st = float(w["start"])
        if st - last >= 8.0:
            if buf:
                parts.append(" ".join(buf))
                buf = []
            parts.append(f"\n[t={st:.0f}s]")
            last = st
        buf.append(txt)
    if buf:
        parts.append(" ".join(buf))
    return "\n".join(parts).strip()


# --------------------------------------------------------------------------- #
# Claude engine
# --------------------------------------------------------------------------- #
def _parse_json_array(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    a, b = raw.find("["), raw.rfind("]")
    if a == -1 or b == -1:
        raise ValueError(f"No JSON array in model response:\n{raw[:400]}")
    return json.loads(raw[a : b + 1])


def _gen_claude(words: list[dict], gaps: list[tuple[float, float]]) -> list[dict]:
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    model = os.environ.get("SHORTSMITH_CLAUDE_MODEL", "claude-opus-4-7")

    gaps_str = "\n".join(f"  - [{a:.1f}, {b:.1f}]" for a, b in gaps) or "  (none)"
    user = (
        f"FREE GAPS (place slides only inside these, in seconds):\n{gaps_str}\n\n"
        f"TRANSCRIPT:\n{_transcript_with_markers(words)}"
    )
    client = anthropic.Anthropic(api_key=key)
    print(f"  calling Claude ({model}) for b-roll slides...")
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=PROMPT_PATH.read_text(encoding="utf-8"),
        messages=[{"role": "user", "content": user}],
    )
    raw = resp.content[0].text  # type: ignore[attr-defined]
    return _parse_json_array(raw)


# --------------------------------------------------------------------------- #
# Heuristic engine
# --------------------------------------------------------------------------- #
# Small curated maps — only well-known marks/people get auto slides offline.
KNOWN_BRANDS = {
    # Crypto assets / protocols
    "ripple": "Ripple", "xrp": "Ripple", "bitcoin": "Bitcoin", "btc": "Bitcoin",
    "ethereum": "Ethereum", "eth": "Ethereum", "solana": "Solana", "cardano": "Cardano",
    "stellar": "Stellar", "xlm": "Stellar", "hedera": "Hedera", "hbar": "Hedera",
    "chainlink": "Chainlink", "dogecoin": "Dogecoin", "litecoin": "Litecoin",
    "quant": "Quant", "xdc": "XDC", "flare": "Flare",
    # Stablecoins
    "tether": "Tether", "usdt": "Tether", "usdc": "Circle",
    # Exchanges / custodians / brokers
    "coinbase": "Coinbase", "binance": "Binance", "kraken": "Kraken",
    "robinhood": "Robinhood", "anchorage": "Anchorage", "uphold": "Uphold",
    # TradFi / asset managers / payments
    "blackrock": "BlackRock", "microstrategy": "MicroStrategy", "fidelity": "Fidelity",
    "jpmorgan": "JPMorgan", "jp morgan": "JPMorgan", "berkshire": "Berkshire Hathaway",
    "ark invest": "ARK Invest", "vanguard": "Vanguard",
    "paypal": "PayPal", "visa": "Visa", "mastercard": "Mastercard",
    "western union": "Western Union",
    # NOTE: "swift" deliberately omitted — the logo source returns Apple's Swift
    # programming-language icon, not the SWIFT interbank network. Ambiguous brand
    # words that collide with a tech icon (swift, etc.) are left out so we never
    # show a confidently-wrong logo.
    # Big tech
    "tesla": "Tesla", "apple": "Apple", "nvidia": "Nvidia", "microsoft": "Microsoft",
    "amazon": "Amazon", "google": "Google", "meta": "Meta", "facebook": "Meta",
    "spacex": "SpaceX", "starlink": "Starlink",
}
KNOWN_PEOPLE = {
    # Ripple / XRP
    "brad garlinghouse": ("Brad Garlinghouse", "CEO, Ripple"),
    "david schwartz": ("David Schwartz", "CTO, Ripple"),
    "chris larsen": ("Chris Larsen", "Co-founder, Ripple"),
    "jed mccaleb": ("Jed McCaleb", "Co-founder, Ripple"),
    # Crypto founders / execs
    "michael saylor": ("Michael Saylor", "Chairman, MicroStrategy"),
    "vitalik buterin": ("Vitalik Buterin", "Co-founder, Ethereum"),
    "satoshi nakamoto": ("Satoshi Nakamoto", "Creator of Bitcoin"),
    "brian armstrong": ("Brian Armstrong", "CEO, Coinbase"),
    "changpeng zhao": ("Changpeng Zhao", "Founder, Binance"),
    # Wall Street / asset managers / TradFi
    "larry fink": ("Larry Fink", "CEO, BlackRock"),
    "warren buffett": ("Warren Buffett", "CEO, Berkshire Hathaway"),
    "jamie dimon": ("Jamie Dimon", "CEO, JPMorgan"),
    "cathie wood": ("Cathie Wood", "CEO, ARK Invest"),
    "michael burry": ("Michael Burry", "Founder, Scion Capital"),
    "peter schiff": ("Peter Schiff", "Economist & Gold Bull"),
    "jim rickards": ("Jim Rickards", "Economist & Author"),
    # Policy / government
    "donald trump": ("Donald Trump", "President of the United States"),
    "jerome powell": ("Jerome Powell", "Chair, Federal Reserve"),
    "gary gensler": ("Gary Gensler", "Former Chair, SEC"),
    "scott bessent": ("Scott Bessent", "U.S. Treasury Secretary"),
    "nayib bukele": ("Nayib Bukele", "President of El Salvador"),
    # Tech founders
    "elon musk": ("Elon Musk", "CEO, Tesla & SpaceX"),
    "jensen huang": ("Jensen Huang", "CEO, Nvidia"),
    "jeff bezos": ("Jeff Bezos", "Founder, Amazon"),
    "mark zuckerberg": ("Mark Zuckerberg", "CEO, Meta"),
    # Finance personalities / authors
    "kevin o'leary": ("Kevin O'Leary", "Investor, Shark Tank"),
    "robert kiyosaki": ("Robert Kiyosaki", "Author, Rich Dad Poor Dad"),
    "jordan belfort": ("Jordan Belfort", "The Wolf of Wall Street"),
    "patrick bet-david": ("Patrick Bet-David", "Founder, Valuetainment"),
}
def _gen_heuristic(words: list[dict], gaps: list[tuple[float, float]], cap: int) -> list[dict]:
    # NOTE: stat slides are intentionally NOT generated — numbers/stats are left
    # to Hyperframes overlays (its bigstat callouts). The heuristic only emits
    # logo and person cutaways.
    slides: list[dict] = []
    used_gaps: set[tuple[float, float]] = set()
    text_join = " ".join((w.get("text") or w.get("word") or "") for w in words)

    def add(slide: dict, t: float, dur: float = 3.5) -> None:
        if len(slides) >= cap:
            return
        fit = _fit_into_gap(t, t + dur, gaps)
        if not fit or fit in used_gaps:
            return
        slide["start"], slide["end"] = fit
        used_gaps.add(fit)
        slides.append(slide)

    # Brands & people: first mention only.
    lower = text_join.lower()
    seen_brand: set[str] = set()
    for key, brand in KNOWN_BRANDS.items():
        if brand in seen_brand:
            continue
        m = re.search(rf"\b{re.escape(key)}\b", lower)
        if m:
            # locate approximate time of first mention
            t = _approx_time(words, key)
            if t is not None:
                add({"type": "logo", "brand": brand, "name": brand, "mode": "badge"}, t, dur=2.2)
                seen_brand.add(brand)

    for key, (name, role) in KNOWN_PEOPLE.items():
        if key in lower:
            t = _approx_time(words, key)
            if t is not None:
                add({"type": "person", "person": name, "name": name, "role": role, "motion": "in"}, t)

    slides.sort(key=lambda s: s["start"])
    return slides


def _approx_time(words: list[dict], phrase: str) -> float | None:
    toks = phrase.split()
    n = len(toks)
    for i in range(len(words) - n + 1):
        window = " ".join((words[j].get("text") or words[j].get("word") or "").lower().strip(".,!?")
                          for j in range(i, i + n))
        if window == phrase:
            return float(words[i]["start"])
    return None


# --------------------------------------------------------------------------- #
# Asset download (logos / person photos)
# --------------------------------------------------------------------------- #
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _cache_path_for(url: str) -> Path:
    """Stable on-disk cache key. Suffix preserves extension for inspection."""
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
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
                        cache_path.write_bytes(data)
                    except OSError:
                        pass  # cache write is best-effort
                    return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < max_retries - 1:
                wait = (2 ** attempt) + random.uniform(0.0, 1.0)
                print(f"    HTTP {e.code} from {url[:60]}; backing off {wait:.1f}s")
                time.sleep(wait)
                continue
            print(f"    fetch failed {url}: HTTP {e.code}")
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
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
    path.write_bytes(result.data)
    print(f"    {brand} -> {result.source}: {result.detail}")
    return (f"assets/broll/{path.name}", False)


def _is_image(raw: bytes) -> bool:
    return (
        raw[:2] == b"\xff\xd8"  # JPEG
        or raw[:8] == b"\x89PNG\r\n\x1a\n"  # PNG
        or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")  # WEBP
    )


# Verified person photos are cached repo-wide rather than per-short, so a person
# looks the same in every short and one manual correction sticks everywhere.
PEOPLE_DIR = Path(__file__).resolve().parent.parent / "assets" / "people"
PEOPLE_MANIFEST = PEOPLE_DIR / "people.json"


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
        PEOPLE_MANIFEST.write_text(
            json.dumps(dict(sorted(manifest.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _cached_person_photo(slug: str) -> Path | None:
    hits = _cached_person_photos(slug)
    return hits[0] if hits else None


def _cached_person_photos(slug: str) -> list[Path]:
    return sorted(PEOPLE_DIR.glob(f"{slug}.*")) if PEOPLE_DIR.exists() else []


def _download_person(name: str, out_dir: Path, seed: int | None = None,
                     role_hint: str = "", fresh: bool = False) -> str | None:
    """Return a short-relative path to a photo VERIFIED to be of `name`, or None.

    Identity is resolved through Wikidata before any image is considered (see
    person_photos), so a name that can't be pinned to a human (or a human with
    no free portrait) yields None and the caller drops the slide. Guessing is
    worse than saying nothing: a stranger's face under a real name is a
    credibility problem the viewer notices and we don't.
    """
    slug = _slug(name)
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
            if raw and len(raw) > 8000 and _is_image(raw):
                PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
                cached = PEOPLE_DIR / f"{slug}{wikidata.file_extension(cand.title)}"
                # Clear any prior photo for this person first. A re-pick with a
                # different extension would otherwise leave both files, and the
                # cache lookup, which globs and takes the first, would keep
                # serving the stale one, silently undoing --fresh-photo.
                for stale in _cached_person_photos(slug):
                    if stale != cached:
                        stale.unlink(missing_ok=True)
                cached.write_bytes(raw)
                _record_manifest(name, {
                    "qid": identity.qid,
                    "label": identity.label,
                    "description": identity.description,
                    "commons_file": cand.title,
                    "origin": cand.origin,
                    "score": cand.score,
                    "cached_as": cached.name,
                })
                print(f"    {identity.qid} {identity.label} -> {cand.title} ({cand.origin})")
                break
        else:
            print(f"    {identity.summary()}: {len(candidates)} candidate(s), none downloadable")
            return None

    dest = out_dir / f"person-{slug}{cached.suffix}"
    if dest.resolve() != cached.resolve():
        shutil.copyfile(cached, dest)
    return f"assets/broll/{dest.name}"


# --------------------------------------------------------------------------- #
# Normalize + resolve assets
# --------------------------------------------------------------------------- #
# Stat slides are deliberately excluded from auto-generation — numbers are left
# to Hyperframes overlays. (Manual broll.json may still use "stat" directly.)
VALID_TYPES = {"text", "list", "logo", "person"}


def _normalize(slides: list[dict], gaps: list[tuple[float, float]]) -> list[dict]:
    out: list[dict] = []
    for s in slides:
        t = s.get("type")
        if t not in VALID_TYPES:
            continue
        try:
            start, end = float(s["start"]), float(s["end"])
        except (KeyError, ValueError, TypeError):
            continue
        fit = _fit_into_gap(start, end, gaps)
        if not fit:
            print(f"  ! dropping {t} {start}-{end}: not inside any free gap")
            continue
        s = dict(s)
        s["start"], s["end"] = fit
        out.append(s)
    out.sort(key=lambda s: s["start"])
    # drop overlaps (keep earlier)
    deduped: list[dict] = []
    for s in out:
        if deduped and s["start"] < deduped[-1]["end"]:
            continue
        deduped.append(s)
    return deduped


def _resolve_assets(slides: list[dict], short_dir: Path, dry_run: bool,
                    photo_seed: int | None = None,
                    fresh_photo: bool = False) -> list[dict]:
    out_dir = short_dir / "assets" / "broll"
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    resolved: list[dict] = []
    for s in slides:
        if s["type"] == "logo":
            brand = s.pop("brand", None) or s.get("name") or ""
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
            person = s.pop("person", None) or s.get("name") or ""
            if dry_run:
                s["src"] = f"(would fetch photo: {person})"
                resolved.append(s)
                continue
            src = _download_person(person, out_dir, seed=photo_seed,
                                   role_hint=s.get("role") or "", fresh=fresh_photo)
            if not src:
                print(f"  ! no verified photo for {person!r}; dropping slide")
                continue
            s["src"] = src
            resolved.append(s)
        else:
            resolved.append(s)
    return resolved


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def audit_people(names: list[str] | None = None) -> int:
    """Print the resolved identity + chosen photo for every curated person.

    This is the eyeball pass: one table showing exactly which human each name
    resolves to and which file would be shown, so a wrong pin is visible before
    it reaches a render. Returns the number of people with no usable photo.
    """
    roster = ([(n, "") for n in names] if names else
              sorted({(name, role) for name, role in KNOWN_PEOPLE.values()}))
    unusable = 0
    for name, role in roster:
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
    roster = brands or sorted(set(KNOWN_BRANDS.values()))
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
    print(f"\n{len(roster)} brands, {len(missing)} with no logo, "
          f"{len(unverified)} from an unverified source.")
    if missing:
        print("  no logo: " + ", ".join(missing))
    return len(missing)


def generate(short_dir: Path, *, heuristic: bool, cap: int, dry_run: bool,
             photo_seed: int | None = None, fresh_photo: bool = False) -> Path | None:
    short_dir = short_dir.resolve()
    words = _load_words(short_dir)
    duration = _duration(short_dir)
    overlays = _overlay_windows(short_dir, duration)
    gaps = _free_gaps(overlays, duration)

    print(f"{short_dir.name}: {duration:.1f}s, {len(overlays)} overlays, {len(gaps)} free gaps")
    if not gaps:
        print("  no free gaps; nothing to do")
        return None

    use_claude = not heuristic and bool(os.environ.get("ANTHROPIC_API_KEY"))
    if use_claude:
        try:
            raw = _gen_claude(words, gaps)
        except Exception as e:  # noqa: BLE001 - fall back to heuristic
            print(f"  Claude failed ({e}); falling back to heuristic")
            raw = _gen_heuristic(words, gaps, cap)
    else:
        if not heuristic:
            print("  ANTHROPIC_API_KEY not set; using heuristic engine")
        raw = _gen_heuristic(words, gaps, cap)

    slides = _normalize(raw, gaps)[:cap]
    slides = _resolve_assets(slides, short_dir, dry_run, photo_seed=photo_seed,
                             fresh_photo=fresh_photo)

    print(f"  -> {len(slides)} slide(s):")
    for s in slides:
        extra = s.get("value") or s.get("title") or s.get("name") or ""
        print(f"     {s['start']:.1f}-{s['end']:.1f}  {s['type']:6} {extra}")

    if dry_run:
        print(json.dumps(slides, ensure_ascii=False, indent=2))
        return None

    out = short_dir / "broll.auto.json"
    out.write_text(json.dumps(slides, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  wrote {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate auto b-roll slides for a short.")
    ap.add_argument("short_dir", type=Path, nargs="?", help="Path to a short-NN-<slug> folder")
    ap.add_argument("--heuristic", action="store_true", help="Force the no-API heuristic engine")
    ap.add_argument("--max", dest="cap", type=int, default=6, help="Max slides (default 6)")
    ap.add_argument("--dry-run", action="store_true", help="Print proposed slides; don't download/write")
    ap.add_argument("--photo-seed", type=int, default=None,
                    help="Seed the person-photo shuffle for reproducible picks (default: random variety)")
    ap.add_argument("--fresh-photo", action="store_true",
                    help="Ignore the repo-wide assets/people cache and re-pick every person photo")
    ap.add_argument("--audit-people", nargs="*", metavar="NAME", default=None,
                    help="Resolve curated people to Wikidata + photo and print the table, then exit. "
                         "Pass names to audit only those.")
    ap.add_argument("--audit-brands", nargs="*", metavar="BRAND", default=None,
                    help="Resolve curated brands to a logo source and print the table, then exit. "
                         "Pass brands to audit only those.")
    ap.add_argument("--offline", action="store_true",
                    help="Disable all network. Uses on-disk cache only; uncached URLs return nothing.")
    ap.add_argument("--no-cache", action="store_true",
                    help="Bypass the on-disk fetch cache. Every URL re-hits the network.")
    args = ap.parse_args()
    if args.offline:
        os.environ["SHORTSMITH_BROLL_OFFLINE"] = "1"
    if args.no_cache:
        os.environ["SHORTSMITH_BROLL_NOCACHE"] = "1"
    if args.audit_people is not None:
        # Informational report. A few people (Burry, McCaleb) legitimately have
        # no free portrait, so "unusable > 0" is not a failure.
        audit_people(args.audit_people)
        return
    if args.audit_brands is not None:
        audit_brands(args.audit_brands)
        return
    if args.short_dir is None:
        ap.error("short_dir is required (or use --audit-people)")
    generate(args.short_dir, heuristic=args.heuristic, cap=args.cap, dry_run=args.dry_run,
             photo_seed=args.photo_seed, fresh_photo=args.fresh_photo)


if __name__ == "__main__":
    main()

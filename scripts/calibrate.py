"""Close the loop: turn real published-post performance into clip-picking
calibration.

The `viral_score` in find_viral_clips.md is Claude's *taste*. This tool replaces
taste with your audience's *revealed preference*: it joins Metricool analytics
for already-published shorts back to the topics in `scheduled_ledger.json`,
scores each topic by how it actually performed, and writes calibration files
that the find_clips prompt injects on the next run (see
`shortsmith.find_clips._common.performance_block`).

Because a plain script cannot call MCP tools, the analytics are supplied as a
JSON dump that the agent (or a scheduled task) produces via the Metricool MCP —
one array of post rows, or a directory of per-network `get_*_reels/videos`
result files. Field names are matched loosely, so the raw MCP shapes work.

Usage:
    # 1. Agent dumps analytics via Metricool MCP into calibration/analytics/
    #    (get_instagram_reels / get_tiktok_videos / get_youtube_videos /
    #     get_facebook_reels for each brand over the ledger's date range).
    # 2. Join + score + emit:
    uv run python scripts/calibrate.py --analytics calibration/analytics \
        --out calibration
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "scheduled_ledger.json"

_SHORT_SLUG_RE = re.compile(r"__short-\d+-(.+)$")

# Loose field lookups — Metricool's per-network shapes vary; match case-insensitively.
_METRIC_KEYS = {
    "views": ("views", "plays", "videoviews", "impressions", "reach", "playcount"),
    "reach": ("reach", "impressions", "uniqueviews"),
    "likes": ("likes", "likecount", "reactions", "favorites"),
    "comments": ("comments", "commentcount"),
    "shares": ("shares", "sharecount", "reposts"),
    "saves": ("saves", "saved", "bookmarks", "savecount"),
}
_DATE_KEYS = ("date", "publisheddate", "publicationdate", "datetime", "createdtime", "timestamp", "publishedat")
_TITLE_KEYS = ("title", "caption", "text", "description", "message", "name")


def _lc(d: dict) -> dict:
    return {str(k).lower(): v for k, v in d.items()}


def _first(d_lc: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in d_lc and d_lc[k] not in (None, ""):
            return d_lc[k]
    return None


def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_date(v) -> str:
    """Return an ISO date (YYYY-MM-DD) from whatever date-ish value we got."""
    s = str(v)
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    # epoch millis / seconds
    try:
        n = float(s)
        if n > 1e12:
            n /= 1000.0
        return datetime.fromtimestamp(n, tz=UTC).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""


def load_ledger() -> list[dict]:
    """Ledger entries as {slug, topic, brand, date}."""
    if not LEDGER.exists():
        return []
    try:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(ledger, dict):
        print(f"  ! ignoring {LEDGER.name}: expected an object, got {type(ledger).__name__}")
        return []
    out: list[dict] = []
    for brand, brand_map in ledger.items():
        if not isinstance(brand_map, dict):
            continue
        for key, entries in brand_map.items():
            m = _SHORT_SLUG_RE.search(str(key))
            if not m:
                continue
            slug = m.group(1)
            topic = slug.replace("-", " ").strip()
            date = ""
            if isinstance(entries, list) and entries and isinstance(entries[0], dict):
                date = _parse_date(entries[0].get("date", ""))
            out.append({"slug": slug, "topic": topic, "brand": brand, "date": date})
    return out


def _iter_rows(obj):
    """Yield post dicts from a raw MCP result of unknown nesting."""
    if isinstance(obj, list):
        for x in obj:
            yield from _iter_rows(x)
    elif isinstance(obj, dict):
        # A metrics-ish leaf: has a date and at least one metric key.
        d_lc = _lc(obj)
        if _first(d_lc, _DATE_KEYS) is not None and any(
            _first(d_lc, ks) is not None for ks in _METRIC_KEYS.values()
        ):
            yield obj
        else:
            for v in obj.values():
                if isinstance(v, (list, dict)):
                    yield from _iter_rows(v)


def normalize_analytics(raw) -> list[dict]:
    rows: list[dict] = []
    for r in _iter_rows(raw):
        d_lc = _lc(r)
        metrics = {name: _to_float(_first(d_lc, ks)) for name, ks in _METRIC_KEYS.items()}
        rows.append({
            "date": _parse_date(_first(d_lc, _DATE_KEYS)),
            "title": str(_first(d_lc, _TITLE_KEYS) or "").strip(),
            "network": str(d_lc.get("network") or d_lc.get("provider") or "").lower(),
            "metrics": metrics,
        })
    return rows


def _score(metrics: dict) -> float:
    """A single engagement-weighted number. Views dominate (distribution signal);
    saves + shares are the strongest intent signals, weighted up."""
    m = metrics
    return (
        m["views"]
        + 6 * m["saves"]
        + 5 * m["shares"]
        + 2 * m["comments"]
        + 1 * m["likes"]
    )


def match_and_score(ledger: list[dict], analytics: list[dict],
                    date_tol_days: int = 1) -> list[dict]:
    """Attach analytics rows to ledger entries by nearest same-window date,
    aggregate across networks, and score each topic."""
    from datetime import date as _date

    def d(s: str):
        try:
            y, mo, dd = (int(x) for x in s.split("-"))
            return _date(y, mo, dd)
        except (ValueError, AttributeError):
            return None

    per_slug: dict[str, dict] = {}
    for e in ledger:
        per_slug.setdefault(e["slug"], {
            "slug": e["slug"], "topic": e["topic"], "dates": set(),
            "metrics": {k: 0.0 for k in _METRIC_KEYS}, "matched": 0,
        })
        if e["date"]:
            per_slug[e["slug"]]["dates"].add(e["date"])

    ledger_dates = [(e, d(e["date"])) for e in ledger if e["date"]]
    for a in analytics:
        ad = d(a["date"])
        if ad is None:
            continue
        # nearest ledger entry within tolerance
        best = None
        best_gap = date_tol_days + 1
        for e, ed in ledger_dates:
            if ed is None:
                continue
            gap = abs((ed - ad).days)
            if gap <= date_tol_days and gap < best_gap:
                best, best_gap = e, gap
        if best is None:
            continue
        agg = per_slug[best["slug"]]
        for k in _METRIC_KEYS:
            agg["metrics"][k] += a["metrics"][k]
        agg["matched"] += 1

    scored = []
    for s in per_slug.values():
        if s["matched"] == 0:
            continue
        s["score"] = round(_score(s["metrics"]), 1)
        s["dates"] = sorted(s["dates"])
        scored.append(s)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def emit(scored: list[dict], out_dir: Path, top_n: int = 15) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "performance.json").write_text(
        json.dumps(scored, indent=2, ensure_ascii=False), encoding="utf-8")

    top = [s["topic"] for s in scored[:top_n]]
    weak = [s["topic"] for s in scored[-top_n:]][::-1] if len(scored) > top_n else []
    (out_dir / "top_topics.json").write_text(
        json.dumps(top, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "weak_topics.json").write_text(
        json.dumps(weak, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = ["# Clip-picking calibration (from real post performance)", ""]
    lines.append(f"Matched {len(scored)} topics to analytics.\n")
    lines.append("## Top performers (lean toward these angles)\n")
    for s in scored[:top_n]:
        lines.append(f"- **{s['topic']}** - score {s['score']:.0f} "
                     f"({int(s['metrics']['views'])} views, "
                     f"{int(s['metrics']['saves'])} saves, {s['matched']} posts)")
    if weak:
        lines.append("\n## Underperformers (raise the bar; do not rehash)\n")
        for s in scored[-top_n:][::-1]:
            lines.append(f"- {s['topic']} - score {s['score']:.0f} "
                         f"({int(s['metrics']['views'])} views)")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Join Metricool analytics to the ledger and emit clip-picking calibration.")
    ap.add_argument("--analytics", required=True, type=Path,
                    help="JSON file, or a directory of JSON files, of Metricool post analytics.")
    ap.add_argument("--out", type=Path, default=ROOT / "calibration",
                    help="Output directory (default: <repo>/calibration).")
    ap.add_argument("--top-n", type=int, default=15)
    args = ap.parse_args()

    raw_inputs = []
    if args.analytics.is_dir():
        for p in sorted(args.analytics.glob("*.json")):
            try:
                raw_inputs.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                print(f"  ! skipping malformed {p.name}")
    elif args.analytics.exists():
        try:
            raw_inputs.append(json.loads(args.analytics.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            print(f"analytics input is not readable JSON: {args.analytics}")
            return 1
    else:
        print(f"analytics input not found: {args.analytics}")
        return 1

    analytics: list[dict] = []
    for raw in raw_inputs:
        analytics.extend(normalize_analytics(raw))
    ledger = load_ledger()
    if not ledger:
        print("empty/absent ledger — nothing to calibrate against.")
        return 1
    print(f"ledger topics: {len(ledger)}  analytics rows: {len(analytics)}")

    scored = match_and_score(ledger, analytics)
    emit(scored, args.out, top_n=args.top_n)
    print(f"Wrote calibration for {len(scored)} matched topics -> {args.out}")
    if scored:
        print("Top 3:", ", ".join(s["topic"] for s in scored[:3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

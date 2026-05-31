"""Build Metricool createScheduledPost payloads for the consolidated shorts.

Generic + config-driven: NO account/brand ids, handles, media URLs, or
credentials are hard-coded — you supply them at runtime. For each short in the
consolidated `_all/` folder this reads the caption (`<base>.txt`) and metadata
(`<base>.json`), maps it to a media URL you provide, and emits a JSON list of
ready-to-send `createScheduledPost` payloads spread over an N/day cadence.

It does NOT call any API and stores no secrets. Feed the emitted payloads to the
Metricool MCP / API yourself. Brand id, networks, timezone, posting times, and
the filename->URL map are all inputs.

The media URL map is a JSON file of `{"<base>.mp4": "https://...", ...}` — e.g.
public links to the files you uploaded to Google Drive / S3. (Metricool imports
from a linked Google Drive URL automatically.)

Usage:
  uv run python scripts/build_metricool_payloads.py \
      --all-dir path/to/_all \
      --url-map drive_urls.json \
      --blog-id "$SHORTSMITH_METRICOOL_BLOG_ID" \
      --networks facebook,instagram,tiktok,youtube,linkedin,threads \
      --per-day 3 --times 09:00,13:00,18:00 --tz America/Chicago \
      --start 2026-06-02 --out payloads.json

Then post each payload via the Metricool createScheduledPost tool (blogId + the
payload's `date` + `info`). Run with --start a day or two out and verify a couple
in Metricool before sending the whole batch.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

# Networks that require a video + per-network field shapes Metricool expects.
_VIDEO_NETWORKS = {"facebook", "instagram", "tiktok", "youtube", "linkedin", "threads", "twitter"}


def _caption_first_line(text: str) -> str:
    for ln in text.splitlines():
        ln = ln.strip()
        if ln:
            return ln[:100]
    return ""


def _network_data(networks: list[str], title: str, tags: list[str]) -> dict:
    """Build the per-network `*Data` blocks Metricool wants for a video post."""
    nd: dict = {}
    if "facebook" in networks:
        nd["facebookData"] = {"type": "REEL", "title": title}
    if "instagram" in networks:
        nd["instagramData"] = {"type": "REEL", "showReelOnFeed": True}
    if "tiktok" in networks:
        nd["tiktokData"] = {"title": title}
    if "youtube" in networks:
        nd["youtubeData"] = {"title": title, "type": "short", "privacy": "public",
                             "madeForKids": False, "tags": tags[:10]}
    if "linkedin" in networks:
        nd["linkedinData"] = {}
    if "threads" in networks:
        nd["threadsData"] = {}
    if "twitter" in networks:
        nd["twitterData"] = {"tags": []}
    return nd


def _slots(start: date, per_day: int, times: list[str], count: int, tz: str):
    """Yield (iso_datetime, {dateTime, timezone}) for `count` posts."""
    out = []
    d = start
    i = 0
    while len(out) < count:
        for t in times[:per_day]:
            if len(out) >= count:
                break
            hh, mm = t.split(":")
            dt = datetime(d.year, d.month, d.day, int(hh), int(mm))
            iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
            out.append((iso, {"dateTime": iso, "timezone": tz}))
        d += timedelta(days=1)
        i += 1
        if i > 5000:  # safety
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build Metricool createScheduledPost payloads (no secrets).")
    ap.add_argument("--all-dir", required=True, type=Path, help="Consolidated _all/ folder (mp4 + txt + json).")
    ap.add_argument("--url-map", required=True, type=Path, help='JSON {"<base>.mp4": "https://...media url"}.')
    ap.add_argument("--blog-id", default=os.environ.get("SHORTSMITH_METRICOOL_BLOG_ID", ""),
                    help="Metricool brand/blog id (or SHORTSMITH_METRICOOL_BLOG_ID).")
    ap.add_argument("--networks", default=os.environ.get("SHORTSMITH_PUBLISH_NETWORKS",
                    "facebook,instagram,tiktok,youtube,linkedin,threads"))
    ap.add_argument("--per-day", type=int, default=int(os.environ.get("SHORTSMITH_PUBLISH_PER_DAY", "3")))
    ap.add_argument("--times", default=os.environ.get("SHORTSMITH_PUBLISH_TIMES", "09:00,13:00,18:00"))
    ap.add_argument("--tz", default=os.environ.get("SHORTSMITH_PUBLISH_TZ", "America/Chicago"))
    ap.add_argument("--start", required=True, help="First posting date, YYYY-MM-DD.")
    ap.add_argument("--order", choices=["score", "name"], default="score",
                    help="Schedule order: best viral_score first (default) or filename.")
    ap.add_argument("--out", type=Path, default=Path("metricool_payloads.json"))
    args = ap.parse_args()

    if not args.blog_id:
        ap.error("--blog-id (or SHORTSMITH_METRICOOL_BLOG_ID) is required.")
    networks = [n.strip() for n in args.networks.split(",") if n.strip()]
    times = [t.strip() for t in args.times.split(",") if t.strip()]
    url_map = json.loads(args.url_map.read_text(encoding="utf-8"))
    start = datetime.strptime(args.start, "%Y-%m-%d").date()

    # Collect shorts that have a caption + a media URL.
    items = []
    for txt in sorted(args.all_dir.glob("*.txt")):
        base = txt.stem
        media = url_map.get(f"{base}.mp4") or url_map.get(base)
        if not media:
            continue  # only schedule clips that survived curation / have a URL
        caption = txt.read_text(encoding="utf-8").strip()
        meta = {}
        mj = args.all_dir / f"{base}.json"
        if mj.exists():
            try:
                meta = json.loads(mj.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        items.append({
            "base": base,
            "media": media,
            "caption": caption,
            "title": (meta.get("title") or _caption_first_line(caption))[:100] or _caption_first_line(caption),
            "tags": [str(k) for k in (meta.get("keywords") or [])][:10],
            "score": meta.get("viral_score") or 0,
        })

    if args.order == "score":
        items.sort(key=lambda x: (-(x["score"] or 0), x["base"]))

    slots = _slots(start, args.per_day, times, len(items), args.tz)
    payloads = []
    for it, (iso, pub) in zip(items, slots):
        info = {
            "autoPublish": True, "draft": False,
            "text": it["caption"],
            "media": [it["media"]], "mediaAltText": [],
            "shortener": False, "smartLinkData": {"ids": []}, "firstCommentText": "",
            "publicationDate": pub,
            "providers": [{"network": n} for n in networks],
            **_network_data(networks, _caption_first_line(it["caption"]) or it["title"], it["tags"]),
        }
        payloads.append({"blogId": str(args.blog_id), "date": f"{iso}{_tz_offset_hint(args.tz)}",
                         "base": it["base"], "info": info})

    args.out.write_text(json.dumps(payloads, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(payloads)} payloads -> {args.out} "
          f"({args.per_day}/day from {args.start}, networks: {','.join(networks)})")
    if len(items) > len(slots):
        print(f"NOTE: {len(items) - len(slots)} shorts had no slot (raise the range).")
    return 0


def _tz_offset_hint(tz: str) -> str:
    """Best-effort numeric offset for the top-level ISO date string. Metricool
    also gets the explicit timezone in info.publicationDate, which is
    authoritative — this is just a hint, default empty if unknown."""
    return ""  # publicationDate.timezone is authoritative; leave the ISO naive.


if __name__ == "__main__":
    raise SystemExit(main())

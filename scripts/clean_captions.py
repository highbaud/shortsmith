"""Strip hashtags from already-written caption .txt files.

Scaffold now removes hashtags at generation time (shortsmith.scaffold._strip_hashtags),
but caption files written before that change — and any consolidated copies in
`<kit>/renders/_all/` — still carry trailing hashtag blocks. This walks the
auto-shorts tree (and _all/) and rewrites every caption .txt in place.

Idempotent and safe to re-run: a file with no hashtags is left byte-for-byte
unchanged (and not rewritten).

Usage:
    uv run python scripts/clean_captions.py            # clean everything
    uv run python scripts/clean_captions.py --dry-run  # report only
"""
from __future__ import annotations

import argparse

from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT
from shortsmith.scaffold import _strip_hashtags


def _caption_files():
    """Yield every caption .txt: per-source `<short>.txt`, per-project
    `caption.txt`, and consolidated `_all/*.txt`."""
    roots = [AUTO_SHORTS_ROOT, KIT_ROOT / "renders" / "_all"]
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.txt"):
            if p in seen:
                continue
            seen.add(p)
            yield p


def main() -> None:
    ap = argparse.ArgumentParser(description="Strip hashtags from caption .txt files.")
    ap.add_argument("--dry-run", action="store_true", help="Report files that would change; write nothing.")
    args = ap.parse_args()

    changed = scanned = 0
    for p in _caption_files():
        scanned += 1
        try:
            original = p.read_text(encoding="utf-8")
        except Exception as e:  # unreadable / binary — skip
            print(f"  skip {p} ({e})")
            continue
        if "#" not in original:
            continue
        cleaned = _strip_hashtags(original) + "\n"
        if cleaned == original:
            continue
        changed += 1
        print(f"{'WOULD CLEAN' if args.dry_run else 'cleaned'}: {p}")
        if not args.dry_run:
            p.write_text(cleaned, encoding="utf-8")

    verb = "would change" if args.dry_run else "cleaned"
    print(f"\nScanned {scanned} caption files, {verb} {changed}.")


if __name__ == "__main__":
    main()

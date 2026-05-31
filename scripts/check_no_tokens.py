r"""Pre-commit hook: scan staged file contents for token-shaped strings.

Catches credentials that detect-secrets' built-in plugins don't have rules
for — specifically:

  * Metricool OAuth client IDs: `client_[a-f0-9]{32}`
  * Bare Bearer tokens of any flavor: `Bearer [A-Za-z0-9._\-]{30,}`
  * Anthropic API keys (their plugin sometimes misses the URL-embedded form)
  * Generic high-entropy 40+ char hex/base64 strings adjacent to obvious
    secret-y variable names (PASSWORD/TOKEN/SECRET/KEY)

Usage (as a pre-commit local hook, invoked by .pre-commit-config.yaml):
    python scripts/check_no_tokens.py file1 file2 file3

Exits 0 if clean, 1 if any match is found. Prints the offending file +
line + a redacted preview so the user can spot what triggered it without
the script itself leaking the value into its own stdout / CI logs.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Each pattern is (name, compiled regex). Order matters only for output —
# all patterns are tried against every line.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Metricool client ID",  re.compile(r"client_[a-f0-9]{32}\b")),
    ("Anthropic API key",    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key",       re.compile(r"sk-[A-Za-z0-9]{40,}")),
    ("GitHub PAT",           re.compile(r"gh[ps]_[A-Za-z0-9]{30,}")),
    ("GitHub OAuth token",   re.compile(r"gho_[A-Za-z0-9]{30,}")),
    ("Slack bot/user token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("Stripe secret key",    re.compile(r"sk_live_[A-Za-z0-9]{20,}")),
    ("AWS access key ID",    re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Bearer token",         re.compile(r"Bearer\s+[A-Za-z0-9._\-]{30,}")),
    # Machine-specific absolute path (workspace root). Not a secret, but it
    # leaks a local layout and breaks a fresh clone — keep it out of tracked
    # files; use repo-relative paths / env vars instead.
    ("Machine-specific path", re.compile(r"[A-Za-z]:[\\/]Claude Code")),
    # Generic high-entropy keyword + value pair. Looks for VAR_NAME=value
    # patterns where VAR_NAME contains TOKEN / SECRET / PASSWORD / KEY and
    # the value is a long opaque blob. Excludes obvious env-var-name-only
    # references like `os.environ["TOKEN"]` by requiring an `=` or `:` plus
    # a long value, not just whitespace.
    ("Suspicious secret assignment",
     re.compile(r"""(?ix)
        (?:password|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)
        \s*[=:]\s*
        ['"]?
        ([A-Za-z0-9+/=_\-]{30,})
        ['"]?
     """)),
]

# File extensions we never scan (binaries / large media / lockfiles).
SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
             ".mp4", ".mkv", ".webm", ".mov", ".wav", ".mp3",
             ".onnx", ".bin", ".pdf", ".zip", ".tar", ".gz"}

# Exact file basenames to skip (this script itself, the secrets baseline,
# the lock files — all legitimately contain example-shaped patterns).
SKIP_NAMES = {".secrets.baseline", "check_no_tokens.py",
              "uv.lock", "package-lock.json", "yarn.lock"}


def redact(s: str) -> str:
    """Show first 4 + last 4 chars of a string, mask the rest. So `sk-abc...xyz`
    instead of the full secret."""
    if len(s) <= 12:
        return "*" * len(s)
    return f"{s[:4]}...{s[-4:]}"


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return [(line_no, pattern_name, redacted_match)] for any hits."""
    if path.suffix.lower() in SKIP_EXTS or path.name in SKIP_NAMES:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    # Allow an inline opt-out for known-safe lines: comment trailing
    # `# pragma: allowlist secret` (matches detect-secrets convention) or
    # `# noqa: secrets` skips that line.
    OPTOUT = re.compile(r"#\s*(pragma:\s*allowlist\s*secret|noqa:\s*secrets)\b",
                        re.IGNORECASE)

    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if OPTOUT.search(line):
            continue
        for name, pat in PATTERNS:
            m = pat.search(line)
            if m:
                # If the pattern has a capture group, use it; else the whole match.
                snippet = m.group(1) if m.groups() else m.group(0)
                hits.append((i, name, redact(snippet)))
    return hits


def main(argv: list[str]) -> int:
    files = [Path(p) for p in argv if Path(p).is_file()]
    if not files:
        return 0

    total = 0
    for f in files:
        for line_no, pattern_name, snippet in scan_file(f):
            print(f"  {f}:{line_no}  {pattern_name}: {snippet}", file=sys.stderr)
            total += 1

    if total:
        print(
            f"\nERROR: {total} potential secret(s) / machine-specific path(s) found in staged files.\n"
            "  - If the match is a real credential: remove it, rotate the\n"
            "    token, store it in .env (gitignored), and re-stage.\n"
            "  - If it's a false positive (example/test value): add a\n"
            "    trailing `# pragma: allowlist secret` on that line.\n"
            "  - To bypass once (dangerous): commit with --no-verify.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

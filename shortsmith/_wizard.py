"""First-run interactive wizard.

If the user runs `shortsmith run` without configuring SHORTSMITH_CLIP_ENGINE or
SHORTSMITH_STYLE in their environment or .env, ask them once and persist the
answers to .env so they aren't prompted again.

Only runs when:
- stdin is a tty (skip in CI / scripts)
- One of the gated env vars is missing
- The user is about to invoke step 2 (clip selection)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

STYLE_OPTIONS = {
    "1": ("xrp-revolution",
          "Premium high-energy — gold/red/green, Anton display, slam hook, vignette + grain."),
    "2": ("minimal",
          "Clean editorial — Inter only, single yellow accent, no overlays."),
    "3": ("bold",
          "Loud high-contrast — electric yellow + magenta + cyan, oversized hook."),
}

ENGINE_OPTIONS = {
    "1": ("anthropic",
          "Claude API. Best quality (~$0.10-$2.00 per source video). Requires ANTHROPIC_API_KEY."),
    "2": ("ollama",
          "Local LLM (Ollama / LM Studio / vLLM). Free, EXPERIMENTAL, lower quality picks."),
}


def is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def needs_wizard(from_step: int) -> bool:
    """True iff this run will trigger the clip selection step AND env is unset."""
    if from_step > 2:
        return False
    style_set = bool(os.environ.get("SHORTSMITH_STYLE"))
    engine_set = bool(os.environ.get("SHORTSMITH_CLIP_ENGINE"))
    return not (style_set and engine_set)


def run_wizard(repo_root: Path) -> dict[str, str]:
    """Interactively ask the user for clip engine + style. Persist to .env.

    Returns the chosen values. Skipped if not a tty (returns empty dict).
    """
    chosen: dict[str, str] = {}

    if not is_tty():
        # Headless run; do nothing. Defaults will be used.
        return chosen

    click.echo("")
    click.echo("=" * 70)
    click.echo("  First-run setup — shortsmith")
    click.echo("=" * 70)
    click.echo("  These get saved to .env so you won't be asked again.")
    click.echo("  Override anytime with --clip-engine / --style or SHORTSMITH_* env vars.")
    click.echo("")

    if not os.environ.get("SHORTSMITH_CLIP_ENGINE"):
        click.echo("How should shortsmith pick the viral clips from your transcripts?")
        for k, (name, desc) in ENGINE_OPTIONS.items():
            click.echo(f"  [{k}] {name:<14}  {desc}")
        choice = click.prompt("Choice [1]", default="1", show_default=False).strip()
        engine = ENGINE_OPTIONS.get(choice, ENGINE_OPTIONS["1"])[0]
        chosen["SHORTSMITH_CLIP_ENGINE"] = engine
        click.echo(f"  -> {engine}\n")

    if not os.environ.get("SHORTSMITH_STYLE"):
        click.echo("Which visual style preset for the scaffolded shorts?")
        for k, (name, desc) in STYLE_OPTIONS.items():
            click.echo(f"  [{k}] {name:<16}  {desc}")
        choice = click.prompt("Choice [1]", default="1", show_default=False).strip()
        style = STYLE_OPTIONS.get(choice, STYLE_OPTIONS["1"])[0]
        chosen["SHORTSMITH_STYLE"] = style
        click.echo(f"  -> {style}\n")

    if chosen:
        _persist_to_env(repo_root / ".env", chosen)
        click.echo("Saved to .env. Continuing with pipeline...\n")
        click.echo("=" * 70)
        # Reflect into the current process so the running pipeline picks them up
        for k, v in chosen.items():
            os.environ[k] = v

    return chosen


def _persist_to_env(env_path: Path, kv: dict[str, str]) -> None:
    """Append/update entries in .env without clobbering other keys."""
    existing: list[str] = []
    if env_path.exists():
        existing = env_path.read_text(encoding="utf-8").splitlines()

    keys_to_set = set(kv.keys())
    new_lines: list[str] = []
    keys_seen: set[str] = set()
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in keys_to_set:
            new_lines.append(f"{key}={kv[key]}")
            keys_seen.add(key)
        else:
            new_lines.append(line)
    # Append any new keys not present
    for key in keys_to_set - keys_seen:
        new_lines.append(f"{key}={kv[key]}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

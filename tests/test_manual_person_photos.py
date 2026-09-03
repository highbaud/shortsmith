"""The manual person photos must stay tracked, resolvable, and attributed.

These are the people Wikidata has no usable free image for, so unlike the rest of
`assets/people/` they cannot be re-fetched. A clone that loses them does not fail
loudly, it just stops giving those people a b-roll cutaway, which is exactly the
kind of silent regression that is hard to notice in a rendered video.

The checks are data-driven rather than naming individuals, so adding a third
person needs no change here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "assets" / "people" / "manual"
sys.path.insert(0, str(ROOT / "scripts"))
import gen_broll  # noqa: E402

IMAGES = sorted(p for p in MANUAL.glob("*")
                if p.suffix.lower() in gen_broll.IMAGE_SUFFIXES)


def test_manual_photos_are_present():
    """An empty manual/ is the regression: the photos exist to fill a gap."""
    assert IMAGES, f"no manual photos in {MANUAL}; a clone lost them"


@pytest.mark.parametrize("image", IMAGES, ids=lambda p: p.name)
def test_each_photo_is_tracked_by_git(image: Path):
    """Tracked, not ignored. The parent rule ignores `assets/people/*`, so a
    change to the re-include line drops these without any other symptom."""
    out = subprocess.run(["git", "ls-files", "--error-unmatch", str(image)],
                         cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", timeout=30)
    assert out.returncode == 0, f"{image.name} is not tracked by git"


@pytest.mark.parametrize("image", IMAGES, ids=lambda p: p.name)
def test_each_photo_resolves_by_slug(image: Path):
    """`_manual_person_photo` looks up by slug, so the basename is the contract."""
    found = gen_broll._manual_person_photo(image.stem)
    assert found is not None, f"{image.name} does not resolve for slug {image.stem!r}"
    assert found.resolve() == image.resolve()


@pytest.mark.parametrize("image", IMAGES, ids=lambda p: p.name)
def test_each_photo_carries_provenance(image: Path):
    """A third-party photo without a recorded source cannot be credited."""
    sidecar = image.with_suffix(".json")
    assert sidecar.is_file(), f"{image.name} has no .json sidecar"
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    for key in ("source_url", "license"):
        assert meta.get(key), f"{sidecar.name} is missing {key}"
    prov = gen_broll._manual_provenance(image)
    assert prov.get("source_url") == meta["source_url"]


@pytest.mark.parametrize("image", IMAGES, ids=lambda p: p.name)
def test_each_photo_is_credited_in_the_readme(image: Path):
    """These are not covered by the project's MIT license, so each one is
    credited by name in manual/README.md."""
    readme = (MANUAL / "README.md").read_text(encoding="utf-8")
    assert image.name in readme, f"{image.name} is not credited in manual/README.md"

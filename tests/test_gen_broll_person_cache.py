"""Tests for the repo-wide person-photo cache in gen_broll.

The cache is what makes a person look identical across every short, so its
staleness rules matter as much as the download itself.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shortsmith.rendering import broll_assets


@pytest.fixture
def people_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(broll_assets, 'PEOPLE_DIR', tmp_path)
    monkeypatch.setattr(broll_assets, 'PEOPLE_MANIFEST', tmp_path / "people.json")
    monkeypatch.setattr(broll_assets, 'MANUAL_DIR', tmp_path / "manual")
    return tmp_path


def _never_resolve(*args, **kwargs):
    raise AssertionError("Wikidata must not be consulted when a manual photo exists")


def test_manual_photo_beats_wikidata_and_the_cache(
    people_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator-supplied file is the last word: no cache lookup, no network,
    and --fresh-photo cannot re-pick past it."""
    (people_dir / "davidschwartz.jpg").write_bytes(b"cached-from-wikidata")
    manual = people_dir / "manual"
    manual.mkdir()
    (manual / "davidschwartz.jpg").write_bytes(b"operator-choice")
    monkeypatch.setattr(broll_assets.person_photos, "resolve_photo_candidates", _never_resolve)
    out_dir = tmp_path / "short" / "assets" / "broll"
    out_dir.mkdir(parents=True)

    for fresh in (False, True):
        src = broll_assets._download_person("David Schwartz", out_dir, fresh=fresh)
        assert src == "assets/broll/person-davidschwartz.jpg"
        assert (out_dir / "person-davidschwartz.jpg").read_bytes() == b"operator-choice"
    assert broll_assets._read_manifest()["David Schwartz"] == {
        "origin": "manual", "file": "manual/davidschwartz.jpg"}


def test_manual_photo_keeps_its_own_format(people_dir: Path, tmp_path: Path) -> None:
    manual = people_dir / "manual"
    manual.mkdir()
    (manual / "jedmccaleb.png").write_bytes(b"png-bytes")
    out_dir = tmp_path / "broll"
    out_dir.mkdir()
    assert broll_assets._download_person("Jed McCaleb", out_dir) == "assets/broll/person-jedmccaleb.png"


def test_manual_lookup_is_by_slug_and_ignores_non_images(people_dir: Path) -> None:
    manual = people_dir / "manual"
    manual.mkdir()
    (manual / "michaelburry.txt").write_text("notes", encoding="utf-8")
    (manual / "michaelsaylor.jpg").write_bytes(b"x")
    assert broll_assets._manual_person_photo("michaelburry") is None
    assert broll_assets._manual_person_photo("michaelsaylor") is not None
    assert broll_assets._manual_person_photo("nobody") is None


def test_no_cached_photo_returns_none(people_dir: Path) -> None:
    assert broll_assets._cached_person_photo("nobody") is None


def test_cached_photo_is_found_by_slug(people_dir: Path) -> None:
    (people_dir / "cathiewood.jpg").write_bytes(b"x")
    found = broll_assets._cached_person_photo("cathiewood")
    assert found is not None
    assert found.name == "cathiewood.jpg"


def test_slug_lookup_does_not_match_a_different_person(people_dir: Path) -> None:
    (people_dir / "michaelsaylor.png").write_bytes(b"x")
    assert broll_assets._cached_person_photo("michaelburry") is None


def test_regression_stale_extension_does_not_shadow_a_fresh_pick(
    people_dir: Path,
) -> None:
    """--fresh-photo re-picking a different format must fully replace.

    Both files coexisting meant the glob lookup (first match, alphabetical) kept
    serving the old .jpg over the new .png, silently undoing --fresh-photo on
    the next run.
    """
    (people_dir / "person.jpg").write_bytes(b"stale")
    fresh = people_dir / "person.png"

    for stale in broll_assets._cached_person_photos("person"):
        if stale != fresh:
            stale.unlink(missing_ok=True)
    fresh.write_bytes(b"fresh")

    assert [p.name for p in people_dir.iterdir()] == ["person.png"]
    assert broll_assets._cached_person_photo("person").read_bytes() == b"fresh"


@pytest.mark.parametrize("hostile", [
    "../../../../etc/passwd.jpg",
    "..\\..\\windows\\system32\\evil.png",
    "C:/Windows/hosts.jpg",
    "name\x00.jpg",
])
def test_remote_filenames_can_never_reach_the_filesystem(hostile: str) -> None:
    """Commons filenames come from a third party, so they must not build paths.

    Cache paths are composed from _slug(name) plus a whitelisted extension; the
    remote title only ever selects the extension. This pins that property.
    """
    from shortsmith.rendering import wikidata

    slug = broll_assets._slug(hostile)
    assert slug.isalnum() or slug == ""
    assert "/" not in slug and "\\" not in slug and ".." not in slug
    assert wikidata.file_extension(hostile) in (".jpg", ".png", ".webp", ".svg")


def test_manifest_round_trips(people_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(broll_assets, 'PEOPLE_MANIFEST', people_dir / "people.json")
    broll_assets._record_manifest("Cathie Wood", {"qid": "Q104587868"})
    broll_assets._record_manifest("Elon Musk", {"qid": "Q317521"})
    manifest = broll_assets._read_manifest()
    assert manifest["Cathie Wood"]["qid"] == "Q104587868"
    assert list(manifest) == ["Cathie Wood", "Elon Musk"], "entries should stay sorted"


def test_unreadable_manifest_degrades_to_empty(
    people_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt audit trail must never break a render."""
    path = people_dir / "people.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(broll_assets, 'PEOPLE_MANIFEST', path)
    assert broll_assets._read_manifest() == {}


def _png_header(width: int) -> bytes:
    import struct

    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, 600)


def test_downloaded_photo_must_meet_the_ranking_width_floor() -> None:
    from shortsmith.rendering import person_photos

    assert broll_assets._is_usable_photo(_png_header(person_photos.MIN_WIDTH))
    assert not broll_assets._is_usable_photo(_png_header(person_photos.MIN_WIDTH - 1))
    assert not broll_assets._is_usable_photo(b"<html>error</html>" * 1000)

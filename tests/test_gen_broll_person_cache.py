"""Tests for the repo-wide person-photo cache in gen_broll.

The cache is what makes a person look identical across every short, so its
staleness rules matter as much as the download itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gen_broll  # noqa: E402


@pytest.fixture
def people_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(gen_broll, "PEOPLE_DIR", tmp_path)
    return tmp_path


def test_no_cached_photo_returns_none(people_dir: Path) -> None:
    assert gen_broll._cached_person_photo("nobody") is None


def test_cached_photo_is_found_by_slug(people_dir: Path) -> None:
    (people_dir / "cathiewood.jpg").write_bytes(b"x")
    found = gen_broll._cached_person_photo("cathiewood")
    assert found is not None
    assert found.name == "cathiewood.jpg"


def test_slug_lookup_does_not_match_a_different_person(people_dir: Path) -> None:
    (people_dir / "michaelsaylor.png").write_bytes(b"x")
    assert gen_broll._cached_person_photo("michaelburry") is None


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

    for stale in gen_broll._cached_person_photos("person"):
        if stale != fresh:
            stale.unlink(missing_ok=True)
    fresh.write_bytes(b"fresh")

    assert [p.name for p in people_dir.iterdir()] == ["person.png"]
    assert gen_broll._cached_person_photo("person").read_bytes() == b"fresh"


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
    import wikidata

    slug = gen_broll._slug(hostile)
    assert slug.isalnum() or slug == ""
    assert "/" not in slug and "\\" not in slug and ".." not in slug
    assert wikidata.file_extension(hostile) in (".jpg", ".png", ".webp", ".svg")


def test_manifest_round_trips(people_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen_broll, "PEOPLE_MANIFEST", people_dir / "people.json")
    gen_broll._record_manifest("Cathie Wood", {"qid": "Q104587868"})
    gen_broll._record_manifest("Elon Musk", {"qid": "Q317521"})
    manifest = gen_broll._read_manifest()
    assert manifest["Cathie Wood"]["qid"] == "Q104587868"
    assert list(manifest) == ["Cathie Wood", "Elon Musk"], "entries should stay sorted"


def test_unreadable_manifest_degrades_to_empty(
    people_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt audit trail must never break a render."""
    path = people_dir / "people.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(gen_broll, "PEOPLE_MANIFEST", path)
    assert gen_broll._read_manifest() == {}

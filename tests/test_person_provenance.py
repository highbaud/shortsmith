"""Tests for the provenance sidecar beside a manual person photo."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gen_broll  # noqa: E402


@pytest.fixture
def people(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "people"
    (root / "manual").mkdir(parents=True)
    monkeypatch.setattr(gen_broll, "PEOPLE_DIR", root)
    monkeypatch.setattr(gen_broll, "PEOPLE_MANIFEST", root / "people.json")
    monkeypatch.setattr(gen_broll, "MANUAL_DIR", root / "manual")
    return root


def test_sidecar_fields_flow_into_the_manifest(people: Path, tmp_path: Path) -> None:
    (people / "manual" / "davidschwartz.jpg").write_bytes(b"photo")
    (people / "manual" / "davidschwartz.json").write_text(json.dumps({
        "source_url": "https://www.theblock.co/profile/313826/david-schwartz",
        "license": "unknown; operator-supplied",
        "added": "2026-09-03",
        "notes": "not a recorded field",
    }), encoding="utf-8")
    out_dir = tmp_path / "broll"
    out_dir.mkdir()
    gen_broll._download_person("David Schwartz", out_dir)
    entry = gen_broll._read_manifest()["David Schwartz"]
    assert entry["origin"] == "manual"
    assert entry["source_url"] == "https://www.theblock.co/profile/313826/david-schwartz"
    assert entry["license"] == "unknown; operator-supplied"
    assert "notes" not in entry


def test_photo_without_a_sidecar_still_works(people: Path, tmp_path: Path) -> None:
    (people / "manual" / "jedmccaleb.png").write_bytes(b"photo")
    out_dir = tmp_path / "broll"
    out_dir.mkdir()
    assert gen_broll._download_person("Jed McCaleb", out_dir) == "assets/broll/person-jedmccaleb.png"
    assert gen_broll._read_manifest()["Jed McCaleb"] == {"origin": "manual", "file": "manual/jedmccaleb.png"}


def test_unreadable_sidecar_is_ignored_with_a_note(
    people: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (people / "manual" / "jedmccaleb.png").write_bytes(b"photo")
    (people / "manual" / "jedmccaleb.json").write_text("{ not json", encoding="utf-8")
    out_dir = tmp_path / "broll"
    out_dir.mkdir()
    assert gen_broll._download_person("Jed McCaleb", out_dir) is not None
    assert "unreadable provenance sidecar" in capsys.readouterr().out


def test_sidecar_is_never_mistaken_for_the_photo(people: Path) -> None:
    (people / "manual" / "davidschwartz.json").write_text("{}", encoding="utf-8")
    assert gen_broll._manual_person_photo("davidschwartz") is None

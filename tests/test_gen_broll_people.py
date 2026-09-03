"""Tests for how b-roll finds the people a transcript names, and for the
render-time guard that keeps an unverified face off the screen.

All offline: the photo resolver is stubbed wherever a test would otherwise
reach Wikidata.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gen_broll  # noqa: E402
import render_remotion  # noqa: E402


def words(text: str, step: float = 0.4) -> list[dict]:
    """A words.json-shaped transcript, one token every `step` seconds."""
    return [{"text": tok, "start": round(i * step, 2), "end": round((i + 1) * step, 2)}
            for i, tok in enumerate(text.split())]


def names(mentions: list[tuple[str, str, float]]) -> list[str]:
    return [name for name, _role, _t in mentions]


# --------------------------------------------------------------------------- #
# Finding the mention
# --------------------------------------------------------------------------- #
def test_full_name_fires_at_the_first_name() -> None:
    found = gen_broll.find_person_mentions(words("I think Gary Gensler denied it."))
    assert names(found) == ["Gary Gensler"]
    assert found[0][2] == pytest.approx(0.8)


def test_surname_alone_fires_for_an_unambiguous_name() -> None:
    found = gen_broll.find_person_mentions(words("They're hoping that Trump comes out with that."))
    assert names(found) == ["Donald Trump"]


def test_possessive_and_trailing_punctuation_do_not_hide_a_mention() -> None:
    assert names(gen_broll.find_person_mentions(words("That was Gensler's, call."))) == ["Gary Gensler"]


def test_ambiguous_surname_needs_the_full_name() -> None:
    # A real transcript names a "Carl von Schwartz"; he must not become Ripple's CTO.
    assert gen_broll.find_person_mentions(words("our subsidiary Carl von Schwartz heads it up")) == []
    assert names(gen_broll.find_person_mentions(words("David Schwartz has said this"))) == ["David Schwartz"]


def test_surname_after_someone_elses_first_name_is_someone_else() -> None:
    found = gen_broll.find_person_mentions(
        words("The Adventures of Barron Trump and The Last President"))
    assert found == []


def test_first_mention_wins_when_a_short_names_two_trumps() -> None:
    found = gen_broll.find_person_mentions(
        words("I think Trump really could be one. The Adventures of Barron Trump."))
    assert names(found) == ["Donald Trump"]
    assert found[0][2] == pytest.approx(0.8)


@pytest.mark.parametrize("text, expect", [
    ("Sailor lost $6 billion in one quarter", ["Michael Saylor"]),
    ("that he did. Sailor is a front man", ["Michael Saylor"]),
    ("michael sailor is a front man", ["Michael Saylor"]),
    ("every sailor knows the tide", []),
])
def test_asr_mishearing_counts_only_when_it_reads_as_a_name(text: str, expect: list[str]) -> None:
    assert names(gen_broll.find_person_mentions(words(text))) == expect


def test_common_verb_is_not_a_hedge_fund_manager() -> None:
    assert gen_broll.find_person_mentions(words("you need to bury it in your yard")) == []
    assert names(gen_broll.find_person_mentions(words("look at Michael Burry, right?"))) == ["Michael Burry"]


def test_mentions_come_back_earliest_first() -> None:
    found = gen_broll.find_person_mentions(
        words("Bezos still owns 13% and Elon Musk owns more, and Trump too."))
    assert names(found) == ["Jeff Bezos", "Elon Musk", "Donald Trump"]


def test_heuristic_places_one_slide_per_person_at_the_first_mention() -> None:
    transcript = words("Trump said this. Then Trump said that.", step=1.0)
    slides = gen_broll._gen_heuristic(transcript, gaps=[(0.0, 12.0)], cap=6)
    people = [s for s in slides if s["type"] == "person"]
    assert [s["name"] for s in people] == ["Donald Trump"]
    assert people[0]["start"] == pytest.approx(0.0)
    assert people[0]["role"] == "President of the United States"


# --------------------------------------------------------------------------- #
# The render-time guard
# --------------------------------------------------------------------------- #
def test_verify_rewrites_src_to_the_verified_photo(tmp_path: Path) -> None:
    def resolve(name: str, out_dir: Path, role_hint: str = "") -> str:
        return f"assets/broll/person-{gen_broll._slug(name)}.jpg"

    slides = [
        {"type": "person", "name": "Warren Buffett", "src": "assets/broll/old-keyword-pick.jpg",
         "start": 1.0, "end": 4.0},
        {"type": "logo", "name": "Ripple", "src": "assets/broll/logo-ripple.svg",
         "start": 5.0, "end": 7.0},
    ]
    out = gen_broll.verify_person_slides(slides, tmp_path, resolve=resolve)
    assert out[0]["src"] == "assets/broll/person-warrenbuffett.jpg"
    assert out[1] == slides[1], "non-person slides pass through untouched"


def test_verify_drops_a_person_with_no_verified_photo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    slides = [{"type": "person", "name": "David Schwartz",
               "src": "assets/broll/person-davidschwartz.jpg", "start": 1.0, "end": 4.0}]
    assert gen_broll.verify_person_slides(slides, tmp_path, resolve=lambda *a, **k: None) == []
    assert "David Schwartz" in capsys.readouterr().out


def test_verify_passes_the_role_as_the_disambiguation_hint(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def resolve(name: str, out_dir: Path, role_hint: str = "") -> str:
        seen[name] = role_hint
        return "assets/broll/person-x.jpg"

    gen_broll.verify_person_slides(
        [{"type": "person", "name": "David Schwartz", "role": "CTO, Ripple", "start": 1.0, "end": 4.0}],
        tmp_path, resolve=resolve)
    assert seen == {"David Schwartz": "CTO, Ripple"}


def test_merge_verifies_auto_slides_but_leaves_manual_ones_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-authored broll.json is the documented escape hatch for a photo the
    resolver cannot verify (a guest with no Wikidata item), so it must pass
    through as written. Auto slides are re-resolved: a stale src is replaced,
    an unverifiable person is dropped."""
    (tmp_path / "broll.auto.json").write_text(json.dumps([
        {"type": "person", "name": "David Schwartz",
         "src": "assets/broll/person-davidschwartz.jpg", "start": 1.0, "end": 4.0},
        {"type": "person", "name": "Gary Gensler",
         "src": "assets/broll/stale.jpg", "start": 10.0, "end": 13.0},
    ]), encoding="utf-8")
    (tmp_path / "broll.json").write_text(json.dumps([
        {"type": "person", "name": "John Deaton",
         "src": "assets/broll/deaton-handpicked.jpg", "start": 20.0, "end": 23.0},
    ]), encoding="utf-8")

    def fake_download(name: str, out_dir: Path, seed: int | None = None,
                      role_hint: str = "", fresh: bool = False) -> str | None:
        return "assets/broll/person-garygensler.jpg" if name == "Gary Gensler" else None

    monkeypatch.setattr(gen_broll, "_download_person", fake_download)
    merged = render_remotion._merge_broll(tmp_path, None)
    assert [(s["name"], s["src"]) for s in merged] == [
        ("Gary Gensler", "assets/broll/person-garygensler.jpg"),
        ("John Deaton", "assets/broll/deaton-handpicked.jpg"),
    ]

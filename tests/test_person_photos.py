"""Tests for identity-verified person photo resolution.

All offline: `fetch` is a stub serving canned API payloads keyed by URL
substring, so the suite never touches Wikidata or Commons.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import person_photos as pp  # noqa: E402


# --------------------------------------------------------------------------- #
# Fake API
# --------------------------------------------------------------------------- #
def make_fetch(routes: dict[str, dict]):
    """Serve a canned dict for the first route whose key is in the URL."""
    calls: list[str] = []

    def fetch(url: str) -> bytes | None:
        calls.append(url)
        for needle, payload in routes.items():
            if needle in url:
                return json.dumps(payload).encode("utf-8")
        return None

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def entity(qid: str, label: str, *, human: bool = True, desc: str = "",
           image: str | None = None, category: str | None = None,
           occupations: list[str] | None = None,
           employers: list[str] | None = None) -> dict:
    def item_claim(prop: str, ids: list[str]) -> list[dict]:
        return [{"mainsnak": {"datavalue": {"value": {"id": i}}}} for i in ids]

    claims: dict[str, list[dict]] = {}
    if human:
        claims["P31"] = item_claim("P31", ["Q5"])
    if image:
        claims["P18"] = [{"mainsnak": {"datavalue": {"value": image}}}]
    if category:
        claims["P373"] = [{"mainsnak": {"datavalue": {"value": category}}}]
    if occupations:
        claims["P106"] = item_claim("P106", occupations)
    if employers:
        claims["P108"] = item_claim("P108", employers)
    return {
        "labels": {"en": {"value": label}},
        "descriptions": {"en": {"value": desc}},
        "claims": claims,
    }


# --------------------------------------------------------------------------- #
# Candidate scoring: the ranking that decides which face ends up on screen
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title", [
    "Elon Musk signature.svg",
    "Warren Buffett grave.jpg",
    "Jamie Dimon plaque at HQ.jpg",
    "Cathie Wood logo.png",
    "Michael Saylor statue.jpg",
])
def test_rejects_files_that_are_not_photos_of_the_person(title: str) -> None:
    score, reasons = pp.score_candidate(title, "depicts", 1280, "Elon Musk")
    assert score <= -999, reasons


def test_rejects_non_image_extensions() -> None:
    score, _ = pp.score_candidate("Scott Bessent statement.ogg", "depicts", 1280,
                                  "Scott Bessent")
    assert score <= -999


def test_rejects_images_below_min_width() -> None:
    score, _ = pp.score_candidate("Jeff Bezos.jpg", "P18", pp.MIN_WIDTH - 1, "Jeff Bezos")
    assert score <= -999


def test_designated_portrait_outranks_a_same_subject_action_shot() -> None:
    """P18 is Wikidata's chosen representative image; prefer it."""
    p18, _ = pp.score_candidate("Michael Saylor 2022.png", "P18", 1280, "Michael Saylor")
    other, _ = pp.score_candidate("Michael Saylor Keynote Address.jpg", "depicts", 1280,
                                  "Michael Saylor")
    assert p18 > other
    # And by more than the variety band, so the shuffle can never swap them.
    assert p18 - other > pp.VARIETY_BAND


def test_group_photo_is_penalised_below_a_solo_shot() -> None:
    solo, _ = pp.score_candidate("Michael Saylor 2022.jpg", "category", 1280,
                                 "Michael Saylor")
    group, reasons = pp.score_candidate("Christos Marafatsos & Michael Saylor.jpg",
                                        "category", 1280, "Michael Saylor")
    assert group < solo
    assert any("group" in r for r in reasons)


def test_another_known_person_in_the_title_is_penalised() -> None:
    _, reasons = pp.score_candidate("Tim Scott and Scott Bessent.jpg", "depicts", 1280,
                                    "Scott Bessent")
    assert any("group" in r or "another known person" in r for r in reasons)


def test_cropped_derivative_keeps_its_score_despite_two_names() -> None:
    """'(cropped)' on Commons means cropped TO the subject, so it's a solo shot."""
    _, reasons = pp.score_candidate("Larry Fink with Valdis Dombrovskis (cropped).jpg",
                                    "P18", 1280, "Larry Fink")
    assert not any("group" in r for r in reasons)


def test_name_match_does_not_fire_on_a_substring_of_another_word() -> None:
    """'Wood' must not match 'Woodward'; token matching is word-anchored."""
    _, reasons = pp.score_candidate("Bob Woodward at a podium.jpg", "depicts", 1280,
                                    "Cathie Wood")
    assert not any("full name" in r for r in reasons)


# --------------------------------------------------------------------------- #
# Identity resolution: the step that stops a stranger reaching the screen
# --------------------------------------------------------------------------- #
def test_pinned_qid_skips_search_entirely() -> None:
    fetch = make_fetch({
        "wbgetentities": {"entities": {"Q4953945": entity(
            "Q4953945", "Brad Garlinghouse", desc="American businessman")}},
    })
    identity = pp.resolve_identity(fetch, "Brad Garlinghouse", "CEO, Ripple")
    assert identity is not None
    assert identity.qid == "Q4953945"
    assert identity.pinned
    assert not any("wbsearchentities" in c for c in fetch.calls)


def test_known_pseudonym_resolves_to_no_photo() -> None:
    fetch = make_fetch({})
    assert pp.resolve_identity(fetch, "Satoshi Nakamoto", "Creator of Bitcoin") is None
    assert fetch.calls == []


def test_non_humans_are_never_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A company or album sharing the person's name must not resolve."""
    monkeypatch.setattr(pp, "PERSON_QIDS", {})
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q1"}, {"id": "Q2"}]},
        "wbgetentities": {"entities": {
            "Q1": entity("Q1", "Ripple", human=False, desc="payments company"),
            "Q2": entity("Q2", "Ripple", human=False, desc="1990s film"),
        }},
    })
    assert pp.resolve_identity(fetch, "Ripple", "") is None


def test_role_hint_disambiguates_same_named_humans(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact-label match must not beat the person the role hint describes.

    This is the real Michael Saylor failure: a substitute teacher whose label is
    an exact string match outscored the MicroStrategy founder, whose label
    carries a middle initial.
    """
    monkeypatch.setattr(pp, "PERSON_QIDS", {})
    monkeypatch.setattr(pp, "_labels_for", lambda fetch, qids: tuple(
        {"Q90": "Ripple", "Q91": "school"}.get(q, q) for q in qids))
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q_TEACHER"}, {"id": "Q_CTO"}]},
        "wbgetentities": {"entities": {
            # Exact label match, wrong human.
            "Q_TEACHER": entity("Q_TEACHER", "David Schwartz", desc="substitute teacher",
                                employers=["Q91"]),
            # Inexact label, right human. Employer matches the role hint.
            "Q_CTO": entity("Q_CTO", "David J. Schwartz", desc="programmer",
                            employers=["Q90"]),
        }},
    })
    identity = pp.resolve_identity(fetch, "David Schwartz", "CTO, Ripple")
    assert identity is not None
    assert identity.qid == "Q_CTO"


def test_generic_title_words_carry_no_disambiguating_signal() -> None:
    """'CEO'/'founder' describe nearly every candidate, so they're dropped.

    The organisation is what actually disambiguates, so it must survive.
    """
    assert pp._hint_tokens("CEO, Ripple") == ["ripple"]
    assert pp._hint_tokens("Co-founder, Ethereum") == ["ethereum"]
    assert pp._hint_tokens("Former Chair, SEC") == ["sec"]
    assert pp._hint_tokens("CEO, Chairman, Founder") == []


def test_unresolvable_name_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pp, "PERSON_QIDS", {})
    fetch = make_fetch({"wbsearchentities": {"search": []}})
    assert pp.resolve_identity(fetch, "Someone Nobody Indexed", "") is None


# --------------------------------------------------------------------------- #
# Gathering + ordering
# --------------------------------------------------------------------------- #
def _identity(**kw) -> pp.Identity:
    base = {"qid": "Q1", "label": "Cathie Wood", "image": "Cathie Wood ARK Invest Photo.jpg",
            "commons_category": "Cathie Wood"}
    return pp.Identity(**{**base, **kw})


def test_rank_gathers_p18_depicts_and_category_without_duplicates() -> None:
    fetch = make_fetch({
        "haswbstatement": {"query": {"search": [
            {"title": "File:Cathie Wood ARK Invest Photo.jpg"},   # dupe of P18
            {"title": "File:Cathie Wood in 2021.jpg"},
        ]}},
        "categorymembers": {"query": {"categorymembers": [
            {"title": "File:Cathie Wood at a conference.jpg"},
        ]}},
        "prop=imageinfo": {"query": {"pages": {
            "1": {"title": "File:Cathie Wood ARK Invest Photo.jpg",
                  "imageinfo": [{"thumburl": "http://x/a.jpg", "thumbwidth": 1280}]},
            "2": {"title": "File:Cathie Wood in 2021.jpg",
                  "imageinfo": [{"thumburl": "http://x/b.jpg", "thumbwidth": 1280}]},
            "3": {"title": "File:Cathie Wood at a conference.jpg",
                  "imageinfo": [{"thumburl": "http://x/c.jpg", "thumbwidth": 1280}]},
        }}},
    })
    candidates = pp.rank_candidates(fetch, _identity(), "Cathie Wood")
    titles = [c.title for c in candidates]
    assert len(titles) == len(set(titles)), "P18 duplicated by the depicts search"
    assert candidates[0].origin == "P18"


def test_rank_returns_empty_when_entity_has_no_bound_images() -> None:
    """Michael Burry's real case: no P18, no category, nothing depicts him."""
    fetch = make_fetch({
        "haswbstatement": {"query": {"search": []}},
    })
    identity = _identity(image=None, commons_category=None)
    assert pp.rank_candidates(fetch, identity, "Michael Burry") == []


def test_shuffle_never_promotes_a_candidate_from_below_the_band() -> None:
    top = pp.Candidate(title="a.jpg", origin="P18", score=42)
    near = pp.Candidate(title="b.jpg", origin="depicts", score=40)
    far = pp.Candidate(title="c.jpg", origin="depicts", score=4)
    for seed in range(25):
        order = pp.shuffle_within_band([top, near, far], seed=seed)
        assert order[-1] is far
        assert set(order[:2]) == {top, near}


def test_shuffle_is_reproducible_for_a_given_seed() -> None:
    cands = [pp.Candidate(title=f"{i}.jpg", origin="depicts", score=40) for i in range(6)]
    assert (pp.shuffle_within_band(cands, seed=7)
            == pp.shuffle_within_band(cands, seed=7))


def test_manual_override_wins_and_skips_ranking() -> None:
    fetch = make_fetch({
        "wbgetentities": {"entities": {"Q4953945": entity(
            "Q4953945", "Brad Garlinghouse", image="Wrong.jpg")}},
        "prop=imageinfo": {"query": {"pages": {"1": {
            "title": "File:Chosen By Hand.jpg",
            "imageinfo": [{"thumburl": "http://x/chosen.jpg", "thumbwidth": 900}]}}}},
    })
    pp.PERSON_PHOTO_OVERRIDES["Brad Garlinghouse"] = "Chosen By Hand.jpg"
    try:
        _, candidates = pp.resolve_photo_candidates(fetch, "Brad Garlinghouse", "CEO, Ripple")
    finally:
        del pp.PERSON_PHOTO_OVERRIDES["Brad Garlinghouse"]
    assert [c.title for c in candidates] == ["Chosen By Hand.jpg"]
    assert not any("haswbstatement" in c for c in fetch.calls)


def test_file_url_encodes_spaces() -> None:
    url = pp.file_url("Cathie Wood ARK Invest Photo.jpg")
    assert " " not in url
    assert "Cathie_Wood" in url


# --------------------------------------------------------------------------- #
# Fail-closed contract
# --------------------------------------------------------------------------- #
def test_unresolved_person_yields_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: no identity means no photo, not a guess."""
    monkeypatch.setattr(pp, "PERSON_QIDS", {})
    fetch = make_fetch({"wbsearchentities": {"search": []}})
    identity, candidates = pp.resolve_photo_candidates(fetch, "Nobody At All", "")
    assert identity is None
    assert candidates == []

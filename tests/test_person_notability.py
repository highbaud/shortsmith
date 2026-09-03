"""Tests for notability in identity resolution: a person nobody has written
about must not win on an exact label match."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import person_photos as pp  # noqa: E402
from test_person_photos import entity, make_fetch  # noqa: E402


def with_links(e: dict, n: int) -> dict:
    return {**e, "sitelinks": {f"w{i}wiki": {"title": "x"} for i in range(n)}}


def test_the_notable_person_beats_an_exact_label_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real Michael Saylor case: his item is labelled with a middle initial,
    a substitute teacher's is an exact match. Coverage decides, not the label."""
    monkeypatch.setattr(pp, "PERSON_QIDS", {})
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q_NOBODY"}, {"id": "Q_REAL"}]},
        "wbgetentities": {"entities": {
            "Q_NOBODY": entity("Q_NOBODY", "Michael Saylor", desc="substitute teacher",
                               image="Teacher.jpg"),
            "Q_REAL": with_links(entity("Q_REAL", "Michael J. Saylor",
                                        desc="American business executive",
                                        image="Michael Saylor 2022.png"), 40),
        }},
    })
    identity = pp.resolve_identity(fetch, "Michael Saylor", "")
    assert identity is not None
    assert identity.qid == "Q_REAL"
    assert identity.sitelinks == 40
    assert "40 sitelinks" in identity.summary()


def test_a_nobody_alone_does_not_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pp, "PERSON_QIDS", {})
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q_NOBODY"}]},
        "wbgetentities": {"entities": {
            "Q_NOBODY": entity("Q_NOBODY", "Michael Saylor", desc="substitute teacher",
                               image="Teacher.jpg"),
        }},
    })
    assert pp.resolve_identity(fetch, "Michael Saylor", "") is None


def test_a_role_hint_rescues_a_person_with_few_sitelinks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pp, "PERSON_QIDS", {})
    monkeypatch.setattr(pp, "_labels_for", lambda fetch, qids: tuple(
        {"Q90": "Ripple"}.get(q, q) for q in qids))
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q_CTO"}]},
        "wbgetentities": {"entities": {
            "Q_CTO": entity("Q_CTO", "David Schwartz", desc="programmer", employers=["Q90"]),
        }},
    })
    identity = pp.resolve_identity(fetch, "David Schwartz", "CTO, Ripple")
    assert identity is not None and identity.qid == "Q_CTO"


def test_search_asks_wikidata_for_sitelinks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pp, "PERSON_QIDS", {})
    fetch = make_fetch({"wbsearchentities": {"search": [{"id": "Q1"}]},
                        "wbgetentities": {"entities": {}}})
    pp.resolve_identity(fetch, "Anyone", "")
    assert any("sitelinks" in url for url in fetch.calls if "wbgetentities" in url)

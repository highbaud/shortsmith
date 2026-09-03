"""Tests for verified brand-logo resolution.

Offline: `fetch` is a stub serving canned payloads keyed by URL substring.

Several cases below are regressions for wrong logos this module actually
produced during its own build-out audit, named as such so nobody "simplifies"
the guard away later.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brand_logos as bl  # noqa: E402

# Long enough to clear the module's minimum-payload floor, like a real mark.
SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><title>%s</title>'
       b'<path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0z'
       b'm0 22C6.477 22 2 17.523 2 12S6.477 2 12 2s10 4.477 10 10-4.477 10-10 10z"/></svg>')


def make_fetch(routes: dict[str, bytes | dict]):
    calls: list[str] = []

    def fetch(url: str) -> bytes | None:
        calls.append(url)
        for needle, payload in routes.items():
            if needle in url:
                return (json.dumps(payload).encode("utf-8")
                        if isinstance(payload, dict) else payload)
        return None

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def org(label: str, *, desc: str = "", logos: list[dict] | None = None,
        company: bool = True, human: bool = False) -> dict:
    claims: dict[str, list[dict]] = {}
    if human:
        claims["P31"] = [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]
    if company:
        claims["P452"] = [{"mainsnak": {"datavalue": {"value": {"id": "Q1"}}}}]
    if logos:
        claims["P154"] = logos
    return {"labels": {"en": {"value": label}},
            "descriptions": {"en": {"value": desc}}, "claims": claims}


def logo(filename: str, *, rank: str = "normal", ended: bool = False) -> dict:
    claim: dict = {"mainsnak": {"datavalue": {"value": filename}}, "rank": rank}
    if ended:
        claim["qualifiers"] = {"P582": [{}]}
    return claim


# --------------------------------------------------------------------------- #
# Simple Icons
# --------------------------------------------------------------------------- #
def test_simple_icons_is_preferred_when_it_has_the_brand() -> None:
    fetch = make_fetch({"cdn.simpleicons.org/coinbase": SVG % b"Coinbase"})
    result = bl.resolve_logo(fetch, "Coinbase")
    assert result is not None
    assert result.source == "simple-icons"
    assert result.is_verified
    # No Wikidata round-trip when the first source answers.
    assert not any("wikidata" in c for c in fetch.calls)


def test_simple_icons_mark_titled_for_another_brand_is_rejected() -> None:
    """Guards against a slug being reassigned upstream."""
    fetch = make_fetch({"cdn.simpleicons.org/ripple": SVG % b"Rippling"})
    assert bl.from_simple_icons(fetch, "Ripple") is None


def test_ambiguous_brand_words_never_resolve_from_a_slug() -> None:
    """'swift' serves Apple's language, not the interbank network."""
    fetch = make_fetch({"cdn.simpleicons.org/swift": SVG % b"Swift",
                        "vectorlogo.zone": SVG % b"Swift"})
    assert bl.from_simple_icons(fetch, "Swift") is None
    assert bl.from_vectorlogo(fetch, "Swift") is None


# --------------------------------------------------------------------------- #
# Wikidata P154: the tier that recovers BlackRock / JPMorgan
# --------------------------------------------------------------------------- #
def test_wikidata_fills_the_gap_when_simple_icons_has_no_entry() -> None:
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q219635"}]},
        "wbgetentities": {"entities": {"Q219635": org(
            "BlackRock", desc="asset manager", logos=[logo("BlackRock wordmark.svg")])}},
        "Special:FilePath": SVG % b"BlackRock",
    })
    result = bl.resolve_logo(fetch, "BlackRock")
    assert result is not None
    assert result.source == "wikidata-p154"
    assert result.is_verified
    assert result.ext == ".svg"


def test_label_suffix_is_allowed_but_a_substring_is_not() -> None:
    """"JPMorgan" -> "JPMorgan Chase" yes; "Quant" -> "Quantico" no.

    A trailing-word suffix is allowed on purpose, so "Circle of Friends" would
    pass this check too. Common-noun brands like Circle are held back by
    AMBIGUOUS_BRANDS instead, not by the label rule.
    """
    assert bl.label_matches("JPMorgan", "JPMorgan Chase")
    assert bl.label_matches("BlackRock", "BlackRock")
    assert bl.label_matches("Vanguard", "The Vanguard Group") is False
    assert not bl.label_matches("Quant", "Quantico")
    assert not bl.label_matches("XDC", "XDCAM")
    assert "circle" in bl.AMBIGUOUS_BRANDS


def test_regression_quant_does_not_resolve_to_the_tv_series() -> None:
    """Shipped 'Quantico Logo (TV-Serie).jpg' for the token Quant."""
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q19866569"}]},
        "wbgetentities": {"entities": {"Q19866569": org(
            "Quantico", desc="American TV series",
            logos=[logo("Quantico Logo (TV-Serie).jpg")])}},
    })
    assert bl.resolve_logo(fetch, "Quant") is None


def test_regression_kraken_does_not_resolve_to_the_band() -> None:
    """A Colombian metal band is genuinely labelled 'Kraken' on Wikidata.

    Label matching alone can't tell it from the exchange. Only the absence of
    any company property can.
    """
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q6435931"}]},
        "wbgetentities": {"entities": {"Q6435931": org(
            "Kraken", desc="band", logos=[logo("Logo krakenvvv.png")],
            company=False)}},
    })
    assert bl.resolve_logo(fetch, "Kraken") is None


def test_founder_is_never_mistaken_for_the_company() -> None:
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q_PERSON"}]},
        "wbgetentities": {"entities": {"Q_PERSON": org(
            "Tesla", desc="inventor", logos=[logo("Tesla signature.svg")], human=True)}},
    })
    assert bl.from_wikidata(fetch, "Tesla") is None


def test_regression_current_logo_wins_over_the_historical_one() -> None:
    """P154 statements run chronologically; claims[0] was Microsoft's 1980 mark."""
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q2283"}]},
        "wbgetentities": {"entities": {"Q2283": org("Microsoft", logos=[
            logo("Microsoft logo (1980).svg", ended=True),
            logo("Microsoft logo (2012).svg"),
        ])}},
        "Special:FilePath": SVG % b"Microsoft",
    })
    result = bl.from_wikidata(fetch, "Microsoft")
    assert result is not None
    assert "2012" in result.detail


def test_preferred_rank_outranks_statement_order() -> None:
    fetch = make_fetch({
        "wbsearchentities": {"search": [{"id": "Q1"}]},
        "wbgetentities": {"entities": {"Q1": org("Acme", logos=[
            logo("Acme old.svg"),
            logo("Acme new.svg", rank="preferred"),
        ])}},
        "Special:FilePath": SVG % b"Acme",
    })
    result = bl.from_wikidata(fetch, "Acme")
    assert result is not None
    assert "Acme new.svg" in result.detail


def test_pinned_qid_skips_search() -> None:
    fetch = make_fetch({
        "wbgetentities": {"entities": {"Q192314": org(
            "JPMorgan Chase", logos=[logo("Logo of JPMorganChase 2024.svg")])}},
        "Special:FilePath": SVG % b"JPMorgan",
    })
    result = bl.from_wikidata(fetch, "JPMorgan")
    assert result is not None
    assert not any("wbsearchentities" in c for c in fetch.calls)


def test_pin_to_a_company_without_a_logo_yields_nothing() -> None:
    """Fidelity: pinned to the right firm, which has no logo. Better than
    resolving to Fidelity International, a different company."""
    fetch = make_fetch({
        "wbgetentities": {"entities": {"Q1411292": org(
            "Fidelity Investments", desc="financial services")}},
    })
    assert bl.from_wikidata(fetch, "Fidelity") is None


# --------------------------------------------------------------------------- #
# Ordering + fail-closed
# --------------------------------------------------------------------------- #
def test_vectorlogo_is_last_and_flagged_unverified() -> None:
    fetch = make_fetch({"vectorlogo.zone": SVG % b"Amazon"})
    result = bl.resolve_logo(fetch, "SomeBrand")
    assert result is not None
    assert result.source == "vectorlogo.zone"
    assert not result.is_verified


def test_no_source_means_no_logo() -> None:
    assert bl.resolve_logo(make_fetch({}), "Nonexistent Brand") is None


@pytest.mark.parametrize("filename,expected", [
    ("BlackRock wordmark.svg", ".svg"),
    ("Anchorage Digital Logo 2022 December.jpg", ".jpg"),
    ("Logo krakenvvv.png", ".png"),
    ("weird.jpeg", ".jpg"),
])
def test_extension_follows_the_commons_filename(filename: str, expected: str) -> None:
    assert bl._ext_of(filename) == expected


# --- _sniff_ext (the bytes decide the extension, not the filename) ---
@pytest.mark.parametrize("raw, fallback, expected", [
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, ".svg", ".png"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 32, ".svg", ".jpg"),
    (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', ".png", ".svg"),
    (b"GIF89a" + b"\x00" * 32, ".png", ".png"),
])
def test_sniff_ext_reads_the_bytes_not_the_filename(raw: bytes, fallback: str,
                                                    expected: str) -> None:
    """Commons' width-resized URL rasterises vector sources, so a P154 filename
    ending .svg routinely returns PNG bytes. Naming those bytes .svg makes the
    browser refuse the image and fails the whole Remotion render, not just one
    slide. Unrecognised bytes keep the filename's extension."""
    assert bl._sniff_ext(raw, fallback) == expected

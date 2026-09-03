"""Tests for the ASR glossary and the mishearing corrections in shortsmith.names."""
from __future__ import annotations

from shortsmith import names


def words(text: str, step: float = 0.5) -> list[dict]:
    return [{"text": tok, "start": round(i * step, 2), "end": round((i + 1) * step, 2)}
            for i, tok in enumerate(text.split())]


def texts(fixed: list[dict]) -> list[str]:
    return [w["text"] for w in fixed]


def test_prompt_is_one_line_with_the_people_last() -> None:
    prompt = names.initial_prompt()
    assert "\n" not in prompt
    assert prompt.index("XRP") < prompt.index("Michael Saylor")
    assert prompt.rstrip(".").endswith(names.PEOPLE[-1])
    for term in (*names.TERMS, *names.PEOPLE):
        assert term in prompt


def test_unique_mishearing_is_corrected_anywhere_and_keeps_punctuation() -> None:
    assert texts(names.fix_words(words("then ginsler's, agency sued"))) == \
        ["then", "Gensler's,", "agency", "sued"]


def test_real_word_mishearing_needs_a_capital_or_the_first_name() -> None:
    assert texts(names.fix_words(words("Sailor lost $6 billion")))[0] == "Saylor"
    assert texts(names.fix_words(words("michael sailor is a front man")))[1] == "Saylor"
    assert texts(names.fix_words(words("every sailor knows the tide")))[1] == "sailor"


def test_first_only_gate_ignores_a_capital_alone() -> None:
    assert texts(names.fix_words(words("Jamie Diamond runs the bank")))[1] == "Dimon"
    assert texts(names.fix_words(words("Diamond hands all the way")))[0] == "Diamond"


def test_split_name_is_merged_and_spans_both_timings() -> None:
    fixed = names.fix_words(words("Brad Garling house, said it"))
    assert texts(fixed) == ["Brad", "Garlinghouse,", "said", "it"]
    assert fixed[1]["start"] == 0.5
    assert fixed[1]["end"] == 1.5, "the merged word ends where 'house' ended"


def test_merge_needs_a_capital_or_the_first_name() -> None:
    assert texts(names.fix_words(words("the garling house next door"))) == \
        ["the", "garling", "house", "next", "door"]
    assert texts(names.fix_words(words("brad garling house said"))) == \
        ["brad", "Garlinghouse", "said"]


def test_acronyms_get_their_casing_back() -> None:
    assert texts(names.fix_words(words("on the xrpl, Defi and Hbar."))) == \
        ["on", "the", "XRPL,", "DeFi", "and", "HBAR."]


def test_corrected_output_is_a_fixed_point() -> None:
    once = names.fix_words(words("Sailor and ginsler on the xrpl"))
    assert names.fix_words(once) == once


def test_input_is_not_mutated() -> None:
    original = words("ginsler said")
    snapshot = [dict(w) for w in original]
    names.fix_words(original)
    assert original == snapshot


def test_word_key_schema_is_accepted() -> None:
    fixed = names.fix_words([{"word": "Ginsler", "start": 0.0, "end": 0.4}])
    assert fixed[0]["text"] == "Gensler"

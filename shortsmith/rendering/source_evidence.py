"""Validate source claims and align them with retained transcript speech."""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections.abc import Sequence

from shortsmith.records import TranscriptWord

CUTAWAY_DURATION = 3.5

GENERIC_EVENT_WORDS = frozenset(
    (
        "a an the and or of to in on at for with by from as is are was were be been "
        "it its they their this that these those has have had will would can could "
        "said says say announced announces announce announcement announcements "
        "news press release releases released releasing statement statements "
        "today yesterday tomorrow recently new latest official company corporation "
        "launch launched launches launching launch announcement update updates updated "
        "posted post posts tweet tweets tweeted according about"
    ).split()
)

_SUBJECT_AUXILIARIES = frozenset(
    "has have had is are was were will would can could may might just also recently officially".split()
)

_CLAUSE_CHANGES = frozenset("while whereas but although however yet instead".split())

_ENTITY_TOKEN = "_source_entity_"


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"\w+", html.unescape(value).casefold()))


def transcript_text(words: Sequence[TranscriptWord]) -> str:
    return " ".join(str(word.get("text") or word.get("word") or "") for word in words)


def transcript_digest(words: Sequence[TranscriptWord]) -> str:
    entries = [
        {
            "text": normalize_text(str(word.get("text") or word.get("word") or "")),
            "start": word.get("start"),
            "end": word.get("end"),
        }
        for word in words
    ]
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def contains_phrase(phrase: str, text: str) -> bool:
    needle = normalize_text(phrase)
    return bool(needle) and f" {needle} " in f" {normalize_text(text)} "


def transcript_tokens(words: Sequence[TranscriptWord]) -> list[tuple[str, float, float]]:
    return [
        (token, float(word["start"]), float(word["end"]))
        for word in words
        for token in normalize_text(str(word.get("text") or word.get("word") or "")).split()
    ]


def phrase_mentions(
    query: str, tokens: list[tuple[str, float, float]]
) -> list[tuple[float, float]]:
    wanted = query.split()
    return [
        (tokens[i][1], tokens[i + len(wanted) - 1][2])
        for i in range(len(tokens) - len(wanted) + 1)
        if [token[0] for token in tokens[i : i + len(wanted)]] == wanted
    ]


def resolve_source_timing(
    item: dict, words: Sequence[TranscriptWord], gaps: list[tuple[float, float]]
) -> tuple[float, float]:
    query = normalize_text(item.get("query", ""))
    transcript = normalize_text(transcript_text(words))
    if not query or f" {query} " not in f" {transcript} ":
        raise ValueError("query does not occur in the transcript")
    entity_name = normalize_text(item.get("entity_name", item["query"]))
    if not entity_name or f" {entity_name} " not in f" {transcript} ":
        raise ValueError("entity_name does not occur in the transcript")
    mentions = phrase_mentions(query, transcript_tokens(words))
    if "start" in item or "end" in item:
        start, end = float(item["start"]), float(item["end"])
        if not any(start < right and end > left for left, right in mentions):
            raise ValueError("Source times do not overlap the transcript mention")
    else:
        start, end = mentions[0]
    if not all(math.isfinite(t) for t in (start, end)) or end <= start or start < 0:
        raise ValueError("source times must be finite, nonnegative, and end after start")
    for left, right in gaps:
        if left <= (start + end) / 2 <= right:
            if "start" in item:
                timing = max(left, start), min(right, end)
            else:
                duration = min(CUTAWAY_DURATION, right - left)
                begin = max(left, min(start, right - duration))
                timing = begin, begin + duration
            if not any(timing[0] < b and timing[1] > a for a, b in mentions):
                raise ValueError("Final source times do not overlap the transcript mention")
            return timing
    raise ValueError("source mention has no free gap")


def identity_tokens(
    tokens: list[tuple[str, float, float]], names: list[str]
) -> list[tuple[str, float, float]]:
    aliases = sorted(
        {normalize_text(name) for name in names if normalize_text(name)}, key=len, reverse=True
    )
    out = []
    index = 0
    while index < len(tokens):
        alias = next(
            (
                name.split()
                for name in aliases
                if [entry[0] for entry in tokens[index : index + len(name.split())]] == name.split()
            ),
            None,
        )
        if alias:
            out.append((_ENTITY_TOKEN, tokens[index][1], tokens[index + len(alias) - 1][2]))
            index += len(alias)
        else:
            out.append(tokens[index])
            index += 1
    return out


def identity_text(value: str, names: list[str]) -> str:
    tokens = [(token, 0.0, 0.0) for token in normalize_text(value).split()]
    return " ".join(token[0] for token in identity_tokens(tokens, names))


def validate_claim_anchor(
    item: dict, quote: str, names: list[str], words: Sequence[TranscriptWord], timing: tuple[float, float]
) -> str:
    """Require the same literal subject and claim at the selected mention.

    Entity aliases are interchangeable. Mere co-occurrence, even in the same
    sentence, is not evidence that the organization owns another subject's claim.
    """
    query = identity_text(item["query"], names)
    source = identity_text(quote, names)
    if query == _ENTITY_TOKEN and item["kind"] == "images":
        return query  # An entity photo is a reference, not an event claim.
    if _CLAUSE_CHANGES.intersection(query.split()):
        raise ValueError("Source subject/claim anchor crosses different clauses")
    if _ENTITY_TOKEN in query.split():
        anchors = [query] if query.startswith(_ENTITY_TOKEN + " ") else []
    else:
        auxiliaries = "|".join(sorted(_SUBJECT_AUXILIARIES))
        pattern = rf"(?<!\S){_ENTITY_TOKEN}(?: (?:{auxiliaries}))* {re.escape(query)}(?!\S)"
        anchors = re.findall(pattern, source)
    tokens = identity_tokens(transcript_tokens(words), names)
    mentions = phrase_mentions(normalize_text(item["query"]), transcript_tokens(words))
    selected = (
        [span for span in mentions if timing[0] < span[1] and timing[1] > span[0]]
        if "start" in item
        else mentions[:1]
    )
    for anchor in anchors:
        if f" {anchor} " not in f" {source} ":
            continue
        prefix = len(anchor.split()) - len(query.split())
        for index in range(len(tokens) - len(anchor.split()) + 1):
            if (
                " ".join(token[0] for token in tokens[index : index + len(anchor.split())])
                == anchor
            ):
                span = tokens[index + prefix][1], tokens[index + len(anchor.split()) - 1][2]
                if span in selected:
                    return anchor
    raise ValueError("Source subject and claim are not grounded at the selected transcript mention")

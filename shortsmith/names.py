"""Proper nouns the ASR must get right, and the mishearings it produces anyway.

Whisper spells what it hears. On this channel that gives "Sailor" for Saylor,
"Larson" for Larsen and "Garling house" for Garlinghouse, and every caption is
drawn straight from the transcript, so viewers see the misspelling. Two
defenses, both applied at transcription time so every consumer (clip finder,
captions, b-roll detection) sees the same corrected words:

  1. `initial_prompt()`: a glossary handed to Whisper as its prompt, which
     biases decoding toward these spellings. Whisper keeps only the last 224
     tokens of a prompt, so the list stays short and the people go last, where
     they survive any truncation.
  2. `fix_words()`: a correction pass over the word list for the mishearings
     that survive. A mishearing that is not an English word ("ginsler") is
     corrected wherever it appears. One that is a real word ("sailor") is
     corrected only when the transcript capitalized it or the person's first
     name is the previous token.
"""
from __future__ import annotations

import re

TERMS: tuple[str, ...] = (
    "XRP", "XRPL", "XRP Ledger", "Ripple", "RippleNet", "RLUSD", "ODL",
    "Bitcoin", "Ethereum", "Solana", "Hedera", "HBAR", "Stellar", "XLM",
    "Chainlink", "Tether", "USDT", "USDC", "Coinbase", "Binance", "Kraken",
    "Uphold", "Linqto", "Evernorth", "BlackRock", "MicroStrategy",
    "Berkshire Hathaway", "DTCC", "SWIFT", "ISO 20022", "Basel III", "SEC",
    "CFTC", "ETF", "stablecoin", "tokenization", "DeFi", "escrow",
    "Digital Ascension Group",
)
PEOPLE: tuple[str, ...] = (
    "Warren Buffett", "Michael Burry", "Cathie Wood", "Jamie Dimon",
    "Larry Fink", "Jerome Powell", "Jim Rickards", "Peter Schiff",
    "Scott Bessent", "Robert Kiyosaki", "Nayib Bukele", "Changpeng Zhao",
    "Vitalik Buterin", "Jed McCaleb", "Chris Larsen", "David Schwartz",
    "Gary Gensler", "Brad Garlinghouse", "John Deaton", "Michael Saylor",
    "Jake Claver",
)

# Mishearings that are not English words: safe to correct wherever they
# appear. Keys are lower-case tokens; values are the spelling drawn on screen.
ALWAYS: dict[str, str] = {
    "ginsler": "Gensler", "genzler": "Gensler", "gentzler": "Gensler",
    "garlinhouse": "Garlinghouse",
    "sayler": "Saylor",
    "bukeli": "Bukele", "bukelly": "Bukele",
    "kiosaki": "Kiyosaki", "kiyosake": "Kiyosaki",
    "butarin": "Buterin", "buteren": "Buterin", "buterine": "Buterin",
    "deeton": "Deaton", "deaten": "Deaton",
    "klaver": "Claver",
    "mccalib": "McCaleb", "mcaleb": "McCaleb",
    "linkto": "Linqto",
    "xrpl": "XRPL", "hbar": "HBAR", "rlusd": "RLUSD", "usdc": "USDC",
    "usdt": "USDT", "defi": "DeFi", "dtcc": "DTCC", "cftc": "CFTC",
}
# Mishearings that are real words: token -> (spelling, first name, first_only).
# Corrected when the person's first name is the previous token, or, unless
# first_only, when the transcript capitalized the token.
GATED: dict[str, tuple[str, str, bool]] = {
    "sailor": ("Saylor", "michael", False),
    "larson": ("Larsen", "chris", False),
    "diamond": ("Dimon", "jamie", True),
    "sheriff": ("Schiff", "peter", True),
}
# Names Whisper splits in two: (first token, second token) -> (spelling, first
# name). Merged into one word spanning both timings when the first token is
# capitalized or the first name precedes it.
MERGES: dict[tuple[str, str], tuple[str, str]] = {
    ("garling", "house"): ("Garlinghouse", "brad"),
    ("mc", "caleb"): ("McCaleb", "jed"),
    ("bet", "david"): ("Bet-David", "patrick"),
}

_TOKEN = re.compile(
    r"^(?P<pre>[\"'(\[“‘]*)(?P<core>.*?)(?P<suf>(?:'s|’s)?[\"')\]”’.,!?;:]*)$"
)


def initial_prompt() -> str:
    """The glossary handed to Whisper as its prompt. One line, people last."""
    return "Glossary: " + ", ".join(TERMS + PEOPLE) + "."


def _split(token: str) -> tuple[str, str, str]:
    m = _TOKEN.match(token)
    if not m or not m.group("core"):
        return "", token, ""
    return m.group("pre"), m.group("core"), m.group("suf")


def word_text(word: dict) -> str:
    """The spoken text of one transcript word.

    Both key spellings are in the wild: WhisperX writes `word`, this project's
    own schema writes `text`. Everything that reads a transcript goes through
    here so the two spellings are handled in exactly one place.
    """
    return str(word.get("text") or word.get("word") or "")


def fix_words(words: list[dict]) -> list[dict]:
    """Return a new word list with known mishearings respelled.

    Timings are untouched except for a merge, where the merged word runs from
    the first token's start to the second token's end. Punctuation and
    possessives around a token are kept ("Ginsler's," -> "Gensler's,").
    """
    out: list[dict] = []
    prev = ""
    i = 0
    while i < len(words):
        word = words[i]
        pre, core, suf = _split(word_text(word))
        low = core.lower()

        if i + 1 < len(words):
            nxt = words[i + 1]
            _npre, ncore, nsuf = _split(word_text(nxt))
            merge = MERGES.get((low, ncore.lower()))
            if merge and (core[:1].isupper() or prev == merge[1]):
                spelling = merge[0]
                merged = {**word, "text": f"{pre}{spelling}{nsuf}"}
                # The merged word runs to the second token's end. When neither
                # token carries one (a transcript whose words were never
                # aligned), leave the key absent rather than writing a null
                # that every timing consumer would read as a number.
                end = nxt.get("end", word.get("end"))
                if end is not None:
                    merged["end"] = end
                out.append(merged)
                prev = spelling.lower()
                i += 2
                continue

        fixed: str | None = ALWAYS.get(low)
        if fixed is None and low in GATED:
            spelling, first, first_only = GATED[low]
            if prev == first or (not first_only and core[:1].isupper()):
                fixed = spelling
        if fixed is not None and fixed != core:
            out.append({**word, "text": f"{pre}{fixed}{suf}"})
        else:
            out.append(word)
        prev = low
        i += 1
    return out

"""Find curated people and brands in transcript words."""

from __future__ import annotations

from collections.abc import Sequence

from shortsmith.names import word_text
from shortsmith.records import TranscriptWord

KNOWN_BRANDS = {
    # Crypto assets / protocols
    "ripple": "Ripple",
    "xrp": "Ripple",
    "bitcoin": "Bitcoin",
    "btc": "Bitcoin",
    "ethereum": "Ethereum",
    "eth": "Ethereum",
    "solana": "Solana",
    "cardano": "Cardano",
    "stellar": "Stellar",
    "xlm": "Stellar",
    "hedera": "Hedera",
    "hbar": "Hedera",
    "chainlink": "Chainlink",
    "dogecoin": "Dogecoin",
    "litecoin": "Litecoin",
    "quant": "Quant",
    "xdc": "XDC",
    "flare": "Flare",
    # Stablecoins
    "tether": "Tether",
    "usdt": "Tether",
    "usdc": "Circle",
    # Exchanges / custodians / brokers
    "coinbase": "Coinbase",
    "binance": "Binance",
    "kraken": "Kraken",
    "robinhood": "Robinhood",
    "anchorage": "Anchorage",
    "uphold": "Uphold",
    # TradFi / asset managers / payments
    "blackrock": "BlackRock",
    "microstrategy": "MicroStrategy",
    "fidelity": "Fidelity",
    "jpmorgan": "JPMorgan",
    "jp morgan": "JPMorgan",
    "berkshire": "Berkshire Hathaway",
    "ark invest": "ARK Invest",
    "vanguard": "Vanguard",
    "paypal": "PayPal",
    "visa": "Visa",
    "mastercard": "Mastercard",
    "western union": "Western Union",
    # NOTE: "swift" deliberately omitted — the logo source returns Apple's Swift
    # programming-language icon, not the SWIFT interbank network. Ambiguous brand
    # words that collide with a tech icon (swift, etc.) are left out so we never
    # show a confidently-wrong logo.
    # Big tech
    "tesla": "Tesla",
    "apple": "Apple",
    "nvidia": "Nvidia",
    "microsoft": "Microsoft",
    "amazon": "Amazon",
    "google": "Google",
    "meta": "Meta",
    "facebook": "Meta",
    "spacex": "SpaceX",
    "starlink": "Starlink",
}

KNOWN_PEOPLE = {
    # Ripple / XRP
    "brad garlinghouse": ("Brad Garlinghouse", "CEO, Ripple"),
    "david schwartz": ("David Schwartz", "CTO, Ripple"),
    "chris larsen": ("Chris Larsen", "Co-founder, Ripple"),
    "jed mccaleb": ("Jed McCaleb", "Co-founder, Ripple"),
    # Crypto founders / execs
    "michael saylor": ("Michael Saylor", "Chairman, MicroStrategy"),
    "vitalik buterin": ("Vitalik Buterin", "Co-founder, Ethereum"),
    "satoshi nakamoto": ("Satoshi Nakamoto", "Creator of Bitcoin"),
    "brian armstrong": ("Brian Armstrong", "CEO, Coinbase"),
    "changpeng zhao": ("Changpeng Zhao", "Founder, Binance"),
    # Wall Street / asset managers / TradFi
    "larry fink": ("Larry Fink", "CEO, BlackRock"),
    "warren buffett": ("Warren Buffett", "CEO, Berkshire Hathaway"),
    "jamie dimon": ("Jamie Dimon", "CEO, JPMorgan"),
    "cathie wood": ("Cathie Wood", "CEO, ARK Invest"),
    "michael burry": ("Michael Burry", "Founder, Scion Capital"),
    "peter schiff": ("Peter Schiff", "Economist & Gold Bull"),
    "jim rickards": ("Jim Rickards", "Economist & Author"),
    # Policy / government
    "donald trump": ("Donald Trump", "President of the United States"),
    "jerome powell": ("Jerome Powell", "Chair, Federal Reserve"),
    "gary gensler": ("Gary Gensler", "Former Chair, SEC"),
    "scott bessent": ("Scott Bessent", "U.S. Treasury Secretary"),
    "nayib bukele": ("Nayib Bukele", "President of El Salvador"),
    # Tech founders
    "elon musk": ("Elon Musk", "CEO, Tesla & SpaceX"),
    "jensen huang": ("Jensen Huang", "CEO, Nvidia"),
    "jeff bezos": ("Jeff Bezos", "Founder, Amazon"),
    "mark zuckerberg": ("Mark Zuckerberg", "CEO, Meta"),
    # Finance personalities / authors
    "kevin o'leary": ("Kevin O'Leary", "Investor, Shark Tank"),
    "robert kiyosaki": ("Robert Kiyosaki", "Author, Rich Dad Poor Dad"),
    "jordan belfort": ("Jordan Belfort", "The Wolf of Wall Street"),
    "patrick bet-david": ("Patrick Bet-David", "Founder, Valuetainment"),
}

PERSON_ALIASES: dict[str, tuple[str, ...]] = {
    "Brad Garlinghouse": ("garlinghouse",),
    "Chris Larsen": ("larsen",),
    "Jed McCaleb": ("mccaleb",),
    "Michael Saylor": ("saylor",),
    "Vitalik Buterin": ("vitalik", "buterin"),
    "Changpeng Zhao": ("changpeng", "cz"),
    "Warren Buffett": ("buffett",),
    "Jamie Dimon": ("dimon",),
    "Michael Burry": ("burry",),
    "Peter Schiff": ("schiff",),
    "Jim Rickards": ("rickards",),
    "Donald Trump": ("trump",),
    "Gary Gensler": ("gensler",),
    "Scott Bessent": ("bessent",),
    "Nayib Bukele": ("bukele",),
    "Elon Musk": ("elon", "musk"),
    "Jeff Bezos": ("bezos",),
    "Mark Zuckerberg": ("zuckerberg",),
    "Kevin O'Leary": ("o'leary",),
    "Robert Kiyosaki": ("kiyosaki",),
    "Jordan Belfort": ("belfort",),
    "Patrick Bet-David": ("bet-david", "bet david"),
}

ASR_VARIANTS: dict[str, tuple[str, ...]] = {
    "Michael Saylor": ("sailor",),
    "Chris Larsen": ("larson",),
}

ALIAS_NOT_AFTER: dict[str, frozenset[str]] = {
    "trump": frozenset({"barron", "baron", "eric", "melania", "ivanka", "tiffany", "lara", "fred"}),
}

_TOKEN_PUNCT = '.,!?;:"()[]'


def _norm_token(raw: str) -> str:
    """Lower-case a transcript token and strip punctuation and possessives, so
    "Gensler's," matches "gensler". Apostrophes inside a name (O'Leary) stay."""
    tok = raw.strip(_TOKEN_PUNCT).lower().replace("\u2019", "'")
    return tok[:-2] if tok.endswith("'s") else tok


def mention_time(
    words: Sequence[TranscriptWord],
    phrase: str,
    *,
    cased_only: bool = False,
    not_after: frozenset[str] = frozenset(),
    first_name: str = "",
) -> float | None:
    """Start time of the first spoken occurrence of `phrase`, or None.

    `cased_only` accepts a match only when the transcript capitalizes it or the
    speaker's first name precedes it (for ASR mishearings that are real words).
    `not_after` rejects a match that follows one of those tokens.
    """
    toks = phrase.lower().split()
    n = len(toks)
    for i in range(len(words) - n + 1):
        raw = [word_text(words[j]) for j in range(i, i + n)]
        if [_norm_token(r) for r in raw] != toks:
            continue
        prev = _norm_token(word_text(words[i - 1])) if i else ""
        if prev in not_after:
            continue
        if cased_only and not (raw[0][:1].isupper() or prev == first_name):
            continue
        return float(words[i]["start"])
    return None


def find_person_mentions(words: Sequence[TranscriptWord]) -> list[tuple[str, str, float]]:
    """(name, role, first-mention time) for every curated person the transcript
    names, earliest first. The full name, its aliases and its ASR variants all
    count; the earliest of them is the mention."""
    found: list[tuple[str, str, float]] = []
    for full, (name, role) in KNOWN_PEOPLE.items():
        first_name = full.split()[0]
        times: list[float] = []
        for phrase in (full, *PERSON_ALIASES.get(name, ())):
            t = mention_time(words, phrase, not_after=ALIAS_NOT_AFTER.get(phrase, frozenset()))
            if t is not None:
                times.append(t)
        for variant in ASR_VARIANTS.get(name, ()):
            t = mention_time(words, variant, cased_only=True, first_name=first_name)
            if t is not None:
                times.append(t)
        if times:
            found.append((name, role, min(times)))
    found.sort(key=lambda mention: mention[2])
    return found

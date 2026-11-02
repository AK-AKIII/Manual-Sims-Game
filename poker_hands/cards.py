"""Cards are two-character strings: rank then suit, e.g. "AS", "TD", "7H"."""

RANKS = "23456789TJQKA"
SUITS = "SHDC"
STANDARD_DECK = [r + s for s in SUITS for r in RANKS]


def rank_of(card):
    """Numeric rank, 2..14 (ace high)."""
    return RANKS.index(card[0]) + 2


def suit_of(card):
    return card[1]


def build_deck(config, rng):
    """Return a shuffled deck for one game.

    "standard": the usual 52 distinct cards.
    "random":   deck_size cards, each drawn uniformly from the 52 card faces,
                so duplicates are possible and the mix changes every game.
    """
    kind = config["deck"]
    if kind == "standard":
        deck = list(STANDARD_DECK)
    elif kind == "random":
        deck = [rng.choice(STANDARD_DECK) for _ in range(config["deck_size"])]
    else:
        raise ValueError(f"unknown deck type {kind!r}; use 'standard' or 'random'")
    rng.shuffle(deck)
    return deck

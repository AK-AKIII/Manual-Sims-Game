"""Fixed rules: cards, hand types, modifiers, prices.

Everything random about a test case lives in the input file (see instance.py);
everything here is the same for every test. web/engine.js mirrors this file
and sim.py exactly; tests/test_poker_run_web.py checks that they agree.

At the bottom, the same numbers as a Ruleset: CLASSIC (the contest's rules)
and BALANCED, an optional variant (problem/balanced.md).
"""

RANKS = "23456789TJQKA"
SUITS = "SHDC"
N_CARDS = 52


def start_face(card_id):
    """Card ids 0-51 start as 2S..AS, 2H..AH, 2D..AD, 2C..AC."""
    return card_id % 13, card_id // 13


def face_str(rank, suit):
    return RANKS[rank] + SUITS[suit]


def chips(rank):
    """What a scored card adds to the base: 2-10 face value, J/Q/K 10, A 11."""
    if rank <= 8:
        return rank + 2
    return 11 if rank == 12 else 10


# code, name, base, mult, base gained per level, mult gained per level.
# Ordered weakest to strongest: a play counts as the strongest type it makes.
HANDS = [
    ("HC", "High Card", 5, 1, 10, 1),
    ("PR", "Pair", 10, 2, 15, 1),
    ("TP", "Two Pair", 20, 2, 20, 1),
    ("TK", "Three of a Kind", 25, 3, 20, 2),
    ("ST", "Straight", 30, 4, 30, 2),
    ("FL", "Flush", 35, 4, 25, 2),
    ("FH", "Full House", 45, 4, 25, 2),
    ("FK", "Four of a Kind", 60, 6, 30, 3),
    ("SF", "Straight Flush", 80, 8, 40, 3),
    ("FV", "Five of a Kind", 100, 10, 35, 3),
    ("HF", "Flush House", 120, 11, 40, 3),
    ("F5", "Flush Five", 150, 12, 50, 3),
]
HAND_CODES = [h[0] for h in HANDS]
HAND_NAMES = {h[0]: h[1] for h in HANDS}


def hand_value(code, level):
    """(base, mult) of a hand type at a level (levels start at 1)."""
    _, _, base, mult, gain_base, gain_mult = HANDS[HAND_CODES.index(code)]
    return base + (level - 1) * gain_base, mult + (level - 1) * gain_mult


# id: (price, text). Scoring order: hand base and mult, then scored cards'
# chips, then every modifier's additions, then every modifier's multipliers.
MODIFIERS = {
    "rose_tint": (5, "+3 mult for each scored heart or diamond"),
    "coal_seam": (5, "+3 mult for each scored spade or club"),
    "low_road": (4, "+12 base for each scored 2, 3, 4, 5 or 6"),
    "high_court": (5, "+3 mult for each scored J, Q or K"),
    "ace_dividend": (4, "+$2 for each scored ace"),
    "twin_engine": (6, "+4 mult for each rank that appears 2+ times among the scored cards"),
    "short_hand": (7, "x2 mult when you play 3 or fewer cards"),
    "full_spread": (5, "+40 base when you play exactly 5 cards"),
    "last_call": (7, "x3 mult on the last play of a round"),
    "momentum": (6, "+2 mult for each play in a row before this one of the same hand type"),
    "collector": (6, "gains +10 base for good each time you play a Straight or Straight Flush; adds its base to every play"),
    "thin_deck": (6, "+1 mult for each card your deck is below 52"),
    "mirror": (6, "scored cards add their chips twice"),
    "tricolor": (6, "x2 mult when the scored cards have 3 or more suits"),
    "savings": (5, "interest cap +$5"),
    "spare_hands": (4, "+$2 for each unused discard when you clear a round"),
    "odd_job": (5, "+2 mult for each scored A, 3, 5, 7 or 9"),
    "even_split": (5, "+15 base for each scored 2, 4, 6, 8 or 10"),
}
MODIFIER_IDS = list(MODIFIERS)

# Shop prices. level costs LEVEL_BASE + LEVEL_STEP * current level.
PRICE_REROLL = 2          # +1 for each reroll in the same shop
PRICE_REMOVE = 3
PRICE_PAINT = 3
PRICE_PROMOTE = 3
PRICE_DISCARDS = 8        # x (times bought + 1)
PRICE_HAND_SIZE = 10      # x (times bought + 1)
LEVEL_BASE, LEVEL_STEP = 2, 2
SHOP_WINDOW = 3           # offers visible at once
MIN_DECK = 20
MAX_HAND_SIZE = 10

# Money when you clear a round.
ROUND_REWARD = 4
PLAY_BONUS = 1            # per unused play
INTEREST_PER = 5          # $1 per $5 held
INTEREST_CAP = 5

# Modifier amounts: the numbers in the texts above (mirror: extra times the
# chips are added; short_hand, last_call, tricolor: the multiplier).
EFFECTS = {
    "rose_tint": 3, "coal_seam": 3, "low_road": 12, "high_court": 3, "ace_dividend": 2, "twin_engine": 4,
    "short_hand": 2, "full_spread": 40, "last_call": 3, "momentum": 2, "collector": 10, "thin_deck": 1,
    "mirror": 1, "tricolor": 2, "savings": 5, "spare_hands": 2, "odd_job": 2, "even_split": 15,
}


# ---- rulesets ---------------------------------------------------------------------

class Ruleset:
    """Every number above, as one object, so a game can be played by other rules.

    CLASSIC is the rules above (the contest's). Game(inst, rules=...) takes a
    Ruleset or its name; get_ruleset() lists the others."""

    def __init__(self, name, **values):
        self.name = name
        self.__dict__.update(values)
        self.hand_codes = [h[0] for h in self.hands]
        self.hand_names = {h[0]: h[1] for h in self.hands}
        self._hand_rows = {h[0]: h for h in self.hands}

    def hand_value(self, code, level):
        """(base, mult) of a hand type at a level (levels start at 1)."""
        _, _, base, mult, gain_base, gain_mult = self._hand_rows[code]
        return base + (level - 1) * gain_base, mult + (level - 1) * gain_mult

    def price(self, name):
        """Price of a modifier."""
        return self.modifiers[name][0]

    def changed(self, name, **changes):
        """A copy with some values changed (dicts given in changes replace the old ones)."""
        values = {k: v for k, v in self.__dict__.items() if k not in ("name", "hand_codes", "hand_names")
                  and not k.startswith("_")}
        values.update(changes)
        return Ruleset(name, **values)


CLASSIC = Ruleset(
    "classic", hands=HANDS, modifiers=MODIFIERS, effects=EFFECTS,
    price_reroll=PRICE_REROLL, price_remove=PRICE_REMOVE, price_paint=PRICE_PAINT, price_promote=PRICE_PROMOTE,
    price_discards=PRICE_DISCARDS, price_hand_size=PRICE_HAND_SIZE, level_base=LEVEL_BASE, level_step=LEVEL_STEP,
    shop_window=SHOP_WINDOW, min_deck=MIN_DECK, max_hand_size=MAX_HAND_SIZE, round_reward=ROUND_REWARD,
    play_bonus=PLAY_BONUS, interest_per=INTEREST_PER, interest_cap=INTEREST_CAP,
    savings_interest_per=INTEREST_PER,   # with savings: $1 interest per this many $ held
    end_in_last_round=True,   # `end` is allowed in the last round
    tricolor_exact=False,     # tricolor wants 3 or more suits (True: exactly 3)
)

# The balanced ruleset: an optional variant of classic (see problem/balanced.md).
BALANCED = CLASSIC.changed(
    "balanced",
    hands=[
        ("HC", "High Card", 5, 1, 10, 1),
        ("PR", "Pair", 10, 2, 15, 1),
        ("TP", "Two Pair", 20, 2, 20, 1),
        ("TK", "Three of a Kind", 30, 3, 25, 2),
        ("ST", "Straight", 30, 4, 30, 2),
        ("FL", "Flush", 40, 5, 30, 2),
        ("FH", "Full House", 35, 4, 20, 2),
        ("FK", "Four of a Kind", 60, 6, 30, 3),
        ("SF", "Straight Flush", 80, 8, 40, 3),
        ("FV", "Five of a Kind", 100, 10, 35, 3),
        ("HF", "Flush House", 120, 11, 40, 3),
        ("F5", "Flush Five", 150, 12, 50, 3),
    ],
    modifiers={**MODIFIERS,
               "ace_dividend": (3, "+$3 for each scored ace"),
               "short_hand": (7, "x3 mult when you play 3 or fewer cards"),
               "momentum": (5, "+3 mult for each play in a row before this one of the same hand type"),
               "collector": (5, "gains +20 base for good each time you play a Straight or Straight Flush; "
                                "adds its base to every play"),
               "thin_deck": (6, "+2 mult for each card your deck is below 52"),
               "tricolor": (7, "x2 mult when the scored cards have exactly 3 suits"),
               "savings": (4, "interest is $1 per $3 held (not per $5), and its cap is +$5"),
               "spare_hands": (3, "+$3 for each unused discard when you clear a round")},
    effects={**EFFECTS, "ace_dividend": 3, "short_hand": 3, "momentum": 3, "collector": 20, "thin_deck": 2,
             "spare_hands": 3},
    price_remove=2, price_paint=2, price_promote=2,
    savings_interest_per=3,
    end_in_last_round=False,
    tricolor_exact=True,
)

RULESETS = {"classic": CLASSIC, "balanced": BALANCED}


def get_ruleset(rules=None):
    """A Ruleset from a name (None means classic), or the Ruleset itself."""
    if isinstance(rules, Ruleset):
        return rules
    if rules is None:
        return CLASSIC
    if rules not in RULESETS:
        raise ValueError(f"unknown ruleset {rules!r}: use one of {', '.join(RULESETS)}")
    return RULESETS[rules]

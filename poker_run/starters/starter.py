"""Poker Run starter (Python 3.8+, no dependencies). Submit this one file.

It reads the input, gives you an exact copy of the judge's simulator, and
prints whatever moves solve() returns. Fill in solve().

    python3 starter.py < input.txt > output.txt

The simulator, in short (full rules in statement.md):

    game = Game(inst)               # a fresh run
    game.hand, game.pile            # card ids in hand, and the rest of this round's draw order
    game.rank[c], game.suit[c]      # current face of card c (rank 0=2 .. 12=A, suit 0..3 = S H D C)
    game.evaluate([c1, c2])         # score of playing these cards now, without playing them
    game.apply("play 3 17")         # do a move (raises IllegalMove if it isn't legal)
    g2 = game.clone()               # cheap copy to try moves on
    game.phase                      # "play", "shop" or "over"
    game.round, game.target, game.round_score, game.total, game.money
    game.plays_left, game.discards_left, game.mods, game.levels
    game.visible_offers(), game.prices(), game.level_price(code)
"""

import sys
import time
from dataclasses import dataclass, field


# ---- rules ----------------------------------------------------------------------

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


# ---- input ----------------------------------------------------------------------

@dataclass
class Instance:
    rounds: int
    hand_size: int
    plays: int
    discards: int
    slots: int
    queue: int
    targets: list
    orders: list = field(repr=False)   # orders[r] = draw order of round r
    offers: list = field(repr=False)   # offers[r] = shop queue after round r


class BadInput(ValueError):
    pass


def parse_instance(text):
    tok = text.split()
    pos = 0

    def take(n):
        nonlocal pos
        if pos + n > len(tok):
            raise BadInput("input ends early")
        out = tok[pos:pos + n]
        pos += n
        return out

    def ints(n):
        words = take(n)
        try:
            return [int(t) for t in words]
        except ValueError as e:
            raise BadInput(f"expected an integer: {e}") from None

    rounds, hand_size, plays, discards, slots, queue = ints(6)
    if not (1 <= rounds and 1 <= hand_size and 1 <= plays and 0 <= discards and 0 <= slots and 0 <= queue):
        raise BadInput("bad parameters")
    targets = ints(rounds)
    orders = [ints(N_CARDS) for _ in range(rounds)]
    offers = [take(queue) for _ in range(rounds - 1)]
    if pos != len(tok):
        raise BadInput("extra data after the offer queues")
    for r, order in enumerate(orders):
        if sorted(order) != list(range(N_CARDS)):
            raise BadInput(f"draw order {r + 1} is not a permutation of 0..51")
    for q in offers:
        if any(m not in MODIFIER_IDS for m in q):
            raise BadInput(f"unknown modifier in {q}")
    return Instance(rounds, hand_size, plays, discards, slots, queue, targets, orders, offers)


# ---- simulator (identical to the judge's) ----------------------------------------

STARTING_MONEY = 4


class IllegalMove(ValueError):
    pass


def classify(ranks, suits):
    """Hand type code and the positions (into ranks) of the cards that score."""
    n = len(ranks)
    count = {}
    for r in ranks:
        count[r] = count.get(r, 0) + 1
    groups = sorted(count.values(), reverse=True)
    flush = n == 5 and len(set(suits)) == 1
    distinct = sorted(count)
    straight = n == 5 and len(distinct) == 5 and (distinct[4] - distinct[0] == 4 or distinct == [0, 1, 2, 3, 12])
    full_house = groups[:2] == [3, 2]
    top = groups[0]
    if top == 5 and flush:
        code = "F5"
    elif full_house and flush:
        code = "HF"
    elif top == 5:
        code = "FV"
    elif straight and flush:
        code = "SF"
    elif top == 4:
        code = "FK"
    elif full_house:
        code = "FH"
    elif flush:
        code = "FL"
    elif straight:
        code = "ST"
    elif top == 3:
        code = "TK"
    elif len(groups) > 1 and groups[1] >= 2:
        code = "TP"
    elif top == 2:
        code = "PR"
    else:
        code = "HC"
    if n == 5 and code in ("F5", "HF", "FV", "SF", "FH", "FL", "ST"):
        scored = list(range(5))
    elif code == "HC":
        best = max(ranks)
        scored = [ranks.index(best)]
    else:
        scored = [i for i, r in enumerate(ranks) if count[r] >= 2]
    return code, scored


class Game:
    __slots__ = ("inst", "rank", "suit", "in_deck", "deck_size", "levels", "mods", "money", "round",
                 "phase", "round_score", "total", "plays_left", "discards_left", "pile", "hand",
                 "offer_pos", "offers_taken", "rerolls", "extra_discards", "extra_hand", "cleared", "moves")

    def __init__(self, inst):
        self.inst = inst
        faces = [start_face(i) for i in range(N_CARDS)]
        self.rank = [f[0] for f in faces]
        self.suit = [f[1] for f in faces]
        self.in_deck = [True] * N_CARDS
        self.deck_size = N_CARDS
        self.levels = {c: 1 for c in HAND_CODES}
        self.mods = []            # [[name, state], ...] in slot order
        self.money = STARTING_MONEY
        self.total = 0
        self.extra_discards = 0
        self.extra_hand = 0
        self.cleared = 0
        self.moves = 0
        self.offer_pos, self.offers_taken, self.rerolls = 0, set(), 0
        self._start_round(0)

    def clone(self):
        g = Game.__new__(Game)
        for name in Game.__slots__:
            setattr(g, name, getattr(self, name))
        g.rank, g.suit, g.in_deck = self.rank[:], self.suit[:], self.in_deck[:]
        g.levels = dict(self.levels)
        g.mods = [[m, dict(s)] for m, s in self.mods]
        g.pile, g.hand, g.offers_taken = self.pile[:], self.hand[:], set(self.offers_taken)
        return g

    # ---- what you can see --------------------------------------------------

    @property
    def over(self):
        return self.phase == "over"

    @property
    def target(self):
        return self.inst.targets[self.round]

    @property
    def hand_size(self):
        return self.inst.hand_size + self.extra_hand

    def face(self, card):
        return self.rank[card], self.suit[card]

    def has_mod(self, name):
        return any(m == name for m, _ in self.mods)

    def visible_offers(self):
        if self.phase != "shop":
            return []
        queue = self.inst.offers[self.round]
        window = queue[self.offer_pos:self.offer_pos + SHOP_WINDOW]
        return [m for k, m in enumerate(window) if self.offer_pos + k not in self.offers_taken]

    def level_price(self, code):
        return LEVEL_BASE + LEVEL_STEP * self.levels[code]

    def prices(self):
        """Prices of the fixed shop items right now."""
        return {
            "reroll": PRICE_REROLL + self.rerolls,
            "remove": PRICE_REMOVE,
            "paint": PRICE_PAINT,
            "promote": PRICE_PROMOTE,
            "discards": PRICE_DISCARDS * (self.extra_discards + 1),
            "handsize": PRICE_HAND_SIZE * (self.extra_hand + 1),
        }

    def evaluate(self, cards):
        """Score of playing these cards now, without playing them."""
        ranks = [self.rank[c] for c in cards]
        suits = [self.suit[c] for c in cards]
        code, scored = classify(ranks, suits)
        s_ranks = [ranks[i] for i in scored]
        s_suits = [suits[i] for i in scored]
        base, mult = hand_value(code, self.levels[code])
        base += sum(chips(r) for r in s_ranks)
        times = 1
        money = 0
        for name, state in self.mods:
            if name == "rose_tint":
                mult += 3 * sum(1 for s in s_suits if s in (1, 2))
            elif name == "coal_seam":
                mult += 3 * sum(1 for s in s_suits if s in (0, 3))
            elif name == "low_road":
                base += 12 * sum(1 for r in s_ranks if r <= 4)
            elif name == "high_court":
                mult += 3 * sum(1 for r in s_ranks if 9 <= r <= 11)
            elif name == "ace_dividend":
                money += 2 * sum(1 for r in s_ranks if r == 12)
            elif name == "twin_engine":
                mult += 4 * sum(1 for r in set(s_ranks) if s_ranks.count(r) >= 2)
            elif name == "short_hand":
                if len(cards) <= 3:
                    times *= 2
            elif name == "full_spread":
                if len(cards) == 5:
                    base += 40
            elif name == "last_call":
                if self.plays_left == 1:
                    times *= 3
            elif name == "momentum":
                if state["last"] == code:
                    mult += 2 * state["streak"]
            elif name == "collector":
                base += state["bonus"] + (10 if code in ("ST", "SF") else 0)
            elif name == "thin_deck":
                mult += max(0, N_CARDS - self.deck_size)
            elif name == "mirror":
                base += sum(chips(r) for r in s_ranks)
            elif name == "tricolor":
                if len(set(s_suits)) >= 3:
                    times *= 2
            elif name == "odd_job":
                mult += 2 * sum(1 for r in s_ranks if r in (1, 3, 5, 7, 12))  # 3 5 7 9 A
            elif name == "even_split":
                base += 15 * sum(1 for r in s_ranks if r <= 8 and r % 2 == 0)
        return {"hand": code, "scored": [cards[i] for i in scored], "base": base, "mult": mult,
                "times": times, "points": base * mult * times, "money": money}

    # ---- moves ---------------------------------------------------------------

    def apply(self, line):
        """Apply one move, given as an output line or a list of tokens."""
        tok = line.split() if isinstance(line, str) else list(line)
        if not tok:
            raise IllegalMove("empty move")
        if self.phase == "over":
            raise IllegalMove("the run is already over")
        cmd, args = tok[0], tok[1:]
        handler = (self._PLAY_MOVES if self.phase == "play" else self._SHOP_MOVES).get(cmd)
        if handler is None:
            where = "during a round" if self.phase == "play" else "in the shop"
            raise IllegalMove(f"'{cmd}' is not a move {where}")
        handler(self, args)
        self.moves += 1

    def _cards(self, args, limit):
        try:
            cards = [int(a) for a in args]
        except ValueError:
            raise IllegalMove("card ids must be integers") from None
        if not 1 <= len(cards) <= limit:
            raise IllegalMove(f"give 1 to {limit} cards")
        if len(set(cards)) != len(cards):
            raise IllegalMove("card ids must be distinct")
        for c in cards:
            if c not in self.hand:
                raise IllegalMove(f"card {c} is not in your hand")
        return cards

    def _play(self, args):
        if self.plays_left <= 0:
            raise IllegalMove("no plays left")
        cards = self._cards(args, 5)
        res = self.evaluate(cards)
        for name, state in self.mods:
            if name == "momentum":
                if state["last"] == res["hand"]:
                    state["streak"] += 1
                else:
                    state["last"], state["streak"] = res["hand"], 1
            elif name == "collector" and res["hand"] in ("ST", "SF"):
                state["bonus"] += 10
        self.round_score += res["points"]
        self.total += res["points"]
        self.money += res["money"]
        self.plays_left -= 1
        self.hand = [c for c in self.hand if c not in cards]
        self._refill()
        if self.plays_left == 0 or not self.hand:
            self._end_round()

    def _discard(self, args):
        if self.discards_left <= 0:
            raise IllegalMove("no discards left")
        cards = self._cards(args, 5)
        self.discards_left -= 1
        self.hand = [c for c in self.hand if c not in cards]
        self._refill()
        if not self.hand:
            self._end_round()

    def _end(self, args):
        if args:
            raise IllegalMove("'end' takes no arguments")
        if self.round_score < self.target:
            raise IllegalMove(f"round score {self.round_score} is below the target {self.target}")
        self._end_round()

    def _refill(self):
        while len(self.hand) < self.hand_size and self.pile:
            self.hand.append(self.pile.pop(0))

    def _start_round(self, r):
        self.round = r
        self.phase = "play"
        self.round_score = 0
        self.plays_left = self.inst.plays
        self.discards_left = self.inst.discards + self.extra_discards
        self.pile = [c for c in self.inst.orders[r] if self.in_deck[c]]
        self.hand = []
        self._refill()

    def _end_round(self):
        if self.round_score < self.target:
            self.phase = "over"
            return
        self.cleared += 1
        cap = INTEREST_CAP + (5 if self.has_mod("savings") else 0)
        reward = ROUND_REWARD + PLAY_BONUS * self.plays_left + min(self.money // INTEREST_PER, cap)
        if self.has_mod("spare_hands"):
            reward += 2 * self.discards_left
        self.money += reward
        if self.round + 1 == self.inst.rounds:
            self.phase = "over"
            return
        self.phase = "shop"
        self.offer_pos = 0
        self.offers_taken = set()
        self.rerolls = 0

    # ---- shop moves ------------------------------------------------------------

    def _pay(self, cost):
        if self.money < cost:
            raise IllegalMove(f"costs ${cost}, you have ${self.money}")
        self.money -= cost

    def _one(self, args, what):
        if len(args) != 1:
            raise IllegalMove(f"give exactly one {what}")
        return args[0]

    def _deck_card(self, args, extra=0):
        if len(args) != 1 + extra:
            raise IllegalMove("give a card id" + (" and a suit" if extra else ""))
        try:
            c = int(args[0])
        except ValueError:
            raise IllegalMove("card ids must be integers") from None
        if not 0 <= c < N_CARDS or not self.in_deck[c]:
            raise IllegalMove(f"card {c} is not in your deck")
        return c

    def _buy(self, args):
        name = self._one(args, "modifier name")
        queue = self.inst.offers[self.round]
        window = range(self.offer_pos, min(self.offer_pos + SHOP_WINDOW, len(queue)))
        slot = next((k for k in window if queue[k] == name and k not in self.offers_taken), None)
        if slot is None:
            raise IllegalMove(f"{name} is not on offer")
        if self.has_mod(name):
            raise IllegalMove(f"you already own {name}")
        if len(self.mods) >= self.inst.slots:
            raise IllegalMove(f"all {self.inst.slots} modifier slots are full")
        self._pay(MODIFIERS[name][0])
        self.offers_taken.add(slot)
        self.mods.append([name, {"last": "", "streak": 0} if name == "momentum" else
                          {"bonus": 0} if name == "collector" else {}])

    def _sell(self, args):
        name = self._one(args, "modifier name")
        for k, (m, _) in enumerate(self.mods):
            if m == name:
                del self.mods[k]
                self.money += MODIFIERS[name][0] // 2
                return
        raise IllegalMove(f"you don't own {name}")

    def _level(self, args):
        code = self._one(args, "hand code")
        if code not in self.levels:
            raise IllegalMove(f"unknown hand code {code}")
        self._pay(self.level_price(code))
        self.levels[code] += 1

    def _remove(self, args):
        c = self._deck_card(args)
        if self.deck_size <= MIN_DECK:
            raise IllegalMove(f"your deck can't go below {MIN_DECK} cards")
        self._pay(PRICE_REMOVE)
        self.in_deck[c] = False
        self.deck_size -= 1

    def _paint(self, args):
        c = self._deck_card(args, extra=1)
        if args[1] not in tuple(SUITS):
            raise IllegalMove("suit must be one of S H D C")
        self._pay(PRICE_PAINT)
        self.suit[c] = SUITS.index(args[1])

    def _promote(self, args):
        c = self._deck_card(args)
        if self.rank[c] == 12:
            raise IllegalMove("an ace can't be promoted")
        self._pay(PRICE_PROMOTE)
        self.rank[c] += 1

    def _discards(self, args):
        if args:
            raise IllegalMove("'discards' takes no arguments")
        self._pay(self.prices()["discards"])
        self.extra_discards += 1

    def _handsize(self, args):
        if args:
            raise IllegalMove("'handsize' takes no arguments")
        if self.hand_size >= MAX_HAND_SIZE:
            raise IllegalMove(f"hand size is already {MAX_HAND_SIZE}")
        self._pay(self.prices()["handsize"])
        self.extra_hand += 1

    def _reroll(self, args):
        if args:
            raise IllegalMove("'reroll' takes no arguments")
        if self.offer_pos + SHOP_WINDOW >= len(self.inst.offers[self.round]):
            raise IllegalMove("the offer queue is used up")
        self._pay(self.prices()["reroll"])
        self.rerolls += 1
        self.offer_pos += SHOP_WINDOW

    def _next(self, args):
        if args:
            raise IllegalMove("'next' takes no arguments")
        self._start_round(self.round + 1)

    _PLAY_MOVES = {"play": _play, "discard": _discard, "end": _end}
    _SHOP_MOVES = {"buy": _buy, "sell": _sell, "level": _level, "remove": _remove, "paint": _paint,
                   "promote": _promote, "discards": _discards, "handsize": _handsize, "reroll": _reroll,
                   "next": _next}


# ---- your solver --------------------------------------------------------------

TIME_LIMIT = 1.8  # seconds; the judge allows 2


def solve(inst, deadline):
    """Return a list of moves. This placeholder plays the first card every turn
    and leaves every shop at once, which is valid but scores little."""
    game = Game(inst)
    moves = []
    while not game.over and time.perf_counter() < deadline:
        if game.phase == "play":
            move = f"play {game.hand[0]}"
        else:
            move = "next"
        game.apply(move)
        moves.append(move)
    return moves


def main():
    deadline = time.perf_counter() + TIME_LIMIT
    inst = parse_instance(sys.stdin.read())
    moves = solve(inst, deadline)
    print(len(moves))
    print("\n".join(moves))


if __name__ == "__main__":
    main()

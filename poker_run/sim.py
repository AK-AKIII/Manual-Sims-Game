"""The simulator: applies moves to a test case and keeps the score.

Moves (one per output line):

    play i j ...        play 1-5 cards from your hand, by card id
    discard i j ...     discard 1-5 cards from your hand
    end                 finish the round early (only once its target is met)
    buy NAME            buy a visible shop offer (a modifier)
    sell NAME           sell a modifier you own for half its price
    level CODE          level up a hand type (HC PR TP TK ST FL FH FK SF FV HF F5)
    remove i            remove card i from your deck
    paint i S           change card i's suit to S (one of S H D C)
    promote i           raise card i's rank by one (not an ace)
    discards            +1 discard per round from now on
    handsize            +1 hand size from now on
    reroll              show the next offers in the queue
    next                leave the shop and start the next round

Solvers can copy a Game with clone() and try moves on the copy.

Game(inst, rules="balanced") plays by another ruleset (rules.get_ruleset);
the default is the classic rules of the contest.
"""

from .rules import N_CARDS, SUITS, chips, get_ruleset, start_face

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
                 "offer_pos", "offers_taken", "rerolls", "extra_discards", "extra_hand", "cleared", "moves", "rules")

    def __init__(self, inst, rules=None):
        self.inst = inst
        self.rules = get_ruleset(rules)
        faces = [start_face(i) for i in range(N_CARDS)]
        self.rank = [f[0] for f in faces]
        self.suit = [f[1] for f in faces]
        self.in_deck = [True] * N_CARDS
        self.deck_size = N_CARDS
        self.levels = {c: 1 for c in self.rules.hand_codes}
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
        window = queue[self.offer_pos:self.offer_pos + self.rules.shop_window]
        return [m for k, m in enumerate(window) if self.offer_pos + k not in self.offers_taken]

    def level_price(self, code):
        return self.rules.level_base + self.rules.level_step * self.levels[code]

    def prices(self):
        """Prices of the fixed shop items right now."""
        R = self.rules
        return {
            "reroll": R.price_reroll + self.rerolls,
            "remove": R.price_remove,
            "paint": R.price_paint,
            "promote": R.price_promote,
            "discards": R.price_discards * (self.extra_discards + 1),
            "handsize": R.price_hand_size * (self.extra_hand + 1),
        }

    def evaluate(self, cards):
        """Score of playing these cards now, without playing them."""
        ranks = [self.rank[c] for c in cards]
        suits = [self.suit[c] for c in cards]
        code, scored = classify(ranks, suits)
        s_ranks = [ranks[i] for i in scored]
        s_suits = [suits[i] for i in scored]
        base, mult = self.rules.hand_value(code, self.levels[code])
        base += sum(chips(r) for r in s_ranks)
        times = 1
        money = 0
        eff = self.rules.effects
        for name, state in self.mods:
            if name == "rose_tint":
                mult += eff[name] * sum(1 for s in s_suits if s in (1, 2))
            elif name == "coal_seam":
                mult += eff[name] * sum(1 for s in s_suits if s in (0, 3))
            elif name == "low_road":
                base += eff[name] * sum(1 for r in s_ranks if r <= 4)
            elif name == "high_court":
                mult += eff[name] * sum(1 for r in s_ranks if 9 <= r <= 11)
            elif name == "ace_dividend":
                money += eff[name] * sum(1 for r in s_ranks if r == 12)
            elif name == "twin_engine":
                mult += eff[name] * sum(1 for r in set(s_ranks) if s_ranks.count(r) >= 2)
            elif name == "short_hand":
                if len(cards) <= 3:
                    times *= eff[name]
            elif name == "full_spread":
                if len(cards) == 5:
                    base += eff[name]
            elif name == "last_call":
                if self.plays_left == 1:
                    times *= eff[name]
            elif name == "momentum":
                if state["last"] == code:
                    mult += eff[name] * state["streak"]
            elif name == "collector":
                base += state["bonus"] + (eff[name] if code in ("ST", "SF") else 0)
            elif name == "thin_deck":
                mult += eff[name] * max(0, N_CARDS - self.deck_size)
            elif name == "mirror":
                base += eff[name] * sum(chips(r) for r in s_ranks)
            elif name == "tricolor":
                k = len(set(s_suits))
                if k == 3 or (k > 3 and not self.rules.tricolor_exact):
                    times *= eff[name]
            elif name == "odd_job":
                mult += eff[name] * sum(1 for r in s_ranks if r in (1, 3, 5, 7, 12))  # 3 5 7 9 A
            elif name == "even_split":
                base += eff[name] * sum(1 for r in s_ranks if r <= 8 and r % 2 == 0)
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
                state["bonus"] += self.rules.effects[name]
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
        if self.round + 1 == self.inst.rounds and not self.rules.end_in_last_round:
            raise IllegalMove(f"'end' is not allowed in the last round ({self.rules.name} rules)")
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
        R = self.rules
        saving = self.has_mod("savings")
        cap = R.interest_cap + (R.effects["savings"] if saving else 0)
        per = R.savings_interest_per if saving else R.interest_per
        reward = R.round_reward + R.play_bonus * self.plays_left + min(self.money // per, cap)
        if self.has_mod("spare_hands"):
            reward += R.effects["spare_hands"] * self.discards_left
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
        window = range(self.offer_pos, min(self.offer_pos + self.rules.shop_window, len(queue)))
        slot = next((k for k in window if queue[k] == name and k not in self.offers_taken), None)
        if slot is None:
            raise IllegalMove(f"{name} is not on offer")
        if self.has_mod(name):
            raise IllegalMove(f"you already own {name}")
        if len(self.mods) >= self.inst.slots:
            raise IllegalMove(f"all {self.inst.slots} modifier slots are full")
        self._pay(self.rules.price(name))
        self.offers_taken.add(slot)
        self.mods.append([name, {"last": "", "streak": 0} if name == "momentum" else
                          {"bonus": 0} if name == "collector" else {}])

    def _sell(self, args):
        name = self._one(args, "modifier name")
        for k, (m, _) in enumerate(self.mods):
            if m == name:
                del self.mods[k]
                self.money += self.rules.price(name) // 2
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
        if self.deck_size <= self.rules.min_deck:
            raise IllegalMove(f"your deck can't go below {self.rules.min_deck} cards")
        self._pay(self.rules.price_remove)
        self.in_deck[c] = False
        self.deck_size -= 1

    def _paint(self, args):
        c = self._deck_card(args, extra=1)
        if args[1] not in tuple(SUITS):
            raise IllegalMove("suit must be one of S H D C")
        self._pay(self.rules.price_paint)
        self.suit[c] = SUITS.index(args[1])

    def _promote(self, args):
        c = self._deck_card(args)
        if self.rank[c] == 12:
            raise IllegalMove("an ace can't be promoted")
        self._pay(self.rules.price_promote)
        self.rank[c] += 1

    def _discards(self, args):
        if args:
            raise IllegalMove("'discards' takes no arguments")
        self._pay(self.prices()["discards"])
        self.extra_discards += 1

    def _handsize(self, args):
        if args:
            raise IllegalMove("'handsize' takes no arguments")
        if self.hand_size >= self.rules.max_hand_size:
            raise IllegalMove(f"hand size is already {self.rules.max_hand_size}")
        self._pay(self.prices()["handsize"])
        self.extra_hand += 1

    def _reroll(self, args):
        if args:
            raise IllegalMove("'reroll' takes no arguments")
        if self.offer_pos + self.rules.shop_window >= len(self.inst.offers[self.round]):
            raise IllegalMove("the offer queue is used up")
        self._pay(self.prices()["reroll"])
        self.rerolls += 1
        self.offer_pos += self.rules.shop_window

    def _next(self, args):
        if args:
            raise IllegalMove("'next' takes no arguments")
        self._start_round(self.round + 1)

    _PLAY_MOVES = {"play": _play, "discard": _discard, "end": _end}
    _SHOP_MOVES = {"buy": _buy, "sell": _sell, "level": _level, "remove": _remove, "paint": _paint,
                   "promote": _promote, "discards": _discards, "handsize": _handsize, "reroll": _reroll,
                   "next": _next}

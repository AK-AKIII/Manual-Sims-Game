"""The game: one deck, one or more players taking turns.

A solo game is a table with one player. For a fair solo comparison the
tournament gives every bot its own table built from the same seed, so they
all see the identical deck. A shared game seats several bots at one table
and they draw from the same deck in turn.
"""

import copy
import random
from collections import Counter

from .cards import build_deck
from .hands import HAND_TYPES, classify

MAX_BUYS_PER_TURN = 50
MAX_ROUNDS = 10_000


def check_action(state, action):
    """Return why `action` is illegal in `state`, or None if it is legal."""
    if not isinstance(action, dict):
        return "action must be a dict"
    kind = action.get("action")
    if kind in ("play", "discard"):
        cards = action.get("cards")
        if not isinstance(cards, (list, tuple)) or not all(isinstance(i, int) for i in cards):
            return "cards must be a list of hand indices"
        if len(set(cards)) != len(cards):
            return "card indices must be distinct"
        if any(i < 0 or i >= len(state["hand"]) for i in cards):
            return f"card indices must be between 0 and {len(state['hand']) - 1}"
        limit = state["max_play_cards"] if kind == "play" else state["max_discard_cards"]
        if not 1 <= len(cards) <= limit:
            return f"{kind} between 1 and {limit} cards"
        if kind == "play" and state["plays_left"] <= 0:
            return "no plays left"
        if kind == "discard" and state["discards_left"] <= 0:
            return "no discards left"
        return None
    if kind == "buy":
        item = action.get("item")
        if item not in state["shop"]:
            return f"{item!r} is not for sale (shop: {sorted(state['shop'])})"
        if state["money"] < state["shop"][item]:
            return f"{item} costs {state['shop'][item]}, you have {state['money']}"
        return None
    return "action must be 'play', 'discard' or 'buy'"


def vary_points(config, rng):
    v = config["points_variation"]
    if v <= 0:
        return dict(config["points"])
    return {t: max(1, round(p * rng.uniform(1 - v, 1 + v))) for t, p in config["points"].items()}


class Player:
    def __init__(self, name, strategy, config):
        self.name = name
        self.strategy = strategy
        self.hand = []
        self.hand_size = config["hand_size"]
        self.plays_left = config["plays"]
        self.discards_left = config["discards"]
        self.money = config["starting_money"]
        self.score = 0
        self.levels = {t: 1 for t in HAND_TYPES}
        self.bought = Counter()
        self.hands_played = Counter()
        self.done = False
        self.error = None


class Table:
    def __init__(self, config, seed, players):
        """players: list of (name, strategy) where strategy(state) -> action."""
        self.config = config
        self.seed = seed
        rng = random.Random(seed)
        self.deck = build_deck(config, rng)
        self.composition = Counter(self.deck)
        self.base_points = vary_points(config, rng)
        self.seen = Counter()  # cards played or discarded so far (public)
        self.history = []      # public log of every action
        self.round = 0
        self.players = [Player(name, fn, config) for name, fn in players]
        for p in self.players:
            self._refill(p)
            self._update_done(p)

    # ---- what a bot sees ------------------------------------------------

    def points_for(self, p, hand_type):
        base = self.base_points[hand_type]
        return round(base * (1 + (p.levels[hand_type] - 1) * self.config["level_bonus"]))

    def shop_for(self, p):
        cfg = self.config
        if not cfg["economy"]:
            return {}
        shop = cfg["shop"]
        items = {
            "discard": shop["discard"] * (p.bought["discard"] + 1),
            "play": shop["play"] * (p.bought["play"] + 1),
        }
        if p.hand_size < cfg["max_hand_size"]:
            items["hand_size"] = shop["hand_size"] * (p.bought["hand_size"] + 1)
        for t in HAND_TYPES:
            items[f"level:{t}"] = shop["level"] * p.levels[t]
        return items

    def state_for(self, p):
        cfg = self.config
        unseen = self.composition - self.seen - Counter(p.hand)
        return {
            "mode": "shared" if len(self.players) > 1 else "solo",
            "seat": self.players.index(p),
            "round": self.round,
            "hand": list(p.hand),
            "hand_size": p.hand_size,
            "deck_remaining": len(self.deck),
            "unseen": dict(unseen),  # deck + opponents' hands, by card
            "plays_left": p.plays_left,
            "discards_left": p.discards_left,
            "money": p.money,
            "score": p.score,
            "points": {t: self.points_for(p, t) for t in HAND_TYPES},
            "levels": dict(p.levels),
            "money_table": dict(cfg["money"]) if cfg["economy"] else {},
            "shop": self.shop_for(p),
            "max_play_cards": cfg["max_play_cards"],
            "max_discard_cards": cfg["max_discard_cards"],
            "opponents": [
                {
                    "seat": i,
                    "score": q.score,
                    "money": q.money,
                    "plays_left": q.plays_left,
                    "discards_left": q.discards_left,
                    "hand_count": len(q.hand),
                    "done": q.done,
                }
                for i, q in enumerate(self.players)
                if q is not p
            ],
            "history": list(self.history),
        }

    # ---- running the game -----------------------------------------------

    def run(self):
        while not all(p.done for p in self.players) and self.round < MAX_ROUNDS:
            for p in self.players:
                if not p.done:
                    self.take_turn(p)
            self.round += 1
        return self.results()

    def take_turn(self, p):
        for _ in range(MAX_BUYS_PER_TURN + 1):
            state = self.state_for(p)
            try:
                action = p.strategy(copy.deepcopy(state))
            except Exception as e:  # a crashing bot ends its own game, not the tournament
                return self._fail(p, f"bot raised {type(e).__name__}: {e}")
            err = check_action(state, action)
            if err:
                return self._fail(p, f"invalid action {action!r}: {err}")
            if action["action"] == "buy":
                self._buy(p, action["item"], state["shop"][action["item"]])
                continue
            if action["action"] == "play":
                self._play(p, action["cards"])
            else:
                self._discard(p, action["cards"])
            break
        else:
            return self._fail(p, f"more than {MAX_BUYS_PER_TURN} purchases in one turn")
        self._refill(p)
        self._update_done(p)

    def _take(self, p, indices):
        cards = [p.hand[i] for i in indices]
        for i in sorted(indices, reverse=True):
            p.hand.pop(i)
        self.seen.update(cards)
        return cards

    def _play(self, p, indices):
        cards = self._take(p, indices)
        points = {t: self.points_for(p, t) for t in HAND_TYPES}
        hand_type = classify(cards, points)
        p.score += points[hand_type]
        p.plays_left -= 1
        p.hands_played[hand_type] += 1
        earned = self.config["money"][hand_type] if self.config["economy"] else 0
        p.money += earned
        self._log(p, action="play", cards=cards, hand_type=hand_type,
                  points=points[hand_type], money=earned)

    def _discard(self, p, indices):
        cards = self._take(p, indices)
        p.discards_left -= 1
        self._log(p, action="discard", cards=cards)

    def _buy(self, p, item, cost):
        p.money -= cost
        if item == "hand_size":
            p.hand_size += 1
            self._refill(p)
        elif item == "discard":
            p.discards_left += 1
        elif item == "play":
            p.plays_left += 1
        else:
            p.levels[item.split(":", 1)[1]] += 1
        p.bought[item] += 1
        self._log(p, action="buy", item=item, cost=cost)

    def _refill(self, p):
        while len(p.hand) < p.hand_size and self.deck:
            p.hand.append(self.deck.pop())

    def _update_done(self, p):
        if p.plays_left <= 0 or not p.hand:
            p.done = True

    def _fail(self, p, message):
        p.error = message
        p.done = True
        self._log(p, action="error", message=message)

    def _log(self, p, **event):
        self.history.append({"round": self.round, "seat": self.players.index(p), **event})

    def results(self):
        return [
            {
                "name": p.name,
                "seat": i,
                "score": p.score,
                "money": p.money,
                "hands_played": dict(p.hands_played),
                "error": p.error,
            }
            for i, p in enumerate(self.players)
        ]

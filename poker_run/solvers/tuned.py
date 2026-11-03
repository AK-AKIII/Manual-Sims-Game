"""Tuned heuristic: a fast hand-written policy whose numbers are learned.

    python3 poker_run/solvers/tuned.py < input.txt > output.txt

The idea
--------
The other solvers spend their time *searching*. This one spends almost no
time: every decision is a formula with weights, e.g.

    utility(buy tricolor) = W["mod_tricolor"]
    utility(level FH)     = W["level"] * (share of next round's points made by FH)
                            / (1 + W["level_decay"] * current level)
    keep at least W["save"] dollars (interest!) unless utility/price > W["splurge"]

Picking those weights by hand is guesswork, so `tune.py` searches for them
automatically: it runs this policy on the training seeds 1001-1060 and keeps
weight changes that raise the mean score (a (1+1) evolution strategy). The
result is written to tuned_weights.json, which this file loads (with
POKER_RULES=balanced it loads tuned_weights_balanced.json if there is one:
weights tuned for those rules).

Because each run takes ~0.1 s the tuner can try hundreds of weight vectors.
The learned weights are also a little readable lesson: look at which
modifiers end up with large weights.

What the policy does
--------------------
* Rounds: a narrow beam search (common.beam_round, width W["width"]) with a
  learned money value (W["dollar"] points per $) that decides when to `end`.
* Shop, repeatedly until nothing has positive net utility:
    - buy / swap a modifier (utility = its weight, adjusted by what the next
      round's draws favour: e.g. rose_tint is worth more if we play hearts);
    - level the hand types we actually score with;
    - deck edits: remove cards (more valuable with thin_deck), paint cards
      into our main suit when we play flushes;
    - handsize / discards;
  Net utility = utility - price * W["price"], and spending below the savings
  reserve costs an extra W["reserve_pen"] per dollar.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
from common import MODIFIERS, N_CARDS, SUITS, Game, IllegalMove, RoundCtx  # noqa: E402

TIME_LIMIT = C.time_limit()
WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuned_weights.json")

# Starting point for the tuner (and fallback if the JSON is missing).
DEFAULT_WEIGHTS = {
    "width": 3, "dollar": 4000.0,
    "price": 1.0, "save": 15.0, "reserve_pen": 0.6, "splurge": 3.0,
    "level": 14.0, "level_decay": 0.35,
    "handsize": 9.0, "discards": 7.0,
    "remove": 1.2, "remove_thin": 3.0, "paint": 2.0, "sell_margin": 2.0,
    "suit_bonus": 6.0,
}
for _m, (_price, _) in MODIFIERS.items():
    DEFAULT_WEIGHTS["mod_" + _m] = 1.5 * _price
for _m, _w in {"tricolor": 30, "last_call": 25, "twin_engine": 14, "full_spread": 10, "mirror": 10,
               "short_hand": 8, "rose_tint": 9, "coal_seam": 9, "momentum": 6, "collector": 3,
               "savings": 4, "spare_hands": 5, "ace_dividend": 3, "thin_deck": 4}.items():
    DEFAULT_WEIGHTS["mod_" + _m] = float(_w)


def weights_file(rules=None):
    """tuned_weights.json, or tuned_weights_<rules>.json for another ruleset if it exists."""
    name = C.get_ruleset(rules).name if rules is not None else C.rules_from_env().name
    if name != "classic":
        path = WEIGHTS_FILE.replace(".json", f"_{name}.json")
        if os.path.exists(path):
            return path
    return WEIGHTS_FILE


def load_weights(path=None):
    path = path or weights_file()
    w = dict(DEFAULT_WEIGHTS)
    try:
        with open(path) as f:
            w.update(json.load(f))
    except (OSError, ValueError):
        pass
    return w


class TunedPolicy:
    def __init__(self, weights):
        self.w = weights

    # ---- rounds ------------------------------------------------------------

    def round_solver(self, game, deadline):
        ctx = RoundCtx(game, dollar=self.w["dollar"])
        s = C.beam_round(ctx, width=max(1, int(round(self.w["width"]))), deadline=deadline)
        return s.move_list()

    # ---- shop --------------------------------------------------------------

    def usage(self, game):
        """Points per hand type, and per suit of scored cards, in a greedy
        simulation of the next round (we know its draw order)."""
        g = game.clone()
        g._start_round(g.round + 1)
        ctx = RoundCtx(g, dollar=0, allow_end=False)
        s = ctx.start()
        by_code, by_suit = {}, [0, 0, 0, 0]
        total = 0
        while not s.done:
            t = C.greedy_step(ctx, s)
            mv = t.moves[1]
            if mv.startswith("play"):
                cards = tuple(map(int, mv.split()[1:]))
                pts, code, _ = ctx.score_play(s, cards)
                by_code[code] = by_code.get(code, 0) + pts
                for c in cards:
                    by_suit[g.suit[c]] += pts
                total += pts
            s = t
        return by_code, by_suit, max(total, 1), s.score / g.target

    def options(self, game, info):
        """(utility, moves) for every affordable shop action."""
        w = self.w
        by_code, by_suit, total, _ = info
        out = []
        money = game.money
        R = game.rules
        full = len(game.mods) >= game.inst.slots
        suit_share = [x / max(sum(by_suit), 1) for x in by_suit]
        red, black = suit_share[1] + suit_share[2], suit_share[0] + suit_share[3]

        def mod_utility(name):
            u = w["mod_" + name]
            if name == "rose_tint":
                u += w["suit_bonus"] * (red - 0.5)
            elif name == "coal_seam":
                u += w["suit_bonus"] * (black - 0.5)
            return u

        owned = {m: mod_utility(m) for m, _ in game.mods}
        queue = game.inst.offers[game.round]
        windows = [(0, game.visible_offers())]
        if game.offer_pos + 3 < len(queue):
            windows.append((game.prices()["reroll"], [m for m in queue[game.offer_pos + 3:game.offer_pos + 6]]))
        for extra, names in windows:
            pre = ["reroll"] if extra else []
            for name in names:
                if game.has_mod(name):
                    continue
                u = mod_utility(name)
                price = R.price(name) + extra
                if not full:
                    out.append((u, price, pre + [f"buy {name}"]))
                else:
                    worst = min(owned, key=owned.get)
                    if u > owned[worst] + w["sell_margin"]:
                        refund = R.price(worst) // 2
                        out.append((u - owned[worst], price - refund, [f"sell {worst}"] + pre + [f"buy {name}"]))
        for code, pts in by_code.items():
            lv = game.levels[code]
            u = w["level"] * pts / total / (1 + w["level_decay"] * (lv - 1))
            out.append((u, game.level_price(code), [f"level {code}"]))
        pr = game.prices()
        if game.hand_size < R.max_hand_size:
            out.append((w["handsize"], pr["handsize"], ["handsize"]))
        out.append((w["discards"] / (1 + game.extra_discards), pr["discards"], ["discards"]))
        # deck edits
        deck = [c for c in range(N_CARDS) if game.in_deck[c]]
        if game.deck_size > R.min_deck:
            rc = [0] * 13
            for c in deck:
                rc[game.rank[c]] += 1
            main = max(range(4), key=lambda s: by_suit[s])
            junk = min(deck, key=lambda c: (game.suit[c] == main, rc[game.rank[c]], C.chips(game.rank[c])))
            u = w["remove"] + (w["remove_thin"] if game.has_mod("thin_deck") else 0)
            out.append((u, R.price_remove, [f"remove {junk}"]))
        fl = (by_code.get("FL", 0) + by_code.get("SF", 0) + by_code.get("HF", 0)) / total
        if fl > 0:
            main = max(range(4), key=lambda s: by_suit[s])
            off = [c for c in deck if game.suit[c] != main]
            if off:
                c = max(off, key=lambda c: C.chips(game.rank[c]))
                out.append((w["paint"] * fl * 3, R.price_paint, [f"paint {c} {SUITS[main]}"]))
        return [(u, price, ms) for u, price, ms in out if price <= money]

    def shop(self, game, deadline):
        w = self.w
        moves = []
        info = self.usage(game)
        for _ in range(20):
            best = None
            for u, price, ms in self.options(game, info):
                net = u - w["price"] * price
                below = max(0.0, min(w["save"], game.money) - (game.money - price))
                if u / max(price, 1) < w["splurge"]:
                    net -= w["reserve_pen"] * below
                if net > 0 and (best is None or net > best[0]):
                    best = (net, ms)
            if best is None:
                break
            g = game.clone()
            try:
                for m in best[1]:
                    g.apply(m)
            except IllegalMove:
                break
            for m in best[1]:
                game.apply(m)
            moves += best[1]
            if any(m.startswith(("level", "buy")) for m in best[1]):
                info = self.usage(game)
        game.apply("next")
        return moves + ["next"]


def solve(inst, weights=None, budget=None):
    pol = TunedPolicy(weights or load_weights())
    return C.solve(inst, pol.round_solver, pol.shop, budget=TIME_LIMIT if budget is None else budget)


def main():
    start = time.perf_counter()
    inst = C.parse_instance(sys.stdin.read())
    pol = TunedPolicy(load_weights())
    moves = C.solve(inst, pol.round_solver, pol.shop, start=start, budget=TIME_LIMIT)
    sys.stdout.write(f"{len(moves)}\n" + "\n".join(moves) + "\n")


if __name__ == "__main__":
    main()

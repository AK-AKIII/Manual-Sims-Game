"""Computer players, a move-for-move port of web/bot.js (levels easy, normal, hard).

The online rooms (platform/server/prplatform/rooms.py) run their bot seats with
this module, so a bot plays the same moves on the server as in the browser;
tests/test_poker_run_multiplayer.py checks that the two agree.

    normal   poker_run/baseline.py exactly ("baseline" is an alias)
    easy     plays its best hand every time, never discards, buys at most one
             modifier per shop, never levels
    hard     the baseline plus a look-ahead when it discards and a modifier wish list

`hands` is a {code: count} dict (insertion ordered, like JS objects and Counter)
of the hand types the bot has played; the caller keeps it between rounds.
`avail(name)` restricts what may be bought (the draft hides drafted modifiers).
"""

from itertools import combinations

from .rules import chips
from .sim import Game

LEVELS = {
    "easy": "plays its best hand, never discards, buys little",
    "normal": "the reference greedy bot (poker_run/baseline.py)",
    "hard": "greedy, with a look-ahead on discards and a modifier wish list",
}


def level(name):
    if name == "baseline":
        return "normal"
    return name if name in LEVELS else "normal"


def best_play(game, hand=None):
    """(cards, result) of the highest-scoring play (ties: more cards, then first found)."""
    hand = game.hand if hand is None else hand
    best, best_key = None, None
    for k in range(1, min(5, len(hand)) + 1):
        for cards in combinations(hand, k):
            res = game.evaluate(list(cards))
            if best_key is None or res["points"] > best_key[0] or (res["points"] == best_key[0] and k > best_key[1]):
                best_key = (res["points"], k)
                best = (list(cards), res)
    return best


def _count(hands, code):
    hands[code] = hands.get(code, 0) + 1


def _after_discard(game, cards):
    g = game.clone()
    g.apply("discard " + " ".join(map(str, cards)))
    if g.phase != "play" or not g.hand:
        return -1
    return best_play(g)[1]["points"]


def _discard_candidates(game, best):
    def chips_of(c):
        return chips(game.rank[c])

    scored = set(best[1]["scored"])
    junk = sorted((c for c in game.hand if c not in scored), key=chips_of)
    out = {}

    def add(cards):
        if cards:
            out[" ".join(map(str, sorted(cards)))] = list(cards)

    for k in range(1, min(5, len(junk)) + 1):
        add(junk[:k])
    for k in range(1, min(5, len(game.hand), len(game.pile)) + 1):
        union = game.hand + game.pile[:k]
        keep = set(best_play(game, union)[0])
        rest = sorted((c for c in game.hand if c not in keep), key=chips_of)
        if len(rest) >= k:
            add(rest[:k])
    return list(out.values())


def round_moves(game, lvl, hands=None):
    """Play the current round to its end on `game` (mutated). Returns the moves."""
    hands = {} if hands is None else hands
    lvl = level(lvl)
    moves = []

    def go(m):
        game.apply(m)
        moves.append(m)

    while game.phase == "play":
        cards, res = best_play(game)
        need = game.target - game.round_score
        if lvl != "easy" and res["points"] * game.plays_left < need and game.discards_left > 0:
            if lvl == "normal":
                scored = set(res["scored"])
                junk = sorted((c for c in game.hand if c not in scored), key=lambda c: chips(game.rank[c]))
                if junk:
                    go("discard " + " ".join(map(str, junk[:5])))
                    continue
            else:
                pick, top = None, res["points"]
                for cand in _discard_candidates(game, (cards, res)):
                    v = _after_discard(game, cand)
                    if v > top:
                        top, pick = v, cand
                if pick:
                    go("discard " + " ".join(map(str, pick)))
                    continue
        go("play " + " ".join(map(str, cards)))
        _count(hands, res["hand"])
    return moves


PREFER = ["tricolor", "last_call", "twin_engine", "mirror", "short_hand", "rose_tint", "coal_seam",
          "high_court", "full_spread", "odd_job", "even_split", "low_road", "momentum", "collector", "thin_deck",
          "savings", "spare_hands", "ace_dividend"]


def _prefer_rank(name):
    return PREFER.index(name) if name in PREFER else len(PREFER)


def _most_played(hands):
    code, n = None, 0
    for c, k in hands.items():
        if k > n:
            code, n = c, k
    return code


def shop_moves(game, lvl, hands=None, avail=None):
    """Shop moves for the current shop on `game` (mutated), ending with "next"."""
    hands = {} if hands is None else hands
    lvl = level(lvl)
    avail = avail or (lambda name: True)
    moves = []

    def go(m):
        game.apply(m)
        moves.append(m)

    offers = game.visible_offers()
    if lvl == "hard":
        offers = sorted(offers, key=_prefer_rank)   # stable, like Array.prototype.sort
    bought = 0
    for name in offers:
        if lvl == "easy" and bought:
            break
        if avail(name) and not game.has_mod(name) and len(game.mods) < game.inst.slots \
                and game.money >= game.rules.price(name):
            go("buy " + name)
            bought += 1
    if lvl != "easy":
        code = _most_played(hands)
        if code:
            while game.money >= game.level_price(code):
                go("level " + code)
    go("next")
    return moves


def solve(inst, rules=None, lvl="normal"):
    game = Game(inst, rules)
    hands, moves = {}, []
    while not game.over:
        moves += round_moves(game, lvl, hands) if game.phase == "play" else shop_moves(game, lvl, hands)
    return moves

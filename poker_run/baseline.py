"""Reference solver: greedy, one move at a time, no look-ahead.

    python -m poker_run.baseline < input.txt > output.txt

It exists to show the problem isn't trivial and to give contestants a score
to beat. It never looks at future draws.
"""

import sys
from collections import Counter
from itertools import combinations

from .instance import parse_instance
from .rules import MODIFIERS, chips
from .sim import Game


def best_play(game):
    """The highest-scoring set of cards to play now (ties: play more cards)."""
    best = None
    for k in range(1, min(5, len(game.hand)) + 1):
        for cards in combinations(game.hand, k):
            res = game.evaluate(list(cards))
            key = (res["points"], k)
            if best is None or key > best[0]:
                best = (key, list(cards), res)
    return best[1], best[2]


def round_moves(game, played):
    moves = []
    while game.phase == "play":
        cards, res = best_play(game)
        need = game.target - game.round_score
        if res["points"] * game.plays_left < need and game.discards_left > 0:
            junk = sorted((c for c in game.hand if c not in res["scored"]), key=lambda c: chips(game.rank[c]))
            if junk:
                move = "discard " + " ".join(map(str, junk[:5]))
                game.apply(move)
                moves.append(move)
                continue
        move = "play " + " ".join(map(str, cards))
        game.apply(move)
        moves.append(move)
        played[res["hand"]] += 1
    return moves


def shop_moves(game, played):
    moves = []

    def do(move):
        game.apply(move)
        moves.append(move)

    for name in game.visible_offers():
        if not game.has_mod(name) and len(game.mods) < game.inst.slots and game.money >= MODIFIERS[name][0]:
            do(f"buy {name}")
    if played:
        code = played.most_common(1)[0][0]
        while game.money >= game.level_price(code):
            do(f"level {code}")
    do("next")
    return moves


def solve(inst):
    game = Game(inst)
    played = Counter()
    moves = []
    while not game.over:
        moves += round_moves(game, played) if game.phase == "play" else shop_moves(game, played)
    return moves


def main():
    moves = solve(parse_instance(sys.stdin.read()))
    print(len(moves))
    print("\n".join(moves))


if __name__ == "__main__":
    main()

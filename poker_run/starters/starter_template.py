# Template for build_starter.py: the @...@ markers are replaced with package code.
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

# @RULES@


# ---- input ----------------------------------------------------------------------

# @INPUT@


# ---- simulator (identical to the judge's) ----------------------------------------

# @SIM@


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

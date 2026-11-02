"""Beam search inside each round, plus the simulation-guided shop.

    python3 poker_run/solvers/beam_search.py < input.txt > output.txt

The idea
--------
Inside a round nothing is random: the input gives the draw order, so playing
or discarding k cards always draws the next k cards of the pile. A round is a
deterministic puzzle of at most plays + discards (~7-9) moves, and we can
search it exactly by simulating moves (common.RoundCtx does that on a light
state instead of cloning the whole Game).

The full game tree is far too big (each state has ~30 sensible plays and ~15
sensible discards), so we use *beam search*:

    layer = [start state]
    repeat:
        expand every state of the layer by every candidate move
        finished rounds (last play, or `end` once the target is met) are
            scored exactly: clear the round >> money earned >> points
        keep the `width` best unfinished children (by a heuristic) as the
            next layer, after merging duplicates (same hand, same position
            in the pile, same plays/discards left)

The heuristic of an unfinished state is "points so far + what the best play
in hand would add + a share of that for each remaining play"; if one more
play clears the round the state is valued like the finished round it leads
to. The beam starts from the greedy solution, so it never returns anything
worse than greedy.

Money vs points: every $ earned in an early round helps build the engine,
so a round's terminal value counts DOLLAR points per $. Ending a round early
saves plays ($1 each); continuing scores more points. In the last round
money is worthless and the beam maximises points (e.g. it keeps the best
hand for the last play when we own last_call).

Shop: common.Shop (simulates the next rounds with the known draw orders to
judge every purchase). Its parameters are below.

Main parameters
---------------
WIDTH     beam width (states kept per layer). Wider = better but slower.
DOLLAR    points one $ is worth when finishing a round.
SHOP      parameters of common.Shop (horizon = rounds simulated per purchase).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

TIME_LIMIT = C.time_limit()
WIDTH = 10
DOLLAR = 5000.0
SHOP = dict(horizon=5)           # simulate 5 rounds ahead per purchase


def round_solver(game, deadline):
    ctx = C.RoundCtx(game, dollar=DOLLAR)
    best = C.beam_round(ctx, width=WIDTH, deadline=deadline)
    return best.move_list()


def solve(inst, budget=None):
    return C.solve(inst, round_solver, C.Shop(SHOP), budget=TIME_LIMIT if budget is None else budget)


def main():
    start = time.perf_counter()
    inst = C.parse_instance(sys.stdin.read())
    moves = C.solve(inst, round_solver, C.Shop(SHOP), start=start, budget=TIME_LIMIT)
    sys.stdout.write(f"{len(moves)}\n" + "\n".join(moves) + "\n")


if __name__ == "__main__":
    main()

"""Flat Monte Carlo: judge each move by random rollouts to the end of the round.

    python3 poker_run/solvers/monte_carlo.py < input.txt > output.txt

The idea
--------
At every decision inside a round:

    for each candidate move m (best plays, discards, `end`):
        repeat ROLLOUTS times:
            apply m, then finish the round with a randomised policy
            (epsilon-greedy: the greedy move, or with probability EPS a
            random candidate move)
            record the finished round's value (clear >> money >> points)
    pick the move whose rollouts did best, apply it, repeat

No heuristic evaluation is needed: the value of a move is measured by
actually playing the round out. The rollout policy matters a lot: a purely
random policy would almost never clear a round, so rollouts are "greedy with
noise".

Max or mean?
------------
In a game with hidden randomness (unknown draws) you must average over
rollouts: one lucky rollout says nothing about what will happen. Poker Run is
fully deterministic: the input reveals every future card. The only
randomness in a rollout is *our own* choice of moves, and any rollout we
simulated is a sequence of moves we can really play. So the best rollout of a
move is a proven lower bound on what that move achieves, and we choose by
MAX. (Mean would measure how forgiving a move is to our own random mistakes,
which is irrelevant.) Going one step further, we remember the best complete
sequence found so far (the "incumbent") and keep it unless a later search
beats it: the solver can never get worse than its best rollout.

Determinism: rollouts use random.Random with a fixed seed per round, and the
number of rollouts is fixed (ROLLOUTS), so the output doesn't depend on the
machine; the deadline only acts as a safety net.

Main parameters
---------------
ROLLOUTS  rollouts per candidate move per decision.
EPS       probability of a random move inside a rollout.
MAX_CANDS number of candidate moves considered per decision.
DOLLAR    points one $ is worth when a round ends (see beam_search.py).
"""

import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

TIME_LIMIT = C.time_limit()
ROLLOUTS = 4
EPS = 0.25
MAX_CANDS = 10
DOLLAR = 5000.0
SHOP = dict(horizon=3)           # shorter than beam_search: leaves time for rollouts


def candidate_moves(ctx, s):
    """The candidate successor states of s, most promising first."""
    out = []
    if ctx.can_end(s):
        out.append(ctx.end(s))
    for pts, cards, code, money in C.play_candidates(ctx, s)[:MAX_CANDS // 2 + 1]:
        out.append(ctx.play(s, cards, (pts, code, money)))
    for d in C.discard_candidates(ctx, s)[:MAX_CANDS // 2]:
        out.append(ctx.discard(s, d))
    return out[:MAX_CANDS]


def rollout(ctx, s, rng):
    """Finish the round from s with epsilon-greedy moves."""
    while not s.done:
        if rng.random() < EPS:
            s = rng.choice(candidate_moves(ctx, s))
        else:
            s = C.greedy_step(ctx, s)
    return s


def round_solver(game, deadline):
    ctx = C.RoundCtx(game, dollar=DOLLAR)
    rng = random.Random(1000 + game.round)
    s = ctx.start()
    incumbent = C.greedy_round(ctx, s)          # best complete round so far
    inc_v = ctx.terminal_value(incumbent)
    depth = 0
    while not s.done:
        children = candidate_moves(ctx, s)
        for child in children:
            if child.done:
                v, fin = ctx.terminal_value(child), child
            else:
                fin = C.greedy_round(ctx, child)          # one greedy rollout ...
                v = ctx.terminal_value(fin)
                for _ in range(ROLLOUTS - 1):             # ... and some noisy ones
                    f = rollout(ctx, child, rng)
                    fv = ctx.terminal_value(f)
                    if fv > v:
                        v, fin = fv, f
            if v > inc_v:                                 # max, not mean (see top)
                incumbent, inc_v = fin, v
            if time.perf_counter() > deadline:
                break
        if time.perf_counter() > deadline:
            break
        # Commit to the next move of the best sequence known, and search again
        # from there: deeper decisions get their own rollouts.
        nxt = incumbent.move_list()[depth]
        s = next((c for c in children if c.moves[1] == nxt), None) or ctx.apply_move(s, nxt)
        depth += 1
    # The incumbent is the best complete sequence ever simulated.
    return incumbent.move_list()


def solve(inst, budget=None):
    return C.solve(inst, round_solver, C.Shop(SHOP), budget=TIME_LIMIT if budget is None else budget)


def main():
    start = time.perf_counter()
    inst = C.parse_instance(sys.stdin.read())
    moves = C.solve(inst, round_solver, C.Shop(SHOP), start=start, budget=TIME_LIMIT)
    sys.stdout.write(f"{len(moves)}\n" + "\n".join(moves) + "\n")


if __name__ == "__main__":
    main()

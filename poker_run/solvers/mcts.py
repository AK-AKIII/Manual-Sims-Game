"""Monte Carlo Tree Search (UCT) inside each round.

    python3 poker_run/solvers/mcts.py < input.txt > output.txt

The idea
--------
Flat Monte Carlo (monte_carlo.py) spends the same effort on every candidate
move. MCTS grows a search tree and spends its rollouts where they look most
promising, while still trying the others now and then. One iteration:

    1. selection   from the root, repeatedly go to the child maximising
                   UCB = Q(child) + C * sqrt(ln N(parent) / N(child))
                   (Q = how good the child has been, the square root = how
                   little we know about it: exploitation + exploration)
    2. expansion   when a node may have one more child (see widening below),
                   add the next candidate move as a new child
    3. rollout     finish the round from the new child with an
                   epsilon-greedy policy (as in monte_carlo.py)
    4. backup      add the rollout's reward to every node on the path

Rewards are squashed into [0, 1]: not clearing the round gives at most 0.6
(more points = closer to 0.6), clearing gives 0.6 + up to 0.4 for the extra
points and money.

Progressive widening. A Poker Run state has ~40 candidate moves, and most
are bad. Instead of expanding all of them, a node with N visits may have at
most 1 + PW_C * N ** PW_ALPHA children; the candidates are ordered by a cheap
prior (finish if allowed, then plays by points, then discards), so the tree
first looks at the obvious moves and widens as a node proves important.

Deterministic game, so MAX matters. Q mixes the mean reward with the best
reward ever seen below the node (Q = (1-BETA) * mean + BETA * max): the best
rollout is a real, replayable sequence (see monte_carlo.py). And we return
the best complete round ever simulated (the incumbent), not merely the most
visited path.

Plan, act, re-plan: we search ITERS iterations, commit to the incumbent's
next move, keep that child's subtree as the new root (its statistics are
reused), and search again until the round ends.

Shop: common.Shop (simulation-guided), with a shorter horizon than
beam_search.py to leave time for the tree search.

Main parameters
---------------
ITERS     iterations per decision.     C       exploration constant.
PW_C, PW_ALPHA  progressive widening.   BETA    weight of max in Q.
EPS       rollout randomness.           DOLLAR  points per $ (see beam_search.py).
"""

import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

TIME_LIMIT = C.time_limit()
ITERS = 30
C_UCB = 0.4
PW_C, PW_ALPHA = 1.5, 0.5
BETA = 0.5
EPS = 0.2
DOLLAR = 5000.0
SHOP = dict(horizon=3)           # shorter than beam_search: leaves time for rollouts


class Node:
    __slots__ = ("state", "moves", "children", "n", "total", "best")

    def __init__(self, state):
        self.state = state
        self.moves = None        # candidate successor states, prior order (lazy)
        self.children = []
        self.n = 0
        self.total = 0.0
        self.best = 0.0

    def q(self):
        return (1 - BETA) * self.total / self.n + BETA * self.best


def candidates(ctx, s):
    out = []
    if ctx.can_end(s):
        out.append(ctx.end(s))
    for pts, cards, code, money in C.play_candidates(ctx, s)[:8]:
        out.append(ctx.play(s, cards, (pts, code, money)))
    for d in C.discard_candidates(ctx, s)[:8]:
        out.append(ctx.discard(s, d))
    return out


def reward(ctx, s):
    """Squash a finished round into [0, 1]."""
    if ctx.final:
        return min(1.0, s.score / (4.0 * ctx.target))
    if s.score < ctx.target:
        return 0.6 * s.score / ctx.target
    extra = s.score - ctx.target + ctx.dollar * ctx.reward(s)
    return 0.6 + 0.4 * min(1.0, extra / (3.0 * ctx.target))


class Search:
    def __init__(self, ctx, rng):
        self.ctx, self.rng = ctx, rng
        self.incumbent = C.greedy_round(ctx)
        self.inc_v = ctx.terminal_value(self.incumbent)

    def offer(self, s):
        v = self.ctx.terminal_value(s)
        if v > self.inc_v:
            self.incumbent, self.inc_v = s, v

    def rollout(self, s):
        ctx, rng = self.ctx, self.rng
        while not s.done:
            if rng.random() < EPS:
                s = rng.choice(candidates(ctx, s))
            else:
                s = C.greedy_step(ctx, s)
        self.offer(s)
        return reward(ctx, s)

    def iterate(self, root):
        ctx = self.ctx
        path, node = [root], root
        while True:
            if node.state.done:
                r = reward(ctx, node.state)
                self.offer(node.state)
                break
            if node.moves is None:
                node.moves = candidates(ctx, node.state)
            limit = 1 + int(PW_C * node.n ** PW_ALPHA)
            if len(node.children) < min(limit, len(node.moves)):
                child = Node(node.moves[len(node.children)])      # expansion
                node.children.append(child)
                path.append(child)
                r = self.rollout(child.state)
                break
            if not node.children:
                r = self.rollout(node.state)
                break
            ln = math.log(node.n + 1)
            node = max(node.children,
                       key=lambda c: c.q() + C_UCB * math.sqrt(ln / c.n) if c.n else float("inf"))
            path.append(node)
        for nd in path:                                            # backup
            nd.n += 1
            nd.total += r
            nd.best = max(nd.best, r)


def round_solver(game, deadline):
    ctx = C.RoundCtx(game, dollar=DOLLAR)
    search = Search(ctx, random.Random(2000 + game.round))
    root = Node(ctx.start())
    depth = 0
    while not root.state.done and time.perf_counter() < deadline:
        for _ in range(ITERS):
            search.iterate(root)
            if time.perf_counter() > deadline:
                break
        # commit to the next move of the best sequence found; reuse its subtree
        moves = search.incumbent.move_list()
        if depth >= len(moves):
            break
        nxt = moves[depth]
        child = next((c for c in root.children if c.state.moves[1] == nxt), None)
        root = child or Node(ctx.apply_move(root.state, nxt))
        depth += 1
    return search.incumbent.move_list()


def solve(inst, budget=None):
    return C.solve(inst, round_solver, C.Shop(SHOP), budget=TIME_LIMIT if budget is None else budget)


def main():
    start = time.perf_counter()
    inst = C.parse_instance(sys.stdin.read())
    moves = C.solve(inst, round_solver, C.Shop(SHOP), start=start, budget=TIME_LIMIT)
    sys.stdout.write(f"{len(moves)}\n" + "\n".join(moves) + "\n")


if __name__ == "__main__":
    main()

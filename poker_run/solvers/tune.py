"""Tune the weights of tuned.py with a simple evolution strategy.

    python3 poker_run/solvers/tune.py [--minutes 6] [--workers 6] [--seeds 1001-1060]

Algorithm: (1+1)-ES with the 1/5th success rule, a classic of black-box
optimisation that fits in a few lines:

    w      = current best weights, f = mean score of w on the training seeds
    repeat until time runs out:
        w' = w with each weight multiplied by exp(sigma * N(0,1))
             (multiplicative noise: weights are positive and of very
             different sizes, so we mutate them on a log scale)
        f' = mean score of w' on the training seeds
        if f' > f: w, f = w', f'; sigma *= 1.5     (success: be bolder)
        else:                       sigma *= 0.9    (failure: be careful)

Each candidate is evaluated on ALL training seeds (1001-1060). Using the same
seeds for every candidate keeps the comparison fair (common random numbers);
the game is deterministic anyway. Never tune on the public seeds 1-10: the
point of the tuned solver is to generalise to tests it has not seen.

Runs are evaluated with a large time budget, so results don't depend on how
busy the machine is; tuned.py itself takes about 0.2 s per test.
The best weights are written to tuned_weights.json after every improvement,
so you can stop the tuner at any time (Ctrl-C).
"""

import argparse
import json
import math
import os
import random
import statistics
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import tuned  # noqa: E402
from poker_run.instance import generate  # noqa: E402

INTEGER = {"width"}
BOUNDS = {"width": (1, 6)}


def run_seed(args):
    seed, weights = args
    inst = generate(seed)
    moves = tuned.solve(inst, weights, budget=30.0)
    g = C.Game(inst)
    for m in moves:
        g.apply(m)
    return g.total, g.cleared


def evaluate(pool, weights, seeds):
    res = pool.map(run_seed, [(s, weights) for s in seeds])
    return statistics.mean(r[0] for r in res), statistics.mean(r[1] for r in res)


def mutate(w, sigma, rng, frac=0.3):
    """Perturb a random subset (~frac) of the weights on a log scale."""
    out = dict(w)
    keys = list(w)
    chosen = [k for k in keys if rng.random() < frac] or [rng.choice(keys)]
    for k in chosen:
        if k in INTEGER:
            lo, hi = BOUNDS[k]
            out[k] = min(hi, max(lo, int(w[k]) + rng.choice((-1, 1))))
        else:
            out[k] = w[k] * math.exp(sigma * rng.gauss(0, 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=6.0)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--seeds", default="1001-1060")
    ap.add_argument("--fresh", action="store_true", help="start from DEFAULT_WEIGHTS, not the JSON")
    ap.add_argument("--out", default=tuned.WEIGHTS_FILE)
    args = ap.parse_args()
    lo, hi = map(int, args.seeds.split("-"))
    seeds = list(range(lo, hi + 1))
    assert not set(seeds) & set(range(1, 11)), "never tune on the public seeds 1-10"
    rng = random.Random(12345)
    w = dict(tuned.DEFAULT_WEIGHTS) if args.fresh else tuned.load_weights()
    sigma = 0.3
    end = time.time() + 60 * args.minutes
    with Pool(args.workers) as pool:
        f, cl = evaluate(pool, w, seeds)
        print(f"start: mean {f:.0f}  cleared {cl:.2f}", flush=True)
        it = 0
        while time.time() < end:
            it += 1
            cand = mutate(w, sigma, rng)
            fc, clc = evaluate(pool, cand, seeds)
            if fc > f:
                w, f, cl = cand, fc, clc
                sigma = min(1.0, sigma * 1.5)
                with open(args.out, "w") as fh:
                    json.dump({k: (round(v, 4) if isinstance(v, float) else v) for k, v in sorted(w.items())},
                              fh, indent=1)
                    fh.write("\n")
                print(f"iter {it:4}: improved to {f:.0f} (cleared {cl:.2f}), sigma {sigma:.2f}", flush=True)
            else:
                sigma = max(0.05, sigma * 0.9)
    print(f"done: mean {f:.0f}  cleared {cl:.2f}  ({it} candidates)")


if __name__ == "__main__":
    main()

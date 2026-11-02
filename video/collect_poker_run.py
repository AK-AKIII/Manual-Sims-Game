"""Run the solvers and save every number the Poker Run video shows.

    python video/collect_poker_run.py            # writes video/poker_run_data.json

Runs each solver through `python -m poker_run batch` on seeds 1-20 (2 s per
test), builds the head-to-head table with the same code as `versus`, and
saves the seed-7 output the video demos.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from poker_run import check, format_instance, generate  # noqa: E402
from poker_run.__main__ import versus_table  # noqa: E402

SEEDS = "1-20"
DEMO_SEED = 7
SOLVERS = {
    "Greedy": "python3 -m poker_run.baseline",
    "Beam search": "python3 poker_run/solvers/beam_search.py",
    "Monte Carlo": "python3 poker_run/solvers/monte_carlo.py",
    "MCTS": "python3 poker_run/solvers/mcts.py",
    "Tuned": "python3 poker_run/solvers/tuned.py",
}


def batch(command):
    out = subprocess.run([sys.executable, "-m", "poker_run", "batch", "--seeds", SEEDS, "--solver", command, "--json"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main():
    results = {}
    for name, command in SOLVERS.items():
        print(f"running {name}...", flush=True)
        results[name] = batch(command)
        s = results[name]["summary"]
        print(f"  mean {s['mean']:.0f}, cleared {s['mean_cleared']:.2f}, invalid {s['invalid']}, max time {s['max_time']:.2f}s")
    names = list(SOLVERS)
    scores = {n: [r["score"] for r in results[n]["results"]] for n in names}
    ranking, wins = versus_table(names, scores)

    demo_input = format_instance(generate(DEMO_SEED))
    demo_output = subprocess.run(SOLVERS["Beam search"].split(), cwd=ROOT, input=demo_input, capture_output=True,
                                 text=True, check=True).stdout
    data = {
        "seeds": SEEDS,
        "solvers": {n: {"command": SOLVERS[n], "summary": results[n]["summary"],
                        "scores": scores[n], "cleared": [r.get("cleared", 0) for r in results[n]["results"]]}
                    for n in names},
        "ranking": ranking,
        "wins": wins,
        "demo": {"seed": DEMO_SEED, "input": demo_input, "output": demo_output, "verdict": check(demo_input, demo_output)[1]},
    }
    path = Path(__file__).with_name("poker_run_data.json")
    path.write_text(json.dumps(data, indent=1))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

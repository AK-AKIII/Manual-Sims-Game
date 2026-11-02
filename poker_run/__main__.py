"""Command line, mirroring Harvest Craft's gen / check / batch tools.

    python -m poker_run gen --seed 7 > in.txt
    python -m poker_run gen --seeds 1-10 --out-dir tests/fixtures/poker_run
    python -m poker_run check in.txt out.txt [--no-json]
    python -m poker_run batch --seeds 1-10 --solver "python3 my_solver.py" [--time-limit 2] [--json]
    python -m poker_run versus --solvers "python3 a.py" "python3 b.py" --names a b --seeds 1-20
"""

import argparse
import json
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path

from .checker import check
from .instance import format_instance, generate


def seed_range(text):
    seeds = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        seeds += range(int(lo), int(hi or lo) + 1)
    return seeds


def describe(v):
    if not v["valid"]:
        return f"INVALID  {v['error']}"
    end = "won" if v["cleared"] == v["rounds"] else ("stopped" if not v["finished"] else "out")
    return f"score {v['score']:>9}  cleared {v['cleared']}/{v['rounds']} ({end})  moves {v['moves']}"


def cmd_gen(args):
    params = {"rounds": args.rounds, "hand_size": args.hand_size, "plays": args.plays, "discards": args.discards}
    if args.seeds:
        out = Path(args.out_dir or ".")
        out.mkdir(parents=True, exist_ok=True)
        for s in seed_range(args.seeds):
            (out / f"{s:04d}.txt").write_text(format_instance(generate(s, **params)))
        return 0
    sys.stdout.write(format_instance(generate(args.seed, **params)))
    return 0


def cmd_check(args):
    code, v = check(Path(args.input).read_text(), Path(args.output).read_text())
    print(describe(v) if args.no_json else json.dumps(v))
    print(describe(v), file=sys.stderr) if not args.no_json else None
    return code


def run_solver(solver, input_text, time_limit):
    start = time.perf_counter()
    try:
        proc = subprocess.run(shlex.split(solver), input=input_text, capture_output=True, text=True,
                              timeout=time_limit)
    except subprocess.TimeoutExpired:
        return None, time.perf_counter() - start, "time limit exceeded"
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        return None, elapsed, f"solver exited with {proc.returncode}: {proc.stderr.strip()[-300:]}"
    return proc.stdout, elapsed, proc.stderr


def cmd_batch(args):
    rows = []
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for s in seed_range(args.seeds):
        input_text = format_instance(generate(s))
        output, elapsed, note = run_solver(args.solver, input_text, args.time_limit)
        if output is None:
            v = {"valid": False, "score": 0, "error": note}
        else:
            _, v = check(input_text, output)
            if out_dir:
                (out_dir / f"{s:04d}.in.txt").write_text(input_text)
                (out_dir / f"{s:04d}.{args.name}.out.txt").write_text(output)
                if args.write_expected:
                    (out_dir / f"{s:04d}.{args.name}.expected.json").write_text(json.dumps(v, indent=2) + "\n")
        v = {"seed": s, "time": round(elapsed, 3), **v}
        rows.append(v)
        if not args.json:
            print(f"seed {s:>4}  {describe(v)}  {elapsed:6.2f}s")
            if args.verbose and output is not None and note:
                print("  " + note.strip().replace("\n", "\n  "))
    scores = [r["score"] for r in rows]
    summary = {
        "seeds": len(rows),
        "total": sum(scores),
        "mean": statistics.mean(scores),
        "mean_cleared": statistics.mean(r.get("cleared", 0) for r in rows),
        "invalid": sum(1 for r in rows if not r["valid"]),
        "max_time": max(r["time"] for r in rows),
    }
    if args.json:
        print(json.dumps({"summary": summary, "results": rows}))
    else:
        print(f"total {summary['total']}  mean {summary['mean']:.1f}  mean cleared {summary['mean_cleared']:.2f}"
              f"  invalid {summary['invalid']}  max time {summary['max_time']:.2f}s")
    return 0


def versus_table(names, scores):
    """Head-to-head results. scores[name] is a list of per-seed scores (same seeds for all).

    Returns (rows, wins): rows are the ranking, wins[a][b] is how many seeds a beat b on.
    """
    seeds = len(scores[names[0]])
    wins = {a: {b: sum(1 for i in range(seeds) if scores[a][i] > scores[b][i]) for b in names} for a in names}
    rows = []
    for a in names:
        ranks = [1 + sum(1 for b in names if scores[b][i] > scores[a][i]) for i in range(seeds)]
        rows.append({
            "name": a,
            "mean": statistics.mean(scores[a]),
            "mean_rank": statistics.mean(ranks),
            "firsts": sum(1 for r in ranks if r == 1),
            "duels_won": sum(1 for b in names if b != a and wins[a][b] > wins[b][a]),
        })
    rows.sort(key=lambda r: (r["mean_rank"], -r["mean"]))
    return rows, wins


def cmd_versus(args):
    names = args.names or [f"solver{i + 1}" for i in range(len(args.solvers))]
    if len(names) != len(args.solvers) or len(names) < 2:
        sys.exit("give 2 or more --solvers, and as many --names if you name them")
    seeds = seed_range(args.seeds)
    scores = {n: [] for n in names}
    for s in seeds:
        input_text = format_instance(generate(s))
        line = []
        for name, solver in zip(names, args.solvers):
            output, _, note = run_solver(solver, input_text, args.time_limit)
            v = check(input_text, output)[1] if output is not None else {"valid": False, "score": 0}
            scores[name].append(v["score"])
            line.append(f"{name} {v['score']}" + ("" if v["valid"] else " (invalid)"))
        if not args.json:
            print(f"seed {s:>4}  " + "   ".join(line))
    rows, wins = versus_table(names, scores)
    if args.json:
        print(json.dumps({"seeds": seeds, "scores": scores, "ranking": rows, "wins": wins}))
        return 0
    print(f"\nRanking over {len(seeds)} seeds (each seed: higher score wins; ties count for no one)")
    for i, r in enumerate(rows, 1):
        print(f"{i:>2}. {r['name']:<16} mean rank {r['mean_rank']:.2f}   first on {r['firsts']:>3} seeds"
              f"   mean score {r['mean']:>10.1f}   head-to-heads won {r['duels_won']}/{len(names) - 1}")
    width = max(len(n) for n in names) + 2
    print("\nHead to head: seeds the row solver beat the column solver on")
    print(" " * width + "".join(f"{n:>{width}}" for n in names))
    for a in names:
        print(f"{a:<{width}}" + "".join(f"{'-' if a == b else wins[a][b]:>{width}}" for b in names))
    return 0


def main():
    ap = argparse.ArgumentParser(prog="python -m poker_run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="write a test case")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--seeds", help="a range like 1-50: write one file per seed")
    g.add_argument("--out-dir")
    for opt in ("rounds", "hand-size", "plays", "discards"):
        g.add_argument(f"--{opt}", type=int)

    c = sub.add_parser("check", help="score an output")
    c.add_argument("input")
    c.add_argument("output")
    c.add_argument("--no-json", action="store_true", help="human-readable verdict only")

    b = sub.add_parser("batch", help="run a solver on many seeds")
    b.add_argument("--seeds", default="1-10")
    b.add_argument("--solver", required=True, help='command line, e.g. "python3 my_solver.py"')
    b.add_argument("--time-limit", type=float, default=2.0, help="seconds per test (default 2)")
    b.add_argument("--json", action="store_true")
    b.add_argument("--verbose", action="store_true", help="show the solver's stderr")
    b.add_argument("--out-dir", help="save inputs, outputs and verdicts here")
    b.add_argument("--name", default="solver", help="label used in saved file names")
    b.add_argument("--write-expected", action="store_true", help="also save each verdict as JSON")

    v = sub.add_parser("versus", help="run 2 or more solvers on the same seeds, head to head")
    v.add_argument("--solvers", nargs="+", required=True, help='command lines, e.g. "python3 a.py" "python3 b.py"')
    v.add_argument("--names", nargs="+", help="a short name for each solver")
    v.add_argument("--seeds", default="1-10")
    v.add_argument("--time-limit", type=float, default=2.0)
    v.add_argument("--json", action="store_true")

    args = ap.parse_args()
    sys.exit({"gen": cmd_gen, "check": cmd_check, "batch": cmd_batch, "versus": cmd_versus}[args.cmd](args))


if __name__ == "__main__":
    main()

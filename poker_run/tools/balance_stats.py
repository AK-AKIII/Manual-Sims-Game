"""Balance statistics: run solvers on many seeds and report what wins points.

    python3 -m poker_run.tools.balance_stats --seeds 1001-1100 --rules classic \
        --solvers baseline tuned beam_search --json out.json

For each solver and seed the solver's output is replayed on the simulator and
we record:

  * score, rounds cleared (and its distribution), wins;
  * which hand types score the points (share of all points, share of plays);
  * for every modifier: in how many runs it was bought, and how much it
    contributed: the points a play loses when that one modifier is taken
    away (leave-one-out), summed over the run and divided by the run's
    points. Money modifiers (ace_dividend, savings, spare_hands) are
    measured in dollars earned instead;
  * levels bought per hand type, deck edits (remove / paint / promote),
    handsize / discards / reroll purchases, `end` moves (and how many of them
    were in the last round).

Solvers run as subprocesses with the environment variables POKER_RULES (the
ruleset) and POKER_TIME (time budget per run, default 3 s so that a busy
machine does not trigger the solvers' wall-time safety net; the solvers are
work-bounded, so their output does not depend on it).
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from poker_run.checker import parse_output  # noqa: E402
from poker_run.instance import format_instance, generate  # noqa: E402
from poker_run.rules import HAND_CODES, MODIFIER_IDS, get_ruleset  # noqa: E402
from poker_run.sim import Game  # noqa: E402

SOLVERS = {
    "baseline": [sys.executable, "-m", "poker_run.baseline"],
    "tuned": [sys.executable, str(ROOT / "poker_run/solvers/tuned.py")],
    "beam_search": [sys.executable, str(ROOT / "poker_run/solvers/beam_search.py")],
    "monte_carlo": [sys.executable, str(ROOT / "poker_run/solvers/monte_carlo.py")],
    "mcts": [sys.executable, str(ROOT / "poker_run/solvers/mcts.py")],
}
MONEY_MODS = ("ace_dividend", "savings", "spare_hands")


def seed_range(text):
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += range(int(lo), int(hi or lo) + 1)
    return out


def run_solver(cmd, input_text, rules, budget):
    env = dict(os.environ, POKER_RULES=rules, POKER_TIME=str(budget))
    proc = subprocess.run(cmd, input=input_text, capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-500:])
    return parse_output(proc.stdout)


def replay(inst, moves, rules):
    """Replay a solver's moves and collect the statistics of one run."""
    R = get_ruleset(rules)
    g = Game(inst, rules=rules)
    st = {"hand_points": Counter(), "hand_plays": Counter(), "mod_points": Counter(), "mod_money": Counter(),
          "bought": set(), "levels": Counter(), "edits": Counter(), "shop": Counter(), "ends": 0,
          "ends_last": 0, "plays": 0}
    for move in moves:
        tok = move.split()
        cmd = tok[0]
        if cmd == "play":
            cards = [int(t) for t in tok[1:]]
            res = g.evaluate(cards)
            st["hand_points"][res["hand"]] += res["points"]
            st["hand_plays"][res["hand"]] += 1
            st["plays"] += 1
            mods = g.mods
            for k, (name, _) in enumerate(mods):
                g.mods = mods[:k] + mods[k + 1:]
                without = g.evaluate(cards)
                st["mod_points"][name] += res["points"] - without["points"]
                if name == "ace_dividend":
                    st["mod_money"][name] += res["money"] - without["money"]
            g.mods = mods
        elif cmd == "end":
            st["ends"] += 1
            if g.round == inst.rounds - 1:
                st["ends_last"] += 1
        elif cmd == "buy":
            st["bought"].add(tok[1])
        elif cmd == "level":
            st["levels"][tok[1]] += 1
        elif cmd in ("remove", "paint", "promote"):
            st["edits"][cmd] += 1
        elif cmd in ("handsize", "discards", "reroll", "sell"):
            st["shop"][cmd] += 1
        money_before = g.money
        was_play = g.phase == "play"
        cleared = g.cleared
        play_money = res["money"] if cmd == "play" else 0
        g.apply(move)
        if was_play and g.cleared > cleared:
            # Money modifiers at the end of a round: what they added to the reward.
            m = money_before + play_money
            if g.has_mod("savings"):
                st["mod_money"]["savings"] += (min(m // R.savings_interest_per, R.interest_cap + R.effects["savings"])
                                               - min(m // R.interest_per, R.interest_cap))
            if g.has_mod("spare_hands"):
                st["mod_money"]["spare_hands"] += R.effects["spare_hands"] * g.discards_left
    st["score"], st["cleared"], st["rounds"] = g.total, g.cleared, inst.rounds
    st["bought"] = sorted(st["bought"])
    return st


def one(job):
    solver, seed, rules, budget = job
    inst = generate(seed)
    moves = run_solver(SOLVERS[solver], format_instance(inst), rules, budget)
    return solver, seed, replay(inst, moves, rules)


def summarise(runs):
    n = len(runs)
    scores = [r["score"] for r in runs]
    total = sum(scores) or 1
    hp, hn, mp, mm = Counter(), Counter(), Counter(), Counter()
    bought, levels, edits, shop = Counter(), Counter(), Counter(), Counter()
    share_by_mod = defaultdict(list)
    plays = ends = ends_last = edit_runs = 0
    cleared = Counter()
    for r in runs:
        hp.update(r["hand_points"])
        hn.update(r["hand_plays"])
        mp.update(r["mod_points"])
        mm.update(r["mod_money"])
        bought.update(r["bought"])
        levels.update(r["levels"])
        edits.update(r["edits"])
        shop.update(r["shop"])
        plays += r["plays"]
        ends += r["ends"]
        ends_last += r["ends_last"]
        edit_runs += 1 if sum(r["edits"].values()) else 0
        cleared[r["cleared"]] += 1
        for name in r["bought"]:
            share_by_mod[name].append(r["mod_points"].get(name, 0) / max(r["score"], 1))
    rounds = runs[0]["rounds"]
    return {
        "runs": n,
        "mean": statistics.mean(scores),
        "median": statistics.median(scores),
        "mean_cleared": statistics.mean(r["cleared"] for r in runs),
        "cleared_hist": [cleared[k] for k in range(rounds + 1)],
        "won": cleared[rounds],
        "hand_point_share": {c: hp[c] / total for c in HAND_CODES},
        "hand_play_share": {c: hn[c] / max(plays, 1) for c in HAND_CODES},
        "mod_bought_runs": {m: bought[m] for m in MODIFIER_IDS},
        "mod_point_share": {m: mp[m] / total for m in MODIFIER_IDS},
        # mean share of a run's points that depends on the modifier, over the runs that bought it
        "mod_share_when_owned": {m: (statistics.mean(share_by_mod[m]) if share_by_mod[m] else 0.0)
                                 for m in MODIFIER_IDS},
        "mod_money_per_run": {m: mm[m] / n for m in MONEY_MODS},
        "levels_per_run": {c: levels[c] / n for c in HAND_CODES},
        "edits_per_run": {k: edits[k] / n for k in ("remove", "paint", "promote")},
        "edit_runs": edit_runs,
        "shop_per_run": {k: shop[k] / n for k in ("handsize", "discards", "reroll", "sell")},
        "ends_per_run": ends / n,
        "ends_last_round": ends_last,
    }


def pct(x):
    return f"{100 * x:5.1f}%"


def report(name, s):
    lines = [f"== {name}: {s['runs']} runs  mean {s['mean']:.0f}  median {s['median']:.0f}  "
             f"cleared {s['mean_cleared']:.2f}  won {s['won']}  cleared histogram {s['cleared_hist']}"]
    lines.append("   hand types   " + "  ".join(f"{c} {pct(s['hand_point_share'][c])}" for c in HAND_CODES
                                             if s["hand_point_share"][c] >= 0.001 or s["levels_per_run"][c]))
    lines.append("   levels/run   " + "  ".join(f"{c} {s['levels_per_run'][c]:.2f}" for c in HAND_CODES
                                             if s["levels_per_run"][c]))
    lines.append("   modifiers    bought-runs / share of all points / share of the run's points when owned")
    for m in MODIFIER_IDS:
        extra = f"  ${s['mod_money_per_run'][m]:.1f}/run" if m in MONEY_MODS else ""
        lines.append(f"     {m:<13} {s['mod_bought_runs'][m]:>4}   {pct(s['mod_point_share'][m])}"
                     f"   {pct(s['mod_share_when_owned'][m])}{extra}")
    e = s["edits_per_run"]
    lines.append(f"   deck edits/run  remove {e['remove']:.2f}  paint {e['paint']:.2f}  promote {e['promote']:.2f}"
                 f"  (runs with any edit: {s['edit_runs']})")
    sh = s["shop_per_run"]
    lines.append(f"   shop/run  handsize {sh['handsize']:.2f}  discards {sh['discards']:.2f}  reroll {sh['reroll']:.2f}"
                 f"  sell {sh['sell']:.2f}   ends/run {s['ends_per_run']:.2f}  ends in last round {s['ends_last_round']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", default="1001-1100")
    ap.add_argument("--rules", default="classic")
    ap.add_argument("--solvers", nargs="+", default=["baseline", "tuned", "beam_search"],
                    help=f"names ({', '.join(SOLVERS)}) or NAME=path/to/solver.py")
    ap.add_argument("--time", type=float, default=3.0, help="POKER_TIME for the solvers")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--json", help="also write the summaries (and per-run data) here")
    args = ap.parse_args()
    seeds = seed_range(args.seeds)
    for k, s in enumerate(args.solvers):
        if "=" in s:
            name, path = s.split("=", 1)
            SOLVERS[name] = [sys.executable, str(Path(path).resolve())]
            args.solvers[k] = name
        elif s not in SOLVERS:
            ap.error(f"unknown solver {s}")
    jobs = [(s, seed, args.rules, args.time) for s in args.solvers for seed in seeds]
    with ThreadPoolExecutor(args.jobs) as ex:
        results = list(ex.map(one, jobs))
    out = {"rules": args.rules, "seeds": args.seeds, "summary": {}, "runs": {}}
    for solver in args.solvers:
        runs = [r for s, _, r in results if s == solver]
        out["summary"][solver] = summarise(runs)
        out["runs"][solver] = [{"seed": seed, "score": r["score"], "cleared": r["cleared"], "bought": r["bought"]}
                               for s, seed, r in results if s == solver]
        print(report(f"{solver} [{args.rules}]", out["summary"][solver]))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

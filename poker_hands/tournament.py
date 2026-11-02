"""Load bot files, play many seeded games, and rank the bots.

A bot file defines either play(state) or STRATEGIES = [f1, f2, f3]
(1 to 3 functions). A bot's score is the mean over its strategies of each
strategy's mean score, so three strategies count equally.
"""

import importlib.util
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import mean, pstdev

from .engine import Table


@dataclass
class Bot:
    name: str
    strategies: list


def load_bot(path):
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"bot_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    strategies = getattr(module, "STRATEGIES", None)
    if strategies is None:
        if not callable(getattr(module, "play", None)):
            raise ValueError(f"{path.name}: define play(state) or STRATEGIES = [...]")
        strategies = [module.play]
    strategies = list(strategies)
    if not 1 <= len(strategies) <= 3 or not all(callable(s) for s in strategies):
        raise ValueError(f"{path.name}: STRATEGIES must hold 1 to 3 functions")
    return Bot(path.stem, strategies)


def discover(paths):
    """Bot files from a mix of files and directories (files starting with _ are skipped)."""
    files = []
    for p in map(Path, paths):
        files += sorted(f for f in p.glob("*.py") if not f.name.startswith("_")) if p.is_dir() else [p]
    return [load_bot(f) for f in files]


class Scores:
    def __init__(self, bots):
        self.bots = bots
        self.by_strategy = {b.name: [[] for _ in b.strategies] for b in bots}
        self.wins = {b.name: 0.0 for b in bots}
        self.tables = {b.name: 0 for b in bots}
        self.errors = {b.name: [] for b in bots}

    def record(self, seated, results, seed):
        """seated: list of (bot, strategy index), in seat order."""
        best = max(r["score"] for r in results)
        winners = [r for r in results if r["score"] == best]
        for (bot, k), r in zip(seated, results):
            self.by_strategy[bot.name][k].append(r["score"])
            self.tables[bot.name] += 1
            if len(results) > 1 and r in winners:
                self.wins[bot.name] += 1 / len(winners)
            if r["error"]:
                self.errors[bot.name].append({"seed": seed, "strategy": k, "error": r["error"]})

    def leaderboard(self):
        rows = []
        for b in self.bots:
            strat_means = [mean(s) for s in self.by_strategy[b.name]]
            all_scores = [x for s in self.by_strategy[b.name] for x in s]
            rows.append({
                "name": b.name,
                "score": mean(strat_means),
                "strategy_means": strat_means,
                "stdev": pstdev(all_scores),
                "games": len(all_scores),
                "win_rate": self.wins[b.name] / self.tables[b.name] if self.tables[b.name] else None,
                "errors": self.errors[b.name],
            })
        return sorted(rows, key=lambda r: r["score"], reverse=True)


def run_solo(bots, config, seeds):
    """Every strategy of every bot plays every seed alone on an identical deck."""
    scores = Scores(bots)
    for bot in bots:
        for k, fn in enumerate(bot.strategies):
            for seed in seeds:
                scores.record([(bot, k)], Table(config, seed, [(bot.name, fn)]).run(), seed)
    return scores


def run_shared(bots, config, seeds, table="all"):
    """Bots draw from one deck in turn.

    table="all":   everyone at one table; seat order rotates with the seed.
    table="pairs": every pair of bots plays every seed in both seat orders.
    Strategy k of each bot sits with strategy k of the others (cycling for
    bots with fewer than 3).
    """
    scores = Scores(bots)
    groups = [bots] if table == "all" else list(combinations(bots, 2))
    for group in groups:
        rounds = max(len(b.strategies) for b in group)
        for i, seed in enumerate(seeds):
            if table == "all":
                orders = [group[i % len(group):] + group[:i % len(group)]]
            else:
                orders = [list(group), list(reversed(group))]
            for order in orders:
                for k in range(rounds):
                    seated = [(b, k % len(b.strategies)) for b in order]
                    players = [(b.name, b.strategies[j]) for b, j in seated]
                    scores.record(seated, Table(config, seed, players).run(), seed)
    return scores


def format_leaderboard(rows, shared):
    head = f"{'#':>2}  {'bot':<20} {'score':>8} {'stdev':>7} {'games':>6}"
    head += f" {'win %':>6}" if shared else ""
    head += "  per-strategy means"
    lines = [head, "-" * len(head)]
    for i, r in enumerate(rows, 1):
        line = f"{i:>2}  {r['name']:<20} {r['score']:>8.1f} {r['stdev']:>7.1f} {r['games']:>6}"
        if shared:
            line += f" {100 * r['win_rate']:>5.1f}%"
        line += "  " + " / ".join(f"{m:.1f}" for m in r["strategy_means"])
        if r["errors"]:
            line += f"  ({len(r['errors'])} games ended by bot errors)"
        lines.append(line)
    return "\n".join(lines)

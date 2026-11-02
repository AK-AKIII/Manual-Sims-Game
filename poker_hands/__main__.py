"""Command line: python -m poker_hands {run,play,config} ..."""

import argparse
import json
import random

from .config import load_config
from .engine import Table
from .human import human_strategy
from .tournament import discover, format_leaderboard, run_shared, run_solo


def main():
    ap = argparse.ArgumentParser(prog="python -m poker_hands", description="Jokerless-Balatro bot game")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--config", help="JSON file overriding the default settings")
        p.add_argument("--deck", choices=["standard", "random"], help="override the deck type")
        p.add_argument("--plays", type=int, help="override X, the number of hands you may play")
        p.add_argument("--discards", type=int, help="override Y, the number of discards")

    run = sub.add_parser("run", help="run a tournament between bot files")
    run.add_argument("bots", nargs="+", help="bot .py files or folders of them")
    run.add_argument("--mode", choices=["solo", "shared"], default="solo",
                     help="solo: each bot gets its own copy of the deck; shared: bots draw from one deck in turn")
    run.add_argument("--table", choices=["all", "pairs"], default="all",
                     help="shared mode: everyone at one table, or every pair head to head")
    run.add_argument("--games", type=int, default=200, help="number of seeds")
    run.add_argument("--seed", type=int, default=0, help="first seed")
    run.add_argument("--out", help="write full results as JSON")
    common(run)

    play = sub.add_parser("play", help="play a game yourself in the terminal")
    play.add_argument("--seed", type=int, help="seed for the deck (random if omitted)")
    play.add_argument("--vs", nargs="*", default=[], help="bot files to share the deck with you")
    common(play)

    cfg = sub.add_parser("config", help="print the effective settings as JSON")
    common(cfg)

    args = ap.parse_args()
    config = load_config(args.config, deck=args.deck, plays=args.plays, discards=args.discards)

    if args.cmd == "config":
        print(json.dumps(config, indent=2))
    elif args.cmd == "run":
        bots = discover(args.bots)
        seeds = list(range(args.seed, args.seed + args.games))
        if args.mode == "solo":
            scores = run_solo(bots, config, seeds)
        else:
            scores = run_shared(bots, config, seeds, args.table)
        rows = scores.leaderboard()
        print(f"{args.mode} mode, {args.games} seeds from {args.seed}, deck={config['deck']}")
        print(format_leaderboard(rows, shared=args.mode == "shared"))
        if args.out:
            with open(args.out, "w") as f:
                json.dump({"config": config, "seeds": seeds, "mode": args.mode,
                           "table": args.table, "leaderboard": rows}, f, indent=2)
            print(f"wrote {args.out}")
    else:
        seed = args.seed if args.seed is not None else random.randrange(10**6)
        players = [("you", human_strategy)]
        for bot in discover(args.vs):
            players.append((bot.name, bot.strategies[0]))
        print(f"seed {seed}, deck={config['deck']}, {config['plays']} plays, {config['discards']} discards. Type h for help.")
        results = Table(config, seed, players).run()
        print()
        for r in results:
            print(f"{r['name']}: {r['score']} points  {r['hands_played']}" + (f"  ({r['error']})" if r["error"] else ""))


if __name__ == "__main__":
    main()

"""Poker Run: a heuristic-optimisation contest problem built on poker hands.

Read problem/statement.md for the rules. The pieces:
    instance.py   generator and input format
    sim.py        the simulator (Game) every tool shares
    checker.py    scores an output file
    baseline.py   reference greedy solver
"""

from .instance import Instance, generate, parse_instance, format_instance
from .sim import Game, IllegalMove, classify
from .checker import check

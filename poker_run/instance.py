"""Test cases: the generator, and the input file format.

Input format (whitespace separated):

    R H P D M Q             rounds, hand size, plays and discards per round,
                            modifier slots, offers in each shop's queue
    T_1 ... T_R             score target of each round
    R lines of 52 card ids  draw order of round r (first id is drawn first)
    R-1 lines of Q names    offer queue of the shop after round r

The generator uses mulberry32 so that web/engine.js can produce the exact
same cases from a seed.
"""

from dataclasses import dataclass, field

from .rules import MODIFIER_IDS, N_CARDS

MASK = 0xFFFFFFFF


class Rng:
    """mulberry32, written to match the JavaScript version bit for bit."""

    def __init__(self, seed):
        self.a = seed & MASK

    def next(self):
        self.a = (self.a + 0x6D2B79F5) & MASK
        a = self.a
        t = ((a ^ (a >> 15)) * (1 | a)) & MASK
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & MASK)) & MASK) ^ t
        return ((t ^ (t >> 14)) & MASK) / 4294967296

    def below(self, n):
        return int(self.next() * n)

    def shuffle(self, items):
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]
        return items


DEFAULT_PARAMS = {"rounds": 8, "hand_size": 8, "plays": 4, "discards": 3, "slots": 5, "queue": 12}
TARGET_BASE = 300
TARGET_GROWTH = 2.0


@dataclass
class Instance:
    rounds: int
    hand_size: int
    plays: int
    discards: int
    slots: int
    queue: int
    targets: list
    orders: list = field(repr=False)   # orders[r] = draw order of round r
    offers: list = field(repr=False)   # offers[r] = shop queue after round r


def targets_for(rounds):
    return [int(round(TARGET_BASE * TARGET_GROWTH ** r / 10)) * 10 for r in range(rounds)]


def generate(seed, **params):
    p = {**DEFAULT_PARAMS, **{k: v for k, v in params.items() if v is not None}}
    rng = Rng(seed)
    orders = [rng.shuffle(list(range(N_CARDS))) for _ in range(p["rounds"])]
    offers = [rng.shuffle(list(MODIFIER_IDS))[:p["queue"]] for _ in range(p["rounds"] - 1)]
    return Instance(p["rounds"], p["hand_size"], p["plays"], p["discards"], p["slots"], p["queue"],
                    targets_for(p["rounds"]), orders, offers)


def format_instance(inst):
    lines = [f"{inst.rounds} {inst.hand_size} {inst.plays} {inst.discards} {inst.slots} {inst.queue}",
             " ".join(map(str, inst.targets))]
    lines += [" ".join(map(str, order)) for order in inst.orders]
    lines += [" ".join(q) for q in inst.offers]
    return "\n".join(lines) + "\n"


class BadInput(ValueError):
    pass


def parse_instance(text):
    tok = text.split()
    pos = 0

    def take(n):
        nonlocal pos
        if pos + n > len(tok):
            raise BadInput("input ends early")
        out = tok[pos:pos + n]
        pos += n
        return out

    def ints(n):
        words = take(n)
        try:
            return [int(t) for t in words]
        except ValueError as e:
            raise BadInput(f"expected an integer: {e}") from None

    rounds, hand_size, plays, discards, slots, queue = ints(6)
    if not (1 <= rounds and 1 <= hand_size and 1 <= plays and 0 <= discards and 0 <= slots and 0 <= queue):
        raise BadInput("bad parameters")
    targets = ints(rounds)
    orders = [ints(N_CARDS) for _ in range(rounds)]
    offers = [take(queue) for _ in range(rounds - 1)]
    if pos != len(tok):
        raise BadInput("extra data after the offer queues")
    for r, order in enumerate(orders):
        if sorted(order) != list(range(N_CARDS)):
            raise BadInput(f"draw order {r + 1} is not a permutation of 0..51")
    for q in offers:
        if any(m not in MODIFIER_IDS for m in q):
            raise BadInput(f"unknown modifier in {q}")
    return Instance(rounds, hand_size, plays, discards, slots, queue, targets, orders, offers)

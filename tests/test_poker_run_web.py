"""Parity between poker_run's Python judge and the browser engine (poker_run/web/engine.js).

    python3 -m unittest tests.test_poker_run_web -v

Every case is run through both implementations and the results must be identical:
generated input text, checker verdicts (including error strings) for baseline outputs,
random deep runs exercising every move type, illegal moves, unparsable outputs and bad
inputs, and per-move game state for a subset of runs. The same is done (on fewer runs)
for the balanced ruleset, and the ruleset tables themselves are compared.
Skipped if node is not installed.
"""

import json
import random
import shutil
import subprocess
import unittest
from pathlib import Path

from poker_run.baseline import best_play, solve
from poker_run.checker import check
from poker_run.instance import format_instance, generate
from poker_run.rules import HAND_CODES, MODIFIER_IDS, RULESETS, SUITS
from poker_run.sim import Game, IllegalMove

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / "poker_run" / "web" / "parity_driver.js"
NODE = shutil.which("node")
SEEDS = range(1, 31)
RUNS_PER_SEED = 12
TRACED_RUNS_PER_SEED = 3


def run_js(cases):
    proc = subprocess.run([NODE, str(DRIVER)], input=json.dumps(cases), capture_output=True, text=True,
                          timeout=600)
    if proc.returncode != 0:
        raise AssertionError(f"node driver failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def legal(game, move):
    try:
        game.clone().apply(move)
        return True
    except IllegalMove:
        return False


def fmt_card(rng, c):
    """A card id as a token, sometimes in an unusual but valid int() spelling."""
    r = rng.random()
    if r < 0.03:
        return f"0{c}"
    if r < 0.05:
        return f"+{c}"
    if r < 0.06 and c >= 10:
        s = str(c)
        return s[0] + "_" + s[1:]
    return str(c)


def play_moves(game, rng):
    """Candidate legal-ish moves during a round, most useful first."""
    hand = game.hand
    out = []
    if game.round_score >= game.target and rng.random() < 0.35:
        out.append("end")
    r = rng.random()
    if r < 0.85:
        cards, res = best_play(game)
        need = game.target - game.round_score
        if res["points"] * game.plays_left < need and game.discards_left > 0 and rng.random() < 0.8:
            junk = [c for c in hand if c not in res["scored"]]
            rng.shuffle(junk)
            if junk:
                out.append("discard " + " ".join(fmt_card(rng, c) for c in junk[:rng.randint(1, 5)]))
        out.append("play " + " ".join(fmt_card(rng, c) for c in cards))
    elif r < 0.93 and game.discards_left > 0:
        cards = rng.sample(hand, rng.randint(1, min(5, len(hand))))
        out.append("discard " + " ".join(fmt_card(rng, c) for c in cards))
    else:
        cards = rng.sample(hand, rng.randint(1, min(5, len(hand))))
        out.append("play " + " ".join(fmt_card(rng, c) for c in cards))
    return out


def shop_move(game, rng, rich=False):
    deck = [c for c in range(52) if game.in_deck[c]]
    owned = [m for m, _ in game.mods]
    pool = []
    for name in game.visible_offers():
        pool += [f"buy {name}"] * 4
    pool += [f"sell {m}" for m in owned]
    pool += [f"level {rng.choice(HAND_CODES)}"] * 3
    pool += [f"remove {rng.choice(deck)}"] * 2
    pool += [f"paint {rng.choice(deck)} {rng.choice(SUITS)}"] * 2
    # 'paint i SH' is legal in the Python judge (substring test); exercise it too.
    pool += [f"paint {rng.choice(deck)} {rng.choice(['SH', 'HD', 'DC', 'SHDC', 'HDC'])}"]
    pool += [f"promote {rng.choice(deck)}"] * 2
    pool += ["discards", "handsize", "reroll", "reroll"]
    pool += ["next"] * 3
    if rich:   # lots of money: push the deck, hand size and slot limits
        pool += [f"remove {rng.choice(deck)}"] * 25 + ["handsize", "discards"] * 3 + ["reroll"] * 10
        pool += [f"buy {name}" for name in game.visible_offers()] * 6
    for _ in range(12):
        m = rng.choice(pool)
        if legal(game, m):
            return m
    return "next"


def illegal_moves(game, rng):
    """Moves that should be illegal right now (verified before use)."""
    if game.over:
        return ["next", "play 0", "end", "buy momentum"]
    hand = game.hand
    common = ["frobnicate", "constructor", "__proto__", "toString", "hasOwnProperty", "PLAY 1"]
    if game.phase == "play":
        not_in_hand = [c for c in range(52) if c not in hand]
        c = [
            "play", "discard", "play 99", "play -1", "play x", "play 1.0", "play 3e1", "play _1", "play 1_",
            "play 1__2", f"play {hand[0]} {hand[0]}", "play " + " ".join(map(str, hand[:6])),
            "discard " + " ".join(map(str, hand[:6])), f"play {rng.choice(not_in_hand)}",
            f"discard {rng.choice(not_in_hand)}", "play 123456789012345678901234567890",
            "play ٣", "end", "end 1", "buy momentum", "level HC", "next", "reroll", f"play {hand[0]}x",
        ]
        if game.discards_left == 0:
            c.append(f"discard {hand[0]}")
    else:
        deck = [x for x in range(52) if game.in_deck[x]]
        aces = [x for x in deck if game.rank[x] == 12]
        removed = [x for x in range(52) if not game.in_deck[x]]
        visible = set(game.visible_offers())
        c = [
            f"buy {rng.choice([m for m in MODIFIER_IDS if m not in visible])}", "buy constructor", "buy",
            "buy a b", "sell nothing", "sell constructor", "level XX", "level", "level hc", "level constructor",
            "level __proto__", "remove 52", "remove -1", "remove x", "remove", "remove 1 2", "paint 3 X",
            "paint 3", "paint 3 s", "paint 3 SC", "paint 99 S", "promote", "promote 52", "discards 1",
            "handsize x", "reroll now", "next 1", "play 1", "discard 1", "end",
            "level " + rng.choice(HAND_CODES),
        ]
        c += [f"buy {m}" for m in visible]
        c += [f"sell {m}" for m, _ in game.mods if rng.random() < 0.2]
        if aces:
            c.append(f"promote {rng.choice(aces)}")
        if removed:
            c += [f"remove {removed[0]}", f"paint {removed[0]} H", f"promote {removed[0]}"]
        c += ["discards", "handsize", "reroll", "reroll", f"remove {rng.choice(deck)}"]
    c += common
    rng.shuffle(c)
    return [m for m in c if not legal(game, m)]


def smart_shop_move(game, rng, played):
    """A move that helps the run go deeper: buy modifiers, level the most played hand."""
    for name in game.visible_offers():
        m = f"buy {name}"
        if legal(game, m):
            return m
    if played:
        code = max(played, key=played.get)
        if legal(game, f"level {code}") and rng.random() < 0.8:
            return f"level {code}"
    return None


def random_run(inst, rng, mode, rich=False, rules=None):
    """A move list. mode: 'legal', 'illegal' (one illegal move somewhere), 'after_over', 'stop'."""
    game = Game(inst, rules)
    moves = []
    played = {}
    stop_at = rng.randint(1, 150) if mode == "stop" else None
    bad_phase = rng.choice(["play", "shop", "shop"])
    bad_round = rng.randint(0, 3)
    while not game.over and len(moves) < 600:
        if stop_at is not None and len(moves) >= stop_at:
            break
        if (mode == "illegal" and game.phase == bad_phase and game.round >= bad_round
                and rng.random() < 0.15):
            bad = illegal_moves(game, rng)
            if bad:
                moves.append(bad[0])
                if rng.random() < 0.5:
                    moves.append("next")
                return moves
        if game.phase == "play":
            cands = play_moves(game, rng)
            move = next((m for m in cands if legal(game, m)), None)
            if move is None:
                cards, _ = best_play(game)
                move = "play " + " ".join(map(str, cards))
            if move.startswith("play"):
                code = game.evaluate([int(t) for t in move.split()[1:]])["hand"]
                played[code] = played.get(code, 0) + 1
        else:
            move = (smart_shop_move(game, rng, played) if rng.random() < 0.6 else None) or shop_move(game, rng, rich)
        game.apply(move)
        moves.append(move)
    if mode == "illegal" or (mode == "after_over" and game.over):
        bad = illegal_moves(game, rng)
        if bad:
            moves.append(bad[0])
            if rng.random() < 0.5:
                moves.append("next")
    return moves


def render_output(moves, rng):
    """Output text, with harmless whitespace variations."""
    sep = rng.choice(["\n", "\n", "\r\n", "\n\n"])
    lines = []
    for m in moves:
        if rng.random() < 0.05:
            m = m.replace(" ", rng.choice(["  ", "\t", " \t "]))
        if rng.random() < 0.03:
            m = "  " + m + " \t"
        lines.append(m)
    return str(len(moves)) + sep + sep.join(lines) + rng.choice(["", "\n", "\n\n  \n"])


def py_snapshot(g):
    hand = list(g.hand)
    ev = g.evaluate(hand[:5]) if hand else None
    return {
        "phase": g.phase, "round": g.round, "total": g.total, "money": g.money, "round_score": g.round_score,
        "plays_left": g.plays_left, "discards_left": g.discards_left, "hand": hand, "pile": list(g.pile),
        "cleared": g.cleared, "moves": g.moves, "deck_size": g.deck_size,
        "rank": list(g.rank), "suit": list(g.suit), "in_deck": list(g.in_deck),
        "levels": dict(g.levels), "mods": [[m, dict(s)] for m, s in g.mods],
        "offers": g.visible_offers(), "prices": g.prices(), "rerolls": g.rerolls, "offer_pos": g.offer_pos,
        "hand_size": g.hand_size, "eval": ev,
    }


def py_trace(inst, moves, rules=None):
    g = Game(inst, rules)
    out = [py_snapshot(g)]
    for m in moves:
        try:
            g.apply(m)
        except IllegalMove as e:
            out.append({"error": str(e)})
            break
        out.append(py_snapshot(g))
    return out


def odd_cases(seed):
    """Unparsable outputs and bad inputs."""
    text = format_instance(generate(seed))
    tok = text.split()
    outs = ["", "   \n  ", "x\nplay 1", "2\nplay 1", "-1\n", "0", "0\n\n", "1\n\n\nend\n", "+0", "0_0",
            "1\nplay 1\nplay 2", "﻿0", "0\x1c", "1\x1cend", "1\x85next", "1 play 0", "1\x0bend",
            "1\x1fend", "1\nplay\x1f0", "1\nplay\xa00", "1\nplay 0", "١\nend", "1\nplay ٣",
            "99999999999999999999\n", "1\n\tplay 'x'", '1\nplay "x', "1\nplay \x01", "1\nplay é😀",
            "1\nplay " + "1" * 4301, "1\nplay " + "1" * 300 + "x"]
    cases = [(text, o) for o in outs]
    bad_inputs = [
        "", "8 8 4 3 5", " ".join(tok[:-1]), text + " extra", text.replace(tok[8], "x", 1),
        text.replace("momentum", "momentun"), text.replace("momentum", "constructor"),
        " ".join(["1", "8", "4", "3", "5", "0", "300"] + tok[8:60]),
        " ".join(["1", "8", "4", "3", "5", "0", "300"] + tok[8:59] + [tok[8]]),
        " ".join(["1", "0", "4", "3", "5", "0", "300"] + tok[8:60]),
        " ".join(["1", "8", "0", "3", "5", "0", "300"] + tok[8:60]),
        " ".join(["1", "8", "4", "-1", "5", "0", "300"] + tok[8:60]),
        " ".join(["0", "8", "4", "3", "5", "0"]),
        " ".join(["-3", "8", "4", "3", "5", "0", "1", "2", "3"]),
        " ".join(["-3", "8", "4", "3", "5", "0", "1", "x", "3"]),
        " ".join(["2", "8", "4", "3", "5", "-2", "300", "600"] + tok[8:112] + ["a", "b"]),
        " ".join(["2", "8", "4", "3", "5", "-1", "300", "600"] + tok[8:112] + ["a", "b"]),
        " ".join(["2", "8", "4", "3", "5", "1", "300", "600"] + tok[8:112] + ["bogus"]),
        " ".join(["1", "8", "4", "3", "5", "0", "1_0"] + tok[8:60]),
        "﻿" + text, "8\x1c" + text[2:], text.replace(" ", "　"),
        " ".join(["1", "8", "4", "3", "5", "0", "5"] + list(reversed(tok[8:60]))),
        " ".join(["99999999999999999999", "8", "4", "3", "5", "0"]),
        " ".join(["1", "8", "4", "3", "5", "0", "99999999999999999999"] + tok[8:60]),
    ]
    one_round = " ".join(["1", "8", "4", "3", "5", "0", "99999999999999999999"] + tok[8:60])
    cases += [(b, "1\nplay 0") for b in bad_inputs]
    cases += [(one_round, "1\nend"), (one_round, "0\n")]
    return cases


@unittest.skipIf(NODE is None, "node is not on PATH; install Node.js to run the web engine parity test")
class TestWebParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(20261103)
        cls.gen_cases = [(s, {}) for s in SEEDS]
        cls.gen_cases += [(s, {"rounds": rng.randint(1, 12), "hand_size": rng.randint(1, 10),
                               "plays": rng.randint(1, 6), "discards": rng.randint(0, 5)}) for s in range(100, 110)]
        cls.gen_cases += [(s, {}) for s in (0, 2 ** 31, 2 ** 32 - 1, 2 ** 32 + 5, -1, -12345, 4294967296 * 3 + 7)]
        cls.checks = []   # (input, output)
        cls.traces = []   # (input, moves)
        runs = []         # (instance, input, moves) of fully legal runs
        modes = ["legal", "legal", "legal", "illegal", "illegal", "illegal", "illegal", "after_over", "stop"]
        for seed in SEEDS:
            inst = generate(seed)
            text = format_instance(inst)
            base = solve(inst)
            cls.checks.append((text, f"{len(base)}\n" + "\n".join(base) + "\n"))
            for k in range(RUNS_PER_SEED):
                moves = random_run(inst, rng, modes[k % len(modes)])
                cls.checks.append((text, render_output(moves, rng)))
                if modes[k % len(modes)] == "legal" and k < 2:
                    runs.append((inst, text, moves))
                if k < TRACED_RUNS_PER_SEED or (k == 3 and seed % 3 == 0):
                    cls.traces.append((text, moves))
            cls.checks += odd_cases(seed) if seed <= 5 else []
        # A custom input with other parameters, to exercise bigger hands and more plays.
        custom = generate(101, rounds=6, hand_size=10, plays=6, discards=5, slots=3)
        ctext = format_instance(custom)
        for k in range(8):
            moves = random_run(custom, rng, modes[k % len(modes)])
            cls.checks.append((ctext, render_output(moves, rng)))
            cls.traces.append((ctext, moves))

        # A rich input (many plays, tiny targets): money piles up, so the shop's limits are reached
        # (deck at 20 cards, hand size 10, all slots full, queue used up).
        for seed in (3, 11, 19):
            rich = generate(seed)
            rich.plays, rich.targets = 150, [10] * rich.rounds
            rtext = format_instance(rich)
            for k in range(6):
                moves = random_run(rich, rng, ["legal", "illegal", "after_over"][k % 3], rich=True)
                cls.checks.append((rtext, render_output(moves, rng)))
                cls.traces.append((rtext, moves))
                if k % 3 == 0:
                    runs.append((rich, rtext, moves))
        # Hit each shop limit on purpose: deck at 20 cards, hand size 10, offer queue used up.
        rich = generate(5)
        rich.plays, rich.targets = 150, [10] * rich.rounds
        rtext = format_instance(rich)
        game, moves = Game(rich), []
        while game.phase == "play":
            m = "play " + str(game.hand[0])
            game.apply(m)
            moves.append(m)
            if game.phase == "play" and game.round_score >= game.target:
                game.apply("end")
                moves.append("end")
        prefix = moves[:]
        removes = [f"remove {c}" for c in range(32)]
        cls.checks.append((rtext, render_output(prefix + removes + ["remove 40"], rng)))
        cls.checks.append((rtext, render_output(prefix + ["handsize", "handsize", "handsize"], rng)))
        cls.checks.append((rtext, render_output(prefix + ["reroll"] * 4, rng)))
        cls.checks.append((rtext, render_output(prefix + ["reroll"] * 3 + ["buy " + rich.offers[0][9]], rng)))
        cls.traces.append((rtext, prefix + removes + ["handsize", "handsize", "reroll", "reroll", "next"]))

        # Illegal moves at many points of legal runs: for a prefix of a run, try a batch of illegal moves.
        for inst, text, moves in runs:
            game = Game(inst)
            points = set(rng.sample(range(len(moves) + 1), min(max(12, len(moves) // 3), len(moves) + 1)))
            for k, m in enumerate(moves + [None]):
                if k in points:
                    for bad in illegal_moves(game, rng)[:4]:
                        cls.checks.append((text, render_output(moves[:k] + [bad], rng)))
                if m is None:
                    break
                game.apply(m)

        cases = [{"op": "gen", "seed": s, "params": p} for s, p in cls.gen_cases]
        cases += [{"op": "check", "input": i, "output": o} for i, o in cls.checks]
        cases += [{"op": "trace", "input": i, "moves": m} for i, m in cls.traces]
        cls.js = run_js(cases)
        n1, n2 = len(cls.gen_cases), len(cls.checks)
        cls.js_gen, cls.js_check, cls.js_trace = cls.js[:n1], cls.js[n1:n1 + n2], cls.js[n1 + n2:]

    def test_generated_inputs(self):
        for (seed, params), js in zip(self.gen_cases, self.js_gen):
            with self.subTest(seed=seed, params=params):
                self.assertEqual(js, format_instance(generate(seed, **params)))

    def test_checker_verdicts(self):
        kinds = {"valid": 0, "illegal": 0, "unparsable": 0, "bad_input": 0}
        for (inp, out), js in zip(self.checks, self.js_check):
            code, v = check(inp, out)
            kinds[["valid", "illegal", "unparsable", "bad_input"][code]] += 1
            with self.subTest(output=out[:120]):
                self.assertEqual(js, [code, v])
        # The case mix must actually cover each verdict kind.
        for k, n in kinds.items():
            self.assertGreater(n, 0, k)
        print(f"\n  {len(self.checks)} checker cases: {kinds}")

    def test_move_coverage(self):
        """Every move type appears in some valid prefix of the runs."""
        seen = set()
        for _, moves in self.traces:
            seen.update(m.split()[0] for m in moves if m.split())
        for inp, out in self.checks:
            code, v = check(inp, out)
            lines = [l.split() for l in out.strip().splitlines()[1:] if l.strip()]
            n = len(lines) if code == 0 else (v.get("move", 1) - 1 if code == 1 else 0)
            seen.update(t[0] for t in lines[:n])
        need = {"play", "discard", "end", "buy", "sell", "level", "remove", "paint", "promote", "discards",
                "handsize", "reroll", "next"}
        self.assertEqual(need - seen, set())

    def test_deep_runs(self):
        """Random runs go into late rounds (so later shops and rounds are compared too)."""
        deepest = max(check(i, o)[1].get("cleared", 0) for i, o in self.checks)
        self.assertGreaterEqual(deepest, 5)

    def test_per_move_state(self):
        steps = 0
        for (inp, moves), js in zip(self.traces, self.js_trace):
            py = py_trace(__import__("poker_run").parse_instance(inp), moves)
            self.assertEqual(len(js), len(py))
            for k, (a, b) in enumerate(zip(js, py)):
                steps += 1
                if a != b:
                    self.fail(f"state differs after move {k} ({moves[k - 1] if k else 'start'!r}):\n"
                              f"js={json.dumps(a)[:800]}\npy={json.dumps(b)[:800]}")
        print(f"\n  {len(self.traces)} traced runs, {steps} states compared")


def ruleset_table(R):
    """A ruleset's values as JSON-like data (what the parity driver's 'rulesets' op returns)."""
    out = {k: v for k, v in vars(R).items() if not k.startswith("_")}
    return json.loads(json.dumps(out))


@unittest.skipIf(NODE is None, "node is not on PATH; install Node.js to run the web engine parity test")
class TestWebParityBalanced(unittest.TestCase):
    """The balanced ruleset: same tables in both engines, and the same verdicts and states."""
    RULES = "balanced"

    @classmethod
    def setUpClass(cls):
        rng = random.Random(20261104)
        rules = cls.RULES
        cls.checks = []   # (input, output, rules)
        cls.traces = []   # (input, moves)
        runs = []
        modes = ["legal", "legal", "illegal", "illegal", "after_over", "stop"]
        for seed in range(1, 13):
            inst = generate(seed)
            text = format_instance(inst)
            base = solve(inst, rules)
            cls.checks.append((text, f"{len(base)}\n" + "\n".join(base) + "\n", rules))
            for k in range(6):
                moves = random_run(inst, rng, modes[k], rules=rules)
                cls.checks.append((text, render_output(moves, rng), rules))
                if k == 0:
                    runs.append((inst, text, moves))
                if k < 2:
                    cls.traces.append((text, moves))
        # Rich inputs: money piles up, so the modifiers, deck edits and shop limits all get used.
        for seed in (3, 11):
            rich = generate(seed)
            rich.plays, rich.targets = 150, [10] * rich.rounds
            rtext = format_instance(rich)
            for k in range(4):
                moves = random_run(rich, rng, ["legal", "illegal"][k % 2], rich=True, rules=rules)
                cls.checks.append((rtext, render_output(moves, rng), rules))
                cls.traces.append((rtext, moves))
                if k == 0:
                    runs.append((rich, rtext, moves))
        # `end` in the last round: legal in classic, illegal in balanced.
        rich = generate(5, rounds=2)
        rich.plays, rich.targets = 150, [10, 10]
        rtext = format_instance(rich)
        cls.last_round = [f"play {c}" for c in rich.orders[0][:1]] + ["end", "next"]
        cls.last_round += [f"play {c}" for c in rich.orders[1][:1]] + ["end"]
        cls.last_round_checks = (len(cls.checks), len(cls.checks) + 1)
        for r in ("classic", rules):
            cls.checks.append((rtext, render_output(cls.last_round, rng), r))
        cls.traces.append((rtext, cls.last_round))
        for inst, text, moves in runs:
            game = Game(inst, rules)
            points = set(rng.sample(range(len(moves) + 1), min(10, len(moves) + 1)))
            for k, m in enumerate(moves + [None]):
                if k in points:
                    for bad in illegal_moves(game, rng)[:3]:
                        cls.checks.append((text, render_output(moves[:k] + [bad], rng), rules))
                if m is None:
                    break
                game.apply(m)

        cases = [{"op": "rulesets"}]
        cases += [{"op": "check", "input": i, "output": o, "rules": r} for i, o, r in cls.checks]
        cases += [{"op": "trace", "input": i, "moves": m, "rules": rules} for i, m in cls.traces]
        js = run_js(cases)
        n = len(cls.checks)
        cls.js_rulesets, cls.js_check, cls.js_trace = js[0], js[1:1 + n], js[1 + n:]

    def test_ruleset_tables(self):
        self.assertEqual(set(self.js_rulesets), set(RULESETS))
        for name, R in RULESETS.items():
            with self.subTest(rules=name):
                self.assertEqual(self.js_rulesets[name], ruleset_table(R))

    def test_checker_verdicts(self):
        kinds = {"valid": 0, "illegal": 0, "unparsable": 0, "bad_input": 0}
        for (inp, out, rules), js in zip(self.checks, self.js_check):
            code, v = check(inp, out, rules)
            kinds[["valid", "illegal", "unparsable", "bad_input"][code]] += 1
            with self.subTest(output=out[:120], rules=rules):
                self.assertEqual(js, [code, v])
        self.assertGreater(kinds["valid"], 20)
        self.assertGreater(kinds["illegal"], 20)
        deepest = max(check(i, o, r)[1].get("cleared", 0) for i, o, r in self.checks)
        self.assertGreaterEqual(deepest, 5)
        print(f"\n  {len(self.checks)} {self.RULES} checker cases: {kinds}")

    def test_end_in_last_round(self):
        classic, balanced = (self.js_check[i] for i in self.last_round_checks)
        self.assertEqual(classic[0], 0)
        self.assertNotIn("rules", classic[1])
        self.assertEqual(balanced[0], 1)
        self.assertIn("not allowed in the last round", balanced[1]["error"])

    def test_per_move_state(self):
        steps = 0
        for (inp, moves), js in zip(self.traces, self.js_trace):
            py = py_trace(__import__("poker_run").parse_instance(inp), moves, self.RULES)
            self.assertEqual(len(js), len(py))
            for k, (a, b) in enumerate(zip(js, py)):
                steps += 1
                if a != b:
                    self.fail(f"state differs after move {k} ({moves[k - 1] if k else 'start'!r}):\n"
                              f"js={json.dumps(a)[:800]}\npy={json.dumps(b)[:800]}")
        print(f"\n  {len(self.traces)} {self.RULES} traced runs, {steps} states compared")


UI_SCRIPT = r"""
const fs = require("fs"), vm = require("vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
if (scripts.length !== 1) throw new Error("expected one inline script");
new vm.Script(scripts[0], { filename: "index.html" });            // syntax check
const PR = require(process.argv[2]);
const ctx = { PokerRun: PR, module: { exports: {} }, console };
vm.runInNewContext(scripts[0], ctx);                                // no document: only the helpers run
const UI = ctx.module.exports;
const inst = PR.generate(7);
const g = new PR.Game(inst);
const out = {};
out.hand = g.hand;
out.desc_discard = UI.describe(g, "discard 26 16 17 45");
g.apply("discard 26 16 17 45");
const p = UI.preview(g, [33, 8, 9, 23, 50]);
out.preview = { hand: p.res.hand, points: p.res.points, playErr: p.playErr, after: p.after, formula: UI.formula(p.res) };
out.desc_play = UI.describe(g, "play 33 8 9 23 50");
out.bad_preview = UI.preview(g, [0]).playErr;
const r = UI.replayStates(inst, ["discard 26 16 17 45", "play 33 8 9 23 50", "end", "next", "play 99"]);
out.replay = { n: r.states.length, errorAt: r.errorAt, error: r.error, phase: r.states[3].phase + "," + r.states[4].phase };
out.cards = UI.moveCards("paint 7 H").concat(UI.moveCards("play 1 2 x 99"));
out.reward = UI.roundReward(r.states[2]);
const b = UI.buildGame(inst, ["discard 26 16 17 45", "play 33 8 9 23 50", "end", "buy constructor"]);
out.build = { applied: b.applied, error: b.error, money: b.game.money };
out.pile = UI.nextRoundPile(r.states[3]).length;
out.hash = ["#seed=7", "#seed=7&rules=balanced", "#mode=bot", "#mode=Draft&seed=12&rules=balanced", "#rules=balanced", "", "#x=1&mode=hotseat",
  "#room=k7m3q", "#match=12", "#mode=online&create=Draft"].map(UI.parseHash);
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipIf(NODE is None, "node is not on PATH; install Node.js to run the web engine parity test")
class TestWebPage(unittest.TestCase):
    def test_page_helpers(self):
        """index.html's inline script parses, and its pure UI helpers agree with the Python game."""
        web = ROOT / "poker_run" / "web"
        proc = subprocess.run([NODE, "-e", UI_SCRIPT, str(web / "index.html"), str(web / "engine.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        g = Game(generate(7))
        self.assertEqual(out["hand"], g.hand)
        g.apply("discard 26 16 17 45")
        res = g.evaluate([33, 8, 9, 23, 50])
        self.assertEqual(out["preview"]["hand"], res["hand"])
        self.assertEqual(out["preview"]["points"], res["points"])
        self.assertEqual(out["preview"]["points"], 316)  # the statement's worked example
        self.assertIsNone(out["preview"]["playErr"])
        self.assertTrue(out["preview"]["formula"].endswith("= 316"))
        self.assertIn("Straight 316", out["desc_play"])
        self.assertEqual(out["bad_preview"], "card 0 is not in your hand")
        self.assertEqual(out["replay"], {"n": 5, "errorAt": 5, "error": "card 99 is not in your hand", "phase": "shop,play"})
        self.assertEqual(out["cards"], [7, 1, 2])
        g.apply("play 33 8 9 23 50")
        money = g.money
        g.apply("end")
        self.assertEqual(out["reward"], g.money - money)
        self.assertEqual(out["build"], {"applied": 3, "error": "constructor is not on offer", "money": g.money})
        self.assertEqual(out["pile"], 52)
        # the /play/ URL hash: #seed= and #rules= for the solo game, #mode= for the landing page's mode links,
        # #room= and #match= for online rooms and saved matches (old #mode=hotseat/draft links open Online)
        none = {"seed": None, "rules": None, "mode": None, "room": None, "match": None, "create": None}
        self.assertEqual(out["hash"], [
            {**none, "seed": 7},
            {**none, "seed": 7, "rules": "balanced"},
            {**none, "mode": "bot"},
            {**none, "seed": 12, "rules": "balanced", "mode": "draft"},
            {**none, "rules": "balanced"},
            none,
            {**none, "mode": "hotseat"},
            {**none, "room": "K7M3Q"},
            {**none, "match": 12},
            {**none, "mode": "online", "create": "draft"},
        ])


if __name__ == "__main__":
    unittest.main()

"""The browser game's bots (poker_run/web/bot.js) and multiplayer layer (multiplayer.js).

    python3 -m unittest tests.test_poker_run_multiplayer -v

Node runs poker_run/web/multiplayer_driver.js; everything it reports is checked here
against the Python simulator and checker:
  * every bot move list (easy, normal, hard; classic and balanced) is a valid output,
    and the normal bot plays exactly like poker_run/baseline.py;
  * pass-and-play players' games are independent: interleaved round by round, each
    player's moves are those of the same player alone, and turns go round by round;
  * the shared-shop draft follows its rules (MULTIPLAYER.md), re-checked here from the
    move history with Python Games: nobody buys a modifier another player owns, the
    shop visits go lowest total first (ties rotate), and every player's moves are valid.
Skipped if node is not installed.
"""

import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from poker_run.baseline import solve
from poker_run.checker import check
from poker_run.instance import format_instance, generate
from poker_run.sim import Game

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "poker_run" / "web"
DRIVER = WEB / "multiplayer_driver.js"
NODE = shutil.which("node")
SEEDS = list(range(1, 13))


def output(moves):
    return f"{len(moves)}\n" + "".join(m + "\n" for m in moves)


@unittest.skipIf(NODE is None, "node is not on PATH; install Node.js to run the multiplayer tests")
class TestMultiplayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run([NODE, str(DRIVER), json.dumps({"seeds": SEEDS, "scenarioSeed": 4})],
                              capture_output=True, text=True, timeout=600, cwd=str(WEB))
        if proc.returncode != 0:
            raise AssertionError(f"node driver failed:\n{proc.stderr}")
        cls.out = json.loads(proc.stdout)
        cls.inputs = {s: format_instance(generate(s)) for s in SEEDS}

    def test_driver_errors(self):
        self.assertEqual(self.out["errors"], [])

    def test_bots_are_legal(self):
        scores = {}
        for b in self.out["bots"]:
            text = self.inputs[b["seed"]]
            code, verdict = check(text, output(b["moves"]), b["rules"])
            self.assertEqual(code, 0, (b["rules"], b["seed"], b["level"], verdict))
            self.assertEqual(b["js"], [code, verdict])
            self.assertTrue(verdict["finished"])
            scores.setdefault((b["rules"], b["level"]), []).append(verdict["score"])
        for rules in ("classic", "balanced"):
            mean = {lv: sum(scores[rules, lv]) / len(SEEDS) for lv in ("easy", "normal", "hard")}
            self.assertLess(mean["easy"], mean["normal"], rules)
            self.assertLess(mean["normal"], mean["hard"], rules)

    def test_normal_bot_is_the_baseline(self):
        for b in self.out["bots"]:
            if b["level"] == "normal":
                self.assertEqual(b["moves"], solve(generate(b["seed"]), b["rules"]), (b["rules"], b["seed"]))

    def test_hotseat_games_are_independent(self):
        for h in self.out["hotseat"]:
            self.assertTrue(h["distinct"])
            self.assertEqual(h["moves"], h["solo"], (h["rules"], h["seed"]))
            for moves in h["moves"]:
                code, verdict = check(self.inputs[h["seed"]], output(moves), h["rules"])
                self.assertEqual(code, 0, verdict)
            # Round by round, seat order: player 1 plays round r (and its shop), then player 2, ...
            turns = []
            for seat, rnd, _phase in h["history"]:
                if not turns or turns[-1] != (seat, rnd):
                    turns.append((seat, rnd))
            self.assertEqual(turns, sorted(turns, key=lambda t: (t[1], t[0])))
            self.assertEqual(len(turns), len(set(turns)))

    def test_draft_rules(self):
        for d in self.out["draft"]:
            inst = generate(d["seed"])
            n = len(d["seats"])
            games = [Game(inst, d["rules"]) for _ in range(n)]
            round_scores = [[] for _ in range(n)]
            visits = {}   # round -> seats in shop-visit order
            for seat, move, rnd, phase in d["history"]:
                g = games[seat]
                self.assertEqual((g.round, g.phase), (rnd, phase))
                if phase == "shop" and (not visits.get(rnd) or visits[rnd][-1] != seat):
                    visits.setdefault(rnd, []).append(seat)
                    # Shop stage: every player still in the run has finished this round.
                    for o in games:
                        self.assertTrue(o.over or o.round > rnd or o.phase == "shop", "shop before all played")
                if move.startswith("buy "):
                    name = move.split()[1]
                    others = [k for k in range(n) if k != seat and games[k].has_mod(name)]
                    self.assertEqual(others, [], f"seat {seat} bought {name}, owned by {others}")
                g.apply(move)
                if phase == "play" and g.phase != "play":
                    round_scores[seat].append(g.round_score)
            for seat in range(n):
                self.assertEqual(games[seat].total, dict((s, t) for s, t, _ in d["standings"])[seat])
                code, verdict = check(self.inputs[d["seed"]], output(d["moves"][seat]), d["rules"])
                self.assertEqual(code, 0, verdict)
                self.assertEqual(d["js"][seat], [code, verdict])
            for rnd, order in visits.items():
                self.assertEqual(order, d["orders"][rnd])
                key = lambda s: (sum(round_scores[s][:rnd + 1]), (s - rnd) % n)
                self.assertEqual(order, sorted(order, key=key), f"round {rnd}")
                self.assertEqual(sorted(order), [s for s in range(n) if len(round_scores[s]) > rnd and games[s].cleared > rnd])
            self.assertTrue(d["restored"])
            places = [p for _, _, p in d["standings"]]
            self.assertEqual(places[0], 1)

    def test_draft_scenario(self):
        s = self.out["scenario"]
        self.assertEqual(s["wrongSeat"], "it is not Ben's turn")
        first = s["first"]
        self.assertIsNotNone(s["pick"])
        self.assertTrue(s["undoInTurn"])
        self.assertFalse(s["undoAfterTurn"])
        self.assertEqual(s["turnAfter"], {"seat": 1 - first, "stage": "shop", "round": 0})
        self.assertEqual(s["ownerSeenBySecond"], [first])
        name = ["Ann", "Ben"][first]
        self.assertEqual(s["secondTry"], f"{s['pick']} was drafted by {name}")
        self.assertEqual(s["secondBuy"], s["secondTry"])
        self.assertEqual(s["claimLater"], first)          # still claimed in the next round
        self.assertEqual(s["claimAfterSell"], -1)         # selling returns it


class TestPlayPageScripts(unittest.TestCase):
    def test_scripts_are_loaded_and_shipped(self):
        """index.html loads the bots and the multiplayer layer, and the site build copies them to /play/."""
        page = (WEB / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r'<script src="([\w.-]+\.js)"></script>', page)
        self.assertEqual(scripts, ["engine.js", "bot.js", "multiplayer.js"])
        spec = importlib.util.spec_from_file_location("site_build", ROOT / "platform" / "site" / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        with tempfile.TemporaryDirectory() as tmp:
            build.build_play(tmp)
            for name in scripts:
                self.assertEqual((Path(tmp) / "play" / name).read_bytes(), (WEB / name).read_bytes())
            self.assertFalse((Path(tmp) / "play" / "multiplayer_driver.js").exists())


if __name__ == "__main__":
    unittest.main()

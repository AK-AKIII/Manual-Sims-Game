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

    @classmethod
    def js_bot(cls, seed, rules, level):
        if not hasattr(cls, "out"):
            cls.setUpClass()
        for b in cls.out["bots"]:
            if (b["seed"], b["rules"], b["level"]) == (seed, rules, level):
                return b["moves"]
        raise KeyError((seed, rules, level))

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

    def test_rounds_games_are_independent(self):
        """The engine's `rounds` mode (online Rounds rooms; it was the old same-device pass & play)."""
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


PARITY_DRIVER = WEB / "draft_parity_driver.js"
PARITY_CASES = [
    {"seed": 3, "rules": "classic", "mode": "draft", "players": [{"name": "Ann"}, {"name": "Ben"}, {"name": "Cy", "bot": "hard"}], "rng": 5},
    {"seed": 8, "rules": "balanced", "mode": "draft", "players": [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D", "bot": "easy"}], "rng": 9},
    {"seed": 21, "rules": "classic", "mode": "draft", "players": [{"name": "A"}, {"name": "B"}], "rng": 2},
    {"seed": 22, "rules": "balanced", "mode": "draft", "players": [{"name": "A", "bot": "normal"}, {"name": "B"}, {"name": "C", "bot": "normal"}], "rng": 3},
    {"seed": 40, "rules": "classic", "mode": "draft", "players": [{"name": "A"}, {"name": "B"}, {"name": "C"}], "rng": 11},
    {"seed": 41, "rules": "classic", "mode": "rounds", "players": [{"name": "A"}, {"name": "B"}, {"name": "C", "bot": "easy"}], "rng": 4},
    {"seed": 42, "rules": "balanced", "mode": "race", "players": [{"name": "A"}, {"name": "B"}, {"name": "Bot", "bot": "hard"}], "rng": 6},
] + [{"seed": 100 + k, "rules": ("classic", "balanced")[k % 2], "mode": "draft",
      "players": [{"name": f"P{i}", **({"bot": ("easy", "normal", "hard")[(k + i) % 3]} if (k + i) % 4 == 0 else {})}
                  for i in range(2 + k % 3)], "rng": 50 + k} for k in range(9)]


@unittest.skipIf(NODE is None, "node is not on PATH; install Node.js to run the multiplayer tests")
class TestPythonPort(unittest.TestCase):
    """poker_run/multiplayer.py and poker_run/bot.py (the online rooms' authority) agree with
    multiplayer.js and bot.js: the same move attempts give the same outcomes, errors, turns,
    shop orders, drafted modifiers, bot moves and standings."""

    @classmethod
    def setUpClass(cls):
        proc = subprocess.run([NODE, str(PARITY_DRIVER), json.dumps({"cases": PARITY_CASES})],
                              capture_output=True, text=True, timeout=600, cwd=str(WEB))
        if proc.returncode != 0:
            raise AssertionError(f"node driver failed:\n{proc.stderr}")
        cls.runs = json.loads(proc.stdout)

    def test_bots_match_bot_js(self):
        from poker_run import bot
        for seed in (1, 2, 5):
            for rules in ("classic", "balanced"):
                for level in ("easy", "normal", "hard"):
                    js = TestMultiplayer.js_bot(seed, rules, level)
                    self.assertEqual(bot.solve(generate(seed), rules, level), js, (seed, rules, level))

    def test_same_attempts_same_outcomes(self):
        from poker_run.multiplayer import Match
        from poker_run.sim import IllegalMove
        kinds = {"ok": 0, "drafted": 0, "turn": 0, "other": 0}
        for case, run in zip(PARITY_CASES, self.runs):
            label = (case["mode"], case["seed"], case["rules"])
            m = Match(generate(case["seed"]), case["mode"], case["rules"], case["players"])
            for i, (seat, move, err, thrown, snap) in enumerate(run["attempts"]):
                where = (*label, i, seat, move)
                self.assertEqual(m.try_move(move, seat), err, where)
                if err is None:
                    m.apply(move, seat)
                    kinds["ok"] += 1
                else:
                    with self.assertRaises(IllegalMove) as cm:
                        m.apply(move, seat)
                    self.assertEqual(str(cm.exception), thrown, where)
                    kinds["drafted" if "drafted by" in err else "turn" if "turn" in err else "other"] += 1
                t = m.turn
                self.assertEqual(t, snap["turn"], where)
                order = m.shop_order(t["round"]) if t and m.mode == "draft" and t["stage"] == "shop" else None
                self.assertEqual(order, snap["order"], where)
                self.assertEqual(m.owners(), snap["owners"], where)
                self.assertEqual([p.game.total for p in m.players], snap["totals"], where)
                self.assertEqual([len(p.moves) for p in m.players], snap["moves"], where)
            self.assertEqual([list(x) for x in m.log], run["log"], label)
            self.assertEqual(m.over, run["over"], label)
            self.assertEqual([[r["seat"], r["total"], r["cleared"], r["place"]] for r in m.standings()], run["standings"], label)
            self.assertEqual([m.shop_order(r) for r in range(m.inst.rounds - 1)], run["orders"], label)
            # replaying the log rebuilds the same match
            again = Match(generate(case["seed"]), case["mode"], case["rules"], case["players"], m.log)
            self.assertEqual(again.log, m.log)
        # the random attempts must actually exercise the draft constraint and turn order
        self.assertGreater(kinds["drafted"], 3, kinds)
        self.assertGreater(kinds["turn"], 10, kinds)
        self.assertGreater(kinds["ok"], 500, kinds)
        sells = sum(1 for run in self.runs for _, mv, err, _, _ in run["attempts"] if mv.startswith("sell ") and err is None)
        self.assertGreater(sells, 5)

    def test_sell_returns_to_pool_and_leave(self):
        from poker_run.multiplayer import LEAVE, Match
        from poker_run import bot
        m = Match(generate(4), "draft", "classic", [{"name": "Ann"}, {"name": "Ben"}])
        self.assertEqual(m.try_move("next", 1), "it is not this player's turn")
        for seat in (0, 1):
            g = m.players[seat].game.clone()
            for mv in bot.round_moves(g, "normal", {}):
                m.apply(mv, seat)
        first = m.turn["seat"]
        second = 1 - first
        g = m.players[first].game
        pick = next(n for n in g.visible_offers() if g.money >= g.rules.price(n))
        m.apply("buy " + pick, first)
        m.apply("next", first)
        name = m.players[first].name
        if pick in m.players[second].game.visible_offers():
            self.assertEqual(m.try_move("buy " + pick, second), f"{pick} was drafted by {name}")
        self.assertEqual(m.owner_of(pick, second), first)
        m.leave(second)   # Ben leaves: his run stops, Ann plays on alone
        self.assertTrue(m.players[second].left)
        self.assertEqual(m.turn["seat"], first)
        self.assertEqual(m.try_move("next", second), "you left this match")
        while m.turn:
            g = m.players[first].game.clone()
            for mv in (bot.round_moves(g, "normal", {}) if g.phase == "play" else ["sell " + pick] * (g.has_mod(pick)) + ["next"]):
                m.apply(mv, first)
                if mv.startswith("sell "):
                    self.assertEqual(m.owner_of(pick, second), -1)   # back in the pool
        self.assertTrue(m.over)
        self.assertIn((second, LEAVE), m.log)
        self.assertEqual(Match(generate(4), "draft", "classic", m.setup, m.log).log, m.log)


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

    def test_site_modes_page_and_links(self):
        """The site renders /modes/ from MULTIPLAYER.md and balanced.md, links it everywhere,
        and every #mode= link on the site names a mode the game knows."""
        spec = importlib.util.spec_from_file_location("site_build", ROOT / "platform" / "site" / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        # the skip marker drops a whole section, up to the next heading of its level
        md = "# T\n\nkeep\n\n<!-- site:skip -->\n## Code\n\nx\n\n### sub\n\ny\n\n## After\n\nz\n<!-- note -->\n"
        self.assertEqual(build.strip_site_skips(md), "# T\n\nkeep\n\n## After\n\nz\n")
        with tempfile.TemporaryDirectory() as tmp:
            out = build.build(str(Path(tmp) / "dist"))
            modes = (Path(out) / "modes" / "index.html").read_text(encoding="utf-8")
            for hid in ("multiplayer-modes", "vs-bot", "the-balanced-ruleset", "every-difference-from-classic"):
                self.assertIn(f'id="{hid}"', modes)
                self.assertIn(f'href="#{hid}"', modes)                  # in the table of contents
            self.assertNotIn("multiplayer_driver.js", modes)            # the repo-only Code section is skipped
            self.assertNotIn("site:skip", modes)
            self.assertIn('aria-current="page">Modes<', modes)
            kinds = set(re.search(r"const KINDS = \{([^}]*)\}", (WEB / "index.html").read_text(encoding="utf-8")).group(1).replace('"', "").split(","))
            kinds = {k.split(":")[0].strip() for k in kinds}
            self.assertEqual(kinds, {"solo", "bot", "online"})   # the same-device modes are gone
            landing = (Path(out) / "index.html").read_text(encoding="utf-8")
            problem = (Path(out) / "problem" / "index.html").read_text(encoding="utf-8")
            # Ways to play: Solo, vs Bot, Online race, Online draft
            self.assertEqual(re.findall(r'class="mode-card" href="/play/#mode=([\w&;=]+)"', landing),
                             ["solo", "bot", "online&amp;create=race", "online&amp;create=draft"])
            creates = re.search(r"const ONLINE_MODES = \{([^}]*)\}", (WEB / "index.html").read_text(encoding="utf-8")).group(1)
            for page in (landing,):
                for c in re.findall(r"create=(\w+)", page):
                    self.assertIn(c + ":", creates.replace(" ", ""))
            self.assertIn('href="/modes/"', landing)
            self.assertIn('href="/modes/"', problem)
            self.assertIn("judged with the <b>classic</b> rules", problem)
            for page in (landing, problem, modes):
                for mode in re.findall(r"#mode=(\w+)", page):
                    self.assertIn(mode, kinds)
                self.assertIn('<a href="/modes/">Modes</a>', page.replace(' aria-current="page"', ""))   # nav
                self.assertIn('<a href="/modes/">Modes &amp; rules</a>', page)                        # footer


if __name__ == "__main__":
    unittest.main()

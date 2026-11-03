import importlib.util
import json
import random
import unittest
from pathlib import Path

from poker_run import Game, IllegalMove, check, classify, format_instance, generate, parse_instance
from poker_run.baseline import best_play, solve as baseline
from poker_run.instance import Rng
from poker_run.rules import HANDS, MODIFIERS, SUITS, face_str, get_ruleset

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "tests" / "fixtures" / "poker_run" / "public"


def card(face):
    """Starting id of a face like "QH"."""
    return SUITS.index(face[1]) * 13 + "23456789TJQKA".index(face[0])


def output(moves):
    return f"{len(moves)}\n" + "\n".join(moves) + "\n"


def shop_game(seed=7, money=100):
    """A game standing in the shop after round 1, with money to spend."""
    g = Game(generate(seed))
    for move in baseline(g.inst):
        g.apply(move)
        if g.phase == "shop":
            break
    g.money = money
    return g


def random_moves(inst, rng, steps=300):
    """Mostly legal random moves that reach deep into the run, for parity tests."""
    g = Game(inst)
    moves = []
    while not g.over and len(moves) < steps:
        if g.phase == "play":
            cards = rng.sample(g.hand, rng.randint(1, min(5, len(g.hand))))
            if g.discards_left and rng.random() < 0.25:
                move = "discard " + " ".join(map(str, cards))
            elif g.round_score >= g.target and rng.random() < 0.3:
                move = "end"
            elif rng.random() < 0.15:
                move = "play " + " ".join(map(str, cards))
            else:
                move = "play " + " ".join(map(str, best_play(g)[0]))
        else:
            deck = [c for c in range(52) if g.in_deck[c]]
            options = ["next", "reroll", "discards", "handsize", f"level {rng.choice(list(g.levels))}",
                       f"remove {rng.choice(deck)}", f"paint {rng.choice(deck)} {rng.choice(SUITS)}",
                       f"promote {rng.choice(deck)}"]
            options += [f"buy {m}" for m in g.visible_offers()] * 3
            options += [f"sell {m}" for m, _ in g.mods]
            move = rng.choice(options)
        try:
            g.apply(move)
        except IllegalMove:
            continue
        moves.append(move)
    return moves


class TestRng(unittest.TestCase):
    def test_mulberry32_reference_values(self):
        # First outputs of mulberry32(1) from the reference JavaScript, times 2**32.
        r = Rng(1)
        self.assertEqual([int(r.next() * 2 ** 32) for _ in range(3)], [2693262067, 11749833, 2265367787])

    def test_generation_is_deterministic(self):
        self.assertEqual(format_instance(generate(5)), format_instance(generate(5)))
        self.assertNotEqual(format_instance(generate(5)), format_instance(generate(6)))

    def test_format_round_trips(self):
        text = format_instance(generate(3))
        self.assertEqual(format_instance(parse_instance(text)), text)


class TestClassify(unittest.TestCase):
    def hand(self, faces):
        ids = [card(f) for f in faces]
        return classify([i % 13 for i in ids], [i // 13 for i in ids])

    def test_types_and_scored_cards(self):
        cases = {
            ("AS",): ("HC", [0]),
            ("2S", "KH", "7D"): ("HC", [1]),
            ("AS", "AH", "3C"): ("PR", [0, 1]),
            ("AS", "AH", "KD", "KC", "2S"): ("TP", [0, 1, 2, 3]),
            ("7S", "7H", "7D", "2C"): ("TK", [0, 1, 2]),
            ("AS", "2H", "3D", "4C", "5S"): ("ST", [0, 1, 2, 3, 4]),
            ("TS", "JH", "QD", "KC", "AS"): ("ST", [0, 1, 2, 3, 4]),
            ("QS", "KH", "AD", "2C", "3S"): ("HC", [2]),
            ("2S", "7S", "9S", "JS", "KS"): ("FL", [0, 1, 2, 3, 4]),
            ("2S", "2H", "2D", "9C", "9S"): ("FH", [0, 1, 2, 3, 4]),
            ("2S", "2H", "2D", "2C", "9S"): ("FK", [0, 1, 2, 3]),
            ("5H", "6H", "7H", "8H", "9H"): ("SF", [0, 1, 2, 3, 4]),
        }
        for faces, expected in cases.items():
            self.assertEqual(self.hand(faces), expected, faces)

    def test_duplicate_hands(self):
        self.assertEqual(classify([7] * 5, [0, 1, 2, 3, 0])[0], "FV")
        self.assertEqual(classify([7] * 5, [2] * 5)[0], "F5")
        self.assertEqual(classify([7, 7, 7, 3, 3], [2] * 5)[0], "HF")


class TestScoring(unittest.TestCase):
    def test_statement_example(self):
        g = Game(generate(7))
        self.assertEqual(g.evaluate([50])["points"], 15)
        g.apply("discard 26 16 17 45")
        res = g.evaluate([33, 8, 9, 23, 50])
        self.assertEqual((res["hand"], res["base"], res["mult"], res["points"]), ("ST", 79, 4, 316))

    def test_levels(self):
        g = Game(generate(7))
        g.levels["HC"] = 3  # base 5 + 2*10, mult 1 + 2*1
        self.assertEqual(g.evaluate([50])["points"], (25 + 10) * 3)

    def test_modifiers(self):
        g = Game(generate(7))  # hand: QH JS KC 6H 8C TS 2D 5H
        qh, js, kc, h6, c8, ts, d2 = 23, 9, 50, 17, 45, 8, 26
        g.mods = [["short_hand", {}]]
        self.assertEqual(g.evaluate([kc])["times"], 2)
        self.assertEqual(g.evaluate([kc, qh, js, h6])["times"], 1)
        g.mods = [["rose_tint", {}], ["high_court", {}]]
        self.assertEqual(g.evaluate([qh])["mult"], 1 + 3 + 3)
        g.mods = [["mirror", {}], ["full_spread", {}]]
        self.assertEqual(g.evaluate([kc, qh, js, h6, c8])["base"], 5 + 10 + 10 + 40)
        g.mods = [["tricolor", {}]]
        self.assertEqual(g.evaluate([d2])["times"], 1)
        g.mods = [["last_call", {}]]
        g.plays_left = 1
        self.assertEqual(g.evaluate([kc])["times"], 3)
        g.mods = [["odd_job", {}]]  # A 3 5 7 9 count; J and K don't
        self.assertEqual(g.evaluate([qh, js, kc, h6, ts])["mult"], 1)
        self.assertEqual(g.evaluate([16])["mult"], 1 + 2)  # 5H
        g.mods = [["thin_deck", {}]]
        g.deck_size = 40
        self.assertEqual(g.evaluate([kc])["mult"], 1 + 12)

    def test_momentum_counts_plays_in_a_row(self):
        g = Game(generate(7))
        g.mods = [["momentum", {"last": "", "streak": 0}]]
        g.apply("play 50")              # HC, streak becomes 1
        self.assertEqual(g.mods[0][1], {"last": "HC", "streak": 1})
        nxt = max(g.hand, key=lambda c: g.rank[c])
        self.assertEqual(g.evaluate([nxt])["mult"], 1 + 2)

    def test_collector_grows_before_scoring(self):
        g = Game(generate(7))
        g.mods = [["collector", {"bonus": 0}]]
        g.apply("discard 26 16 17 45")
        self.assertEqual(g.evaluate([33, 8, 9, 23, 50])["base"], 79 + 10)
        g.apply("play 33 8 9 23 50")
        self.assertEqual(g.mods[0][1]["bonus"], 10)


class TestRounds(unittest.TestCase):
    def test_round_reward_and_failure(self):
        g = Game(generate(7))
        g.apply("discard 26 16 17 45")
        g.apply("play 33 8 9 23 50")    # 316 >= 300
        g.apply("end")                  # 3 plays unused: $4 + $3 + interest 0
        self.assertEqual((g.phase, g.money, g.cleared), ("shop", 4 + 4 + 3, 1))
        g2 = Game(generate(7))
        for _ in range(4):
            g2.apply(f"play {g2.hand[0]}")
        self.assertTrue(g2.over)
        self.assertEqual(g2.cleared, 0)

    def test_end_needs_the_target(self):
        g = Game(generate(7))
        with self.assertRaises(IllegalMove):
            g.apply("end")

    def test_wrong_phase(self):
        g = Game(generate(7))
        for move in ("next", "buy rose_tint", "level PR"):
            with self.assertRaises(IllegalMove):
                g.apply(move)


class TestShop(unittest.TestCase):
    def test_buy_and_window(self):
        g = shop_game()
        offers = g.visible_offers()
        self.assertEqual(len(offers), 3)
        g.apply(f"buy {offers[0]}")
        self.assertEqual(g.visible_offers(), offers[1:])
        with self.assertRaises(IllegalMove):
            g.apply(f"buy {offers[0]}")
        g.apply("reroll")
        self.assertEqual(g.visible_offers(), g.inst.offers[g.round][3:6])
        self.assertEqual(g.prices()["reroll"], 3)

    def test_sell_refunds_half(self):
        g = shop_game()
        name = g.visible_offers()[0]
        g.apply(f"buy {name}")
        before = g.money
        g.apply(f"sell {name}")
        self.assertEqual(g.money, before + MODIFIERS[name][0] // 2)

    def test_deck_edits(self):
        g = shop_game()
        with self.assertRaises(IllegalMove):
            g.apply("paint 0 SH")
        g.apply("paint 0 H")
        g.apply("promote 0")
        self.assertEqual(face_str(*g.face(0)), "3H")
        g.apply("remove 5")
        self.assertEqual(g.deck_size, 51)
        with self.assertRaises(IllegalMove):
            g.apply("remove 5")
        with self.assertRaises(IllegalMove):
            g.apply(f"promote {card('AS')}")
        g.apply("next")
        self.assertNotIn(5, g.hand + g.pile)

    def test_limits(self):
        g = shop_game(money=1000)
        g.deck_size = 20
        with self.assertRaises(IllegalMove):
            g.apply("remove 0")
        g.apply("handsize")
        g.apply("handsize")
        with self.assertRaises(IllegalMove):
            g.apply("handsize")
        self.assertEqual(g.hand_size, 10)
        g.money = 0
        with self.assertRaises(IllegalMove):
            g.apply("level PR")

    def test_slots(self):
        g = shop_game(money=1000)
        g.mods = [[m, {}] for m in ["savings", "mirror", "odd_job", "low_road", "even_split"]]
        name = next(m for m in g.visible_offers() if not g.has_mod(m))
        with self.assertRaises(IllegalMove):
            g.apply(f"buy {name}")


class TestChecker(unittest.TestCase):
    def test_exit_codes(self):
        text = format_instance(generate(7))
        self.assertEqual(check(text, output(["play 50"]))[0], 0)
        self.assertEqual(check(text, output(["play 99"]))[0], 1)
        self.assertEqual(check(text, "two\nplay 50\n")[0], 2)
        self.assertEqual(check(text, "3\nplay 50\n")[0], 2)
        self.assertEqual(check("1 2 3", output([]))[0], 3)

    def test_illegal_scores_zero(self):
        code, v = check(format_instance(generate(7)), output(["play 50", "next"]))
        self.assertEqual((code, v["valid"], v["score"], v["move"]), (1, False, 0, 2))

    def test_public_fixtures(self):
        for expected in sorted(PUBLIC.glob("*.baseline.expected.json")):
            seed = expected.name[:4]
            code, v = check((PUBLIC / f"{seed}.in.txt").read_text(),
                            (PUBLIC / f"{seed}.baseline.out.txt").read_text())
            self.assertEqual(code, 0)
            self.assertEqual(v, json.loads(expected.read_text()), seed)

    def test_public_inputs_match_generator(self):
        for path in sorted(PUBLIC.glob("*.in.txt")):
            self.assertEqual(path.read_text(), format_instance(generate(int(path.name[:4]))))


class TestVersus(unittest.TestCase):
    def test_head_to_head_table(self):
        from poker_run.__main__ import versus_table
        rows, wins = versus_table(["a", "b", "c"], {"a": [10, 50, 30], "b": [20, 40, 30], "c": [0, 0, 99]})
        self.assertEqual(wins["a"]["b"], 1)       # seed 2 only; seed 3 is a tie
        self.assertEqual(wins["c"]["a"], 1)
        self.assertEqual([r["name"] for r in rows], ["a", "b", "c"])
        self.assertEqual(rows[0]["duels_won"], 1)  # beats c 2-1, ties b 1-1


class TestStarter(unittest.TestCase):
    """The starter bundles its own copy of the simulator; it must agree with the judge's."""

    @classmethod
    def setUpClass(cls):
        path = ROOT / "poker_run" / "starters" / "starter.py"
        spec = importlib.util.spec_from_file_location("poker_run_starter", path)
        cls.starter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.starter)

    def test_same_results_as_judge(self):
        rng = random.Random(0)
        for seed in range(1, 21):
            text = format_instance(generate(seed))
            moves = random_moves(parse_instance(text), rng)
            g = self.starter.Game(self.starter.parse_instance(text))
            for move in moves:
                g.apply(move)
            v = check(text, output(moves))[1]
            self.assertEqual((g.total, g.money, g.cleared), (v["score"], v["money"], v["cleared"]), seed)

    def test_starter_is_up_to_date(self):
        build = ROOT / "poker_run" / "starters" / "build_starter.py"
        spec = importlib.util.spec_from_file_location("build_starter", build)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        template = mod.TEMPLATE.read_text().split("\n", 1)[1]
        expected = template.replace("# @RULES@", mod.body((mod.PKG / "rules.py").read_text()).strip())
        expected = expected.replace("# @INPUT@", mod.parser_part((mod.PKG / "instance.py").read_text()))
        expected = expected.replace("# @SIM@", mod.body((mod.PKG / "sim.py").read_text()).strip())
        self.assertEqual(mod.OUT.read_text(), expected,
                         "starter.py is stale: run python3 poker_run/starters/build_starter.py")


class TestBalanced(unittest.TestCase):
    """The optional balanced ruleset: each difference from classic applies, and only those."""

    def setUp(self):
        self.C, self.B = get_ruleset("classic"), get_ruleset("balanced")

    def test_default_is_classic(self):
        self.assertIs(get_ruleset(), self.C)
        self.assertIs(get_ruleset(None), self.C)
        self.assertIs(get_ruleset(self.B), self.B)
        self.assertIs(Game(generate(7)).rules, self.C)
        self.assertIs(Game(generate(7), "balanced").rules, self.B)
        with self.assertRaises(ValueError):
            get_ruleset("chaos")
        # classic is exactly the module constants
        self.assertEqual((self.C.hands, self.C.modifiers, self.C.price_remove), (HANDS, MODIFIERS, 3))

    def test_only_documented_differences(self):
        diff = {k for k, v in vars(self.B).items() if not k.startswith("_") and vars(self.C)[k] != v}
        self.assertEqual(diff, {"name", "hands", "modifiers", "effects", "price_remove", "price_paint",
                                "price_promote", "savings_interest_per", "end_in_last_round", "tricolor_exact"})
        hands = {h[0] for h, c in zip(self.B.hands, self.C.hands) if h != c}
        self.assertEqual(hands, {"TK", "FL", "FH"})
        mods = {m for m in MODIFIERS if self.B.modifiers[m] != self.C.modifiers[m]}
        self.assertEqual(mods, {"ace_dividend", "short_hand", "momentum", "collector", "thin_deck", "tricolor",
                                "savings", "spare_hands"})
        effects = {m for m in MODIFIERS if self.B.effects[m] != self.C.effects[m]}
        self.assertEqual(effects, {"ace_dividend", "short_hand", "momentum", "collector", "thin_deck",
                                   "spare_hands"})
        self.assertEqual(list(self.B.modifiers), list(self.C.modifiers))   # same ids, same order

    def test_hand_values(self):
        g = Game(generate(7), "balanced")
        self.assertEqual(self.B.hand_value("FH", 1), (35, 4))
        self.assertEqual(self.B.hand_value("FL", 2), (70, 7))
        self.assertEqual(self.B.hand_value("TK", 3), (80, 7))
        g.apply("discard 26 16 17 45")   # the statement's straight: ST is unchanged
        self.assertEqual(g.evaluate([33, 8, 9, 23, 50])["points"], 316)
        g.levels["TK"] = 2
        trips = [card("2S"), card("2H"), card("2D")]
        self.assertEqual(g.evaluate(trips)["base"], 55 + 6)

    def test_modifier_effects(self):
        g, c = Game(generate(7), "balanced"), Game(generate(7))
        kc = 50   # KC
        for game, short, last, thin in ((c, 2, 3, 12), (g, 3, 3, 24)):
            game.mods = [["short_hand", {}]]
            self.assertEqual(game.evaluate([kc])["times"], short)
            game.mods = [["last_call", {}]]
            game.plays_left = 1
            self.assertEqual(game.evaluate([kc])["times"], last)
            game.mods = [["thin_deck", {}]]
            game.deck_size = 40
            self.assertEqual(game.evaluate([kc])["mult"], 1 + thin)
        for game, dollars in ((c, 2), (g, 3)):
            game.mods = [["ace_dividend", {}]]
            self.assertEqual(game.evaluate([card("AS")])["money"], dollars)
        # momentum +3 per play in a row
        g.mods = [["momentum", {"last": "HC", "streak": 2}]]
        self.assertEqual(g.evaluate([kc])["mult"], 1 + 6)
        # collector +20 for each straight
        g.mods = [["collector", {"bonus": 0}]]
        g.plays_left = 4
        g.apply("discard 26 16 17 45")
        self.assertEqual(g.evaluate([33, 8, 9, 23, 50])["base"], 79 + 20)
        g.apply("play 33 8 9 23 50")
        self.assertEqual(g.mods[0][1]["bonus"], 20)

    def test_tricolor_wants_exactly_three_suits(self):
        three = [card(f) for f in ("2S", "2H", "2D", "3S", "3H")]   # full house, 3 suits
        four = [card(f) for f in ("2S", "2H", "2D", "3S", "3C")]    # full house, 4 suits
        for rules, times4 in (("classic", 2), ("balanced", 1)):
            g = Game(generate(7), rules)
            g.mods = [["tricolor", {}]]
            self.assertEqual(g.evaluate(three)["times"], 2)
            self.assertEqual(g.evaluate(four)["times"], times4, rules)
            self.assertEqual(g.evaluate(three[:2])["times"], 1)

    def test_prices(self):
        for rules, edit, tricolor in (("classic", 3, 6), ("balanced", 2, 7)):
            g = shop_game(money=100)
            g.rules = get_ruleset(rules)
            self.assertEqual({k: g.prices()[k] for k in ("remove", "paint", "promote")},
                             {"remove": edit, "paint": edit, "promote": edit})
            for move in ("remove 5", "paint 0 H", "promote 0"):
                money = g.money
                g.apply(move)
                self.assertEqual(money - g.money, edit, move)
            self.assertEqual(g.rules.price("tricolor"), tricolor)
        g = shop_game(money=100)
        g.rules = self.B
        name = g.visible_offers()[0]
        g.apply(f"buy {name}")
        self.assertEqual(g.money, 100 - self.B.price(name))
        g.apply(f"sell {name}")
        self.assertEqual(g.money, 100 - self.B.price(name) + self.B.price(name) // 2)

    def round_money(self, rules, mods, money):
        """Money after clearing round 1 with the statement's straight and `end`, holding `money`."""
        g = Game(generate(7), rules)
        g.mods = [[m, {}] for m in mods]
        g.money = money
        g.apply("discard 26 16 17 45")
        g.apply("play 33 8 9 23 50")
        g.apply("end")
        return g.money - money

    def test_savings_and_spare_hands(self):
        # reward = $4 + $3 (unused plays) + interest; 2 discards left
        self.assertEqual(self.round_money("classic", [], 30), 4 + 3 + 5)
        self.assertEqual(self.round_money("balanced", [], 30), 4 + 3 + 5)
        self.assertEqual(self.round_money("classic", ["savings"], 30), 4 + 3 + 6)
        self.assertEqual(self.round_money("balanced", ["savings"], 30), 4 + 3 + 10)
        self.assertEqual(self.round_money("balanced", ["savings"], 12), 4 + 3 + 4)
        self.assertEqual(self.round_money("classic", ["spare_hands"], 0), 4 + 3 + 2 * 2)
        self.assertEqual(self.round_money("balanced", ["spare_hands"], 0), 4 + 3 + 3 * 2)

    def test_end_in_last_round(self):
        inst = generate(5, rounds=2)
        inst.plays, inst.targets = 150, [10, 10]
        for rules, ok in (("classic", True), ("balanced", False)):
            g = Game(inst, rules)
            g.apply(f"play {g.hand[0]}")
            g.apply("end")                  # not the last round: fine
            g.apply("next")
            g.apply(f"play {g.hand[0]}")
            if ok:
                g.apply("end")
                self.assertTrue(g.over)
            else:
                with self.assertRaisesRegex(IllegalMove, "not allowed in the last round"):
                    g.apply("end")
                self.assertEqual(g.phase, "play")

    def test_checker_and_baseline(self):
        text = format_instance(generate(7))
        moves = baseline(generate(7), "balanced")
        code, v = check(text, output(moves), "balanced")
        self.assertEqual((code, v["rules"]), (0, "balanced"))
        self.assertNotIn("rules", check(text, output(moves))[1])
        self.assertEqual(baseline(generate(7)), baseline(generate(7), "classic"))

    def test_cli_passes_rules_to_solver(self):
        from poker_run.__main__ import run_solver
        import shlex
        import sys
        solver = shlex.join([sys.executable, "-c", "import os; print(0 if os.environ['POKER_RULES'] == 'balanced' else 'x')"])
        self.assertEqual(run_solver(solver, "", 20, "balanced")[0].strip(), "0")
        self.assertEqual(run_solver(solver, "", 20)[0].strip(), "x")

    def test_solver_scorer(self):
        import sys
        sys.path.insert(0, str(ROOT / "poker_run" / "solvers"))
        import common
        common.self_test(n=400, rules="balanced")


if __name__ == "__main__":
    unittest.main()

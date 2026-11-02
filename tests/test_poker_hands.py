import unittest

from poker_hands import DEFAULT_CONFIG, Table, best_play, check_action, classify, load_config
from poker_hands.tournament import Bot, run_shared, run_solo

P = DEFAULT_CONFIG["points"]


def greedy(state):
    return {"action": "play", "cards": best_play(state["hand"], state["points"])[0]}


class TestHands(unittest.TestCase):
    def test_classify(self):
        cases = {
            ("AS",): "High Card",
            ("AS", "AH"): "Pair",
            ("AS", "AH", "KD", "KC"): "Two Pair",
            ("7S", "7H", "7D"): "Three of a Kind",
            ("AS", "2H", "3D", "4C", "5S"): "Straight",
            ("TS", "JH", "QD", "KC", "AS"): "Straight",
            ("2S", "7S", "9S", "JS", "KS"): "Flush",
            ("2S", "2H", "2D", "9C", "9S"): "Full House",
            ("2S", "2H", "2D", "2C"): "Four of a Kind",
            ("5H", "6H", "7H", "8H", "9H"): "Straight Flush",
            ("QS", "KH", "AD", "2C", "3S"): "High Card",  # no wrap-around straights
            ("9D", "9D", "9D", "9D", "9D"): "Flush Five",
            ("9D", "9H", "9D", "9C", "9S"): "Five of a Kind",
            ("9D", "9D", "9D", "4D", "4D"): "Flush House",
        }
        for cards, expected in cases.items():
            self.assertEqual(classify(list(cards), P), expected, cards)

    def test_best_play(self):
        idx, t, pts = best_play(["2S", "KH", "KD", "7C", "9S", "KS", "3D"], P)
        self.assertEqual((t, pts, len(idx)), ("Three of a Kind", 30, 3))


class TestGame(unittest.TestCase):
    def test_same_seed_same_game(self):
        a = Table(DEFAULT_CONFIG, 42, [("a", greedy)]).run()
        b = Table(DEFAULT_CONFIG, 42, [("b", greedy)]).run()
        self.assertEqual(a[0]["score"], b[0]["score"])

    def test_random_deck_changes_with_seed(self):
        cfg = load_config(deck="random")
        self.assertNotEqual(Table(cfg, 1, []).composition, Table(cfg, 2, []).composition)

    def test_plays_limit_and_deck_exhaustion(self):
        cfg = load_config(plays=100)
        r = Table(cfg, 0, [("a", greedy)]).run()[0]
        self.assertIsNone(r["error"])
        self.assertLessEqual(sum(r["hands_played"].values()), 52)  # stops when the deck and hand are empty

    def test_invalid_action_ends_game(self):
        r = Table(DEFAULT_CONFIG, 0, [("bad", lambda s: {"action": "play", "cards": [99]})]).run()[0]
        self.assertIn("invalid action", r["error"])
        self.assertEqual(r["score"], 0)

    def test_buying(self):
        cfg = load_config(starting_money=100)
        t = Table(cfg, 0, [("a", greedy)])
        state = t.state_for(t.players[0])
        self.assertIsNone(check_action(state, {"action": "buy", "item": "hand_size"}))
        t._buy(t.players[0], "hand_size", state["shop"]["hand_size"])
        self.assertEqual(len(t.players[0].hand), 8)
        self.assertEqual(t.players[0].money, 100 - cfg["shop"]["hand_size"])

    def test_shared_deck_is_split(self):
        t = Table(DEFAULT_CONFIG, 0, [("a", greedy), ("b", greedy)])
        self.assertEqual(len(t.deck), 52 - 14)
        t.run()
        played = sum(p.hands_played.total() for p in t.players)
        self.assertGreater(played, 0)

    def test_tournaments(self):
        bots = [Bot("g", [greedy]), Bot("g3", [greedy, greedy, greedy])]
        solo = run_solo(bots, DEFAULT_CONFIG, range(5)).leaderboard()
        self.assertAlmostEqual(solo[0]["score"], solo[1]["score"])
        shared = run_shared(bots, DEFAULT_CONFIG, range(5), "pairs").leaderboard()
        self.assertEqual(len(shared), 2)


if __name__ == "__main__":
    unittest.main()

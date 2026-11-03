"""Online multiplayer rooms (prplatform/rooms.py, API.md "Online multiplayer")."""

import threading
import time
import unittest
from datetime import timedelta

from prtest_helpers import PlatformTest

from poker_run import bot
from poker_run.checker import check
from poker_run.instance import format_instance, generate
from poker_run.multiplayer import Match
from poker_run.sim import Game
from prplatform import db, rooms


def ago(seconds):
    return db.iso(db.now() - timedelta(seconds=seconds))


class RoomTest(PlatformTest):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.restore_limits)
        self.ann = self.user_client("ann")
        self.ben = self.user_client("ben")

    @staticmethod
    def restore_limits():
        """Tests override LIMITS attributes on the instance; drop the overrides."""
        for k in list(vars(rooms.LIMITS)):
            delattr(rooms.LIMITS, k)

    def room_id(self, code):
        return self.conn.execute("SELECT id FROM mp_rooms WHERE code = ?", (code,)).fetchone()[0]

    @property
    def state(self):
        return rooms.STATE[self.settings.db]

    def ok(self, r, status=200):
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def create(self, client=None, **body):
        body = {"mode": "draft", "rules": "classic", "seed": 4, "max_players": 2, **body}
        return self.ok((client or self.ann).post("/api/rooms", json=body), 201)

    def get(self, client, code, **params):
        return self.ok(client.get(f"/api/rooms/{code}", params=params))

    def post(self, client, code, what, body=None, status=200):
        r = client.post(f"/api/rooms/{code}/{what}", json=body or {})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def started(self, mode="draft", seed=4, **kw):
        room = self.create(mode=mode, seed=seed, **kw)
        code = room["code"]
        self.post(self.ben, code, "join")
        self.post(self.ben, code, "ready", {"ready": True})
        return self.post(self.ann, code, "start")

    # A stand-in for a person: the normal bot's next move for this player, from the room view.
    def next_move(self, view):
        me = next(p for p in view["players"] if p["you"])
        g = Game(generate(view["seed"]), view["rules"])
        for m in me["moves"]:
            g.apply(m)
        hands = {}
        for r in me["rounds"]:
            for p in r["plays"]:
                hands[p["hand"]] = hands.get(p["hand"], 0) + 1
        owners = view["owners"]
        if g.phase == "play":
            return bot.round_moves(g, "normal", hands)[0]
        return bot.shop_moves(g, "normal", hands, lambda n: owners.get(n, me["seat"]) == me["seat"])[0]

    def play_out(self, code, clients):
        """Both players move whenever the server says it's their turn, until the game ends."""
        for _ in range(3000):
            views = {c: self.get(c, code) for c in clients}
            v = next(iter(views.values()))
            if v["status"] == "finished":
                return v
            moved = False
            for c, view in views.items():
                me = next(p for p in view["players"] if p["you"])
                mine = view["turn"] and view["turn"]["seat"] == me["seat"]
                if (view["mode"] == "race" and not me["over"] and me["status"] == "in") or mine:
                    self.post(c, code, "move", {"move": self.next_move(view)})
                    moved = True
                    break
            self.assertTrue(moved, "nobody could move")
        self.fail("the game never ended")


class LobbyTest(RoomTest):
    def test_create_join_codes_and_auth(self):
        room = self.create()
        code = room["code"]
        self.assertRegex(code, r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{5}$")
        self.assertEqual((room["status"], room["you"], room["host"], room["version"]), ("lobby", 0, 0, 1))
        self.assertEqual([p["name"] for p in room["players"]], ["ann"])
        # signed out: 401; no CSRF header: 403
        anon = self.new_client()
        self.assertEqual(anon.post("/api/rooms", json={}).status_code, 401)
        self.assertEqual(anon.get(f"/api/rooms/{code}").status_code, 401)
        raw = self.new_client()
        raw.cookies.update(self.ben.cookies)
        raw.headers.pop("X-Requested-With")
        self.assertEqual(raw.post(f"/api/rooms/{code}/join").status_code, 403)
        # a non-member sees the lobby (to decide to join) without a seat
        peek = self.get(self.ben, code)
        self.assertIsNone(peek["you"])
        # join by code, any case; joining again resumes the same seat
        v = self.post(self.ben, code.lower(), "join")
        self.assertEqual(v["you"], 1)
        self.assertEqual(self.post(self.ben, code, "join")["you"], 1)
        self.assertEqual([(p["name"], p["ready"], p["host"]) for p in v["players"]],
                         [("ann", True, True), ("ben", False, False)])
        # full
        cy = self.user_client("cyd")
        r = cy.post(f"/api/rooms/{code}/join")
        self.assertEqual((r.status_code, r.json()["error"]), (409, "the room is full"))
        # unknown and malformed codes
        self.assertEqual(cy.get("/api/rooms/ZZZZZ").status_code, 404)
        self.assertEqual(cy.post("/api/rooms/0O1I!/join").status_code, 404)
        self.assertEqual(cy.get("/api/rooms/" + "A" * 300).status_code, 404)

    def test_validation(self):
        for bad in ({"mode": "chess"}, {"rules": "x"}, {"seed": -1}, {"seed": 2 ** 32}, {"max_players": 5},
                    {"max_players": 1}, {"bots": ["easy", "hard"]}, {"bots": ["god"]}, {"seed": "abc"}):
            r = self.ann.post("/api/rooms", json={"mode": "race", "max_players": 2, **bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("error", r.json())
        room = self.create(seed=None)
        self.assertTrue(1 <= room["seed"] <= 1_000_000)

    def test_ready_start_kick_leave(self):
        code = self.create(max_players=3)["code"]
        self.post(self.ben, code, "join")
        self.post(self.ben, code, "start", status=403)              # only the host
        r = self.post(self.ann, code, "start", status=409)
        self.assertIn("waiting for ben", r["error"])
        self.post(self.ben, code, "ready", {"ready": True})
        # host changes the terms: ready resets
        v = self.post(self.ann, code, "settings", {"mode": "race", "rules": "balanced", "bots": ["easy"]})
        self.assertEqual((v["mode"], v["rules"]), ("race", "balanced"))
        self.assertEqual([(p["name"], p["ready"]) for p in v["players"]],
                         [("ann", True), ("ben", False), ("Easy bot", True)])
        self.post(self.ann, code, "settings", {"bots": ["easy", "hard"]}, status=409)   # doesn't fit
        self.post(self.ben, code, "settings", {"rules": "classic"}, status=403)
        # kick: ben is out and can't come back
        self.post(self.ann, code, "kick", {"seat": 1})
        self.assertEqual(self.ben.get(f"/api/rooms/{code}").status_code, 403)
        self.assertEqual(self.ben.post(f"/api/rooms/{code}/join").json()["error"], "the host removed you from this room")
        cy = self.user_client("cyd")
        self.assertEqual(self.post(cy, code, "join")["you"], 1)
        # host leaves: cy becomes host; cy leaves: the room is gone
        self.post(self.ann, code, "leave")
        v = self.get(cy, code)
        self.assertEqual([(p["name"], p["host"]) for p in v["players"]], [("cyd", True), ("Easy bot", False)])
        self.assertEqual(self.post(cy, code, "leave"), {"deleted": True})
        self.assertEqual(cy.get(f"/api/rooms/{code}").status_code, 404)

    def test_my_rooms(self):
        code = self.create()["code"]
        self.post(self.ben, code, "join")
        rows = self.ok(self.ben.get("/api/rooms"))
        self.assertEqual([(r["code"], r["status"], r["you"], r["players"]) for r in rows], [(code, "lobby", 1, 2)])


class GameTest(RoomTest):
    def test_draft_turns_constraint_and_results(self):
        v = self.started("draft", seed=4)
        code = v["code"]
        self.assertEqual((v["status"], v["turn"]), ("playing", {"seat": 0, "stage": "play", "round": 0}))
        # wrong player: 409; nonsense: 400; illegal for the game: 400
        bv = self.get(self.ben, code)
        r = self.ben.post(f"/api/rooms/{code}/move", json={"move": self.next_move(bv)})
        self.assertEqual(r.status_code, 409)
        self.assertIn("not ben's turn", r.json()["error"])
        for bad in ("", "x" * 300, "play; DROP TABLE", "fly 1", "play 1 2 3 4 5 6 7"):
            self.assertEqual(self.ann.post(f"/api/rooms/{code}/move", json={"move": bad}).status_code, 400, bad)
        r = self.ann.post(f"/api/rooms/{code}/move", json={"move": "play 999"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not in your hand", r.json()["error"])
        # round 1 for both
        for c in (self.ann, self.ben):
            while True:
                view = self.get(c, code)
                if view["turn"]["stage"] != "play" or not next(p for p in view["players"] if p["you"])["seat"] == view["turn"]["seat"]:
                    break
                self.post(c, code, "move", {"move": self.next_move(view)})
        view = self.get(self.ann, code)
        self.assertEqual(view["turn"]["stage"], "shop")
        order = view["shop_order"]
        first = [self.ann, self.ben][order[0]]
        second = [self.ann, self.ben][order[1]]
        fv = self.get(first, code)
        me = next(p for p in fv["players"] if p["you"])
        g = Game(generate(4), "classic")
        for m in me["moves"]:
            g.apply(m)
        pick = next(n for n in g.visible_offers() if g.money >= g.rules.price(n))
        self.post(first, code, "move", {"move": "buy " + pick})
        sv = self.post(first, code, "move", {"move": "next"})
        self.assertEqual(sv["owners"], {pick: order[0]})
        r = second.post(f"/api/rooms/{code}/move", json={"move": "buy " + pick})
        if r.status_code == 400:  # offered to the second player too: drafted
            self.assertIn(f"{pick} was drafted by", r.json()["error"])
        # the others' details: summaries yes, move lists no
        other = next(p for p in self.get(second, code)["players"] if not p["you"])
        self.assertNotIn("moves", other)
        self.assertEqual(other["mods"], [pick])
        end = self.play_out(code, [self.ann, self.ben])
        self.assertEqual(end["status"], "finished")
        self.assertEqual(end["ended"], "done")
        self.assertIsNone(end["turn"])
        # every move list is a valid solo output, places are set, the log replays
        text = format_instance(generate(4))
        for p in end["players"]:
            code_, verdict = check(text, f"{len(p['moves'])}\n" + "".join(m + "\n" for m in p["moves"]), "classic")
            self.assertEqual(code_, 0, verdict)
            self.assertEqual(verdict["score"], p["total"])
            self.assertIn(p["place"], (1, 2))
        log = [(r_["seat"], r_["move"]) for r_ in self.conn.execute(
            f"SELECT seat, move FROM mp_moves WHERE room_id = (SELECT id FROM mp_rooms WHERE code = '{code}') ORDER BY n")]
        m = Match(generate(4), "draft", "classic", [{"name": "ann"}, {"name": "ben"}], log)
        self.assertTrue(m.over)
        # results saved to both accounts
        for c, name in ((self.ann, "ann"), (self.ben, "ben")):
            h = self.ok(c.get("/api/matches"))
            self.assertEqual(h["record"]["played"], 1)
            mt = h["matches"][0]
            self.assertEqual((mt["code"], mt["mode"], mt["seed"], mt["ended"]), (code, "draft", 4, "done"))
            self.assertEqual(mt["players"][mt["you"]]["name"], name)
            d = self.ok(c.get(f"/api/matches/{mt['id']}"))
            self.assertEqual([p["moves"] for p in d["players"]], [p["moves"] for p in end["players"]])
        wins = sum(self.ok(c.get("/api/matches"))["record"]["wins"] for c in (self.ann, self.ben))
        self.assertGreaterEqual(wins, 1)
        cy = self.user_client("cyd")
        self.assertEqual(cy.get(f"/api/matches/{mt['id']}").status_code, 404)
        self.assertEqual(self.ok(cy.get("/api/matches")), {"record": {"played": 0, "wins": 0}, "matches": []})
        # no effect on the contest leaderboard
        self.assertEqual(self.ok(self.ann.get("/api/leaderboard"))["rows"], [])
        # moves after the end: 409
        self.assertEqual(self.ann.post(f"/api/rooms/{code}/move", json={"move": "next"}).status_code, 409)
        # rematch: one new room for everyone
        new = self.post(self.ben, code, "rematch")["code"]
        self.assertEqual(self.post(self.ann, code, "rematch")["code"], new)
        self.assertEqual(self.get(self.ann, code)["rematch"], new)
        self.assertEqual(self.post(self.ann, new, "join")["you"], 1)

    def test_race_with_bot_and_hidden_rounds(self):
        room = self.create(mode="race", seed=7, max_players=3, bots=["normal"])
        code = room["code"]
        self.post(self.ben, code, "join")
        self.post(self.ben, code, "ready")
        v = self.post(self.ann, code, "start")
        names = [p["name"] for p in v["players"]]
        self.assertEqual(names, ["ann", "Normal bot", "ben"])
        botp = v["players"][1]
        self.assertTrue(botp["hidden"])            # the bot has played its run; nothing shown yet
        self.assertEqual((botp["total"], botp["rounds"]), (0, []))
        self.assertTrue(botp["over"])
        # the bot played exactly the baseline
        moves = [r["move"] for r in self.conn.execute(
            "SELECT move FROM mp_moves WHERE seat = 1 ORDER BY n")]
        from poker_run.baseline import solve
        self.assertEqual(moves, solve(generate(7), "classic"))
        # ann finishes round 1: she sees the bot's and ben's round 1 (if played), not more
        while True:
            view = self.get(self.ann, code)
            me = view["players"][0]
            if me["rounds"] and me["rounds"][0]["cleared"] is not None:
                break
            self.post(self.ann, code, "move", {"move": self.next_move(view)})
        view = self.get(self.ann, code)
        self.assertEqual(len(view["players"][1]["rounds"]), 1)
        self.assertEqual(view["players"][2]["rounds"], [])
        # both play at the same time (race), to the end
        end = self.play_out(code, [self.ann, self.ben])
        self.assertEqual(sorted(p["place"] for p in end["players"])[0], 1)
        self.assertFalse(any(p["hidden"] for p in end["players"]))
        self.assertEqual(end["players"][1]["moves"], moves)

    def test_leave_mid_game_and_afk_removal(self):
        v = self.started("rounds", seed=9)
        code = v["code"]
        # ben is away: no polls, no moves; ann can't remove him before the timeout
        rooms.LIMITS.afk_seconds = 60
        view = self.get(self.ann, code)
        while view["turn"]["seat"] == 0:
            view = self.post(self.ann, code, "move", {"move": self.next_move(view)})
        self.assertEqual(view["turn"]["seat"], 1)
        self.assertFalse(view["players"][1]["removable"])
        self.post(self.ann, code, "remove", {"seat": 1}, status=409)
        # an hour later (in the database) and ben not seen for a while
        self.conn.execute("UPDATE mp_moves SET created = ?", (ago(3600),))
        self.state["presence"].seen[(self.room_id(code), self.user_id("ben"))] = time.time() - 600
        view = self.get(self.ann, code)
        self.assertTrue(view["players"][1]["removable"])
        self.post(self.ann, code, "remove", {"seat": 0}, status=400)
        v = self.post(self.ann, code, "remove", {"seat": 1})
        self.assertEqual(v["players"][1]["status"], "removed")
        self.assertEqual(v["turn"]["seat"], 0)            # the game goes on without him
        self.assertEqual(self.ben.post(f"/api/rooms/{code}/move", json={"move": "next"}).status_code, 409)
        # ann leaves too: the game ends and both are saved, ben's run scoring what it had
        end = self.post(self.ann, code, "leave")
        self.assertEqual(end["status"], "finished")
        h = self.ok(self.ben.get("/api/matches"))["matches"][0]
        self.assertEqual([p["left"] for p in h["players"]], [True, True])

    def test_expiry(self):
        lobby = self.create()["code"]
        game = self.started("race", seed=3)["code"]
        rooms.STATE[self.settings.db]["sweep_at"][0] = 0
        self.conn.execute("UPDATE mp_rooms SET active = ? WHERE code = ?", (ago(31 * 60), lobby))
        self.assertEqual(self.ann.get(f"/api/rooms/{lobby}").status_code, 404)
        self.conn.execute("UPDATE mp_rooms SET active = ? WHERE code = ?", (ago(3 * 3600), game))
        v = self.get(self.ben, game)
        self.assertEqual((v["status"], v["ended"]), ("finished", "expired"))
        self.assertEqual(self.ok(self.ben.get("/api/matches"))["matches"][0]["ended"], "expired")
        # finished rooms are kept a while for the results, then deleted (the match stays)
        self.conn.execute("UPDATE mp_rooms SET finished = ? WHERE code = ?", (ago(31 * 60), game))
        self.assertEqual(self.ann.get(f"/api/rooms/{game}").status_code, 404)
        self.assertEqual(len(self.ok(self.ann.get("/api/matches"))["matches"]), 1)
        # the periodic sweep removes idle lobbies nobody looks at
        other = self.create(client=self.ben)["code"]
        self.conn.execute("UPDATE mp_rooms SET active = ? WHERE code = ?", (ago(31 * 60), other))
        rooms.STATE[self.settings.db]["sweep_at"][0] = 0
        self.ok(self.ann.get("/api/rooms"))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM mp_rooms WHERE code = ?", (other,)).fetchone())


class LimitTest(RoomTest):
    def test_rate_and_room_limits(self):
        rooms.LIMITS.open_rooms_per_user = 2
        self.create()
        self.create()
        r = self.ann.post("/api/rooms", json={"mode": "race"})
        self.assertEqual(r.status_code, 429)
        self.assertIn("open rooms", r.json()["error"])
        rooms.LIMITS.open_rooms_per_user = 50
        rooms.LIMITS.create_per_window = 4
        self.create()
        r = self.ann.post("/api/rooms", json={"mode": "race"})
        self.assertEqual(r.status_code, 429)
        rooms.LIMITS.max_rooms = 3
        r = self.ben.post("/api/rooms", json={"mode": "race"})
        self.assertEqual(r.status_code, 503)

    def test_move_rate_limit(self):
        rooms.LIMITS.moves_per_minute = 5
        code = self.started("race", seed=2)["code"]
        statuses = [self.ann.post(f"/api/rooms/{code}/move", json={"move": "reroll"}).status_code for _ in range(7)]
        self.assertEqual(statuses[-2:], [429, 429])


class LiveTest(RoomTest):
    def test_versions_and_long_poll(self):
        v = self.started("draft", seed=4)
        code, ver = v["code"], v["version"]
        # nothing new: wait=0 answers at once with the same version
        t0 = time.monotonic()
        self.assertEqual(self.get(self.ben, code, since=ver)["version"], ver)
        # long poll: ben waits; ann moves; ben hears about it at once
        got = {}

        def wait():
            c = self.new_client()
            c.cookies.update(self.ben.cookies)
            t = time.monotonic()
            got["v"] = c.get(f"/api/rooms/{code}", params={"since": ver, "wait": 10}).json()
            got["t"] = time.monotonic() - t

        th = threading.Thread(target=wait)
        th.start()
        time.sleep(0.5)
        nv = self.post(self.ann, code, "move", {"move": self.next_move(self.get(self.ann, code))})
        th.join(15)
        self.assertGreater(got["v"]["version"], ver)
        self.assertEqual(got["v"]["version"], nv["version"])
        self.assertLess(got["t"], 3)   # woken by the move, not by the 5 s re-check
        # a long poll with no change ends at its timeout with the same version
        rooms.LIMITS.long_poll_max = 1.0
        t0 = time.monotonic()
        self.assertEqual(self.get(self.ben, code, since=nv["version"], wait=30)["version"], nv["version"])
        self.assertGreaterEqual(time.monotonic() - t0, 0.9)
        # presence
        players = self.get(self.ann, code)["players"]
        self.assertEqual([p["online"] for p in players], [True, True])

    def test_concurrent_moves(self):
        """Two players acting at the same moment: every accepted move is in the log once, in order."""
        code = self.started("race", seed=5)["code"]
        errors = []

        def run(client):
            c = self.new_client()
            c.cookies.update(client.cookies)
            for _ in range(25):
                view = self.get(c, code)
                me = next(p for p in view["players"] if p["you"])
                if me["over"]:
                    return
                r = c.post(f"/api/rooms/{code}/move", json={"move": self.next_move(view)})
                if r.status_code != 200:
                    errors.append(r.text)

        ths = [threading.Thread(target=run, args=(c,)) for c in (self.ann, self.ben)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(60)
        self.assertEqual(errors, [])
        rid = self.room_id(code)
        ns = [r[0] for r in self.conn.execute("SELECT n FROM mp_moves WHERE room_id = ? ORDER BY n", (rid,))]
        self.assertEqual(ns, list(range(len(ns))))
        self.assertGreaterEqual(len(ns), 40)
        ver = self.conn.execute("SELECT version FROM mp_rooms WHERE id = ?", (rid,)).fetchone()[0]
        self.assertEqual(ver, 1 + 1 + 1 + 1 + len(ns))   # create, join, ready, start, one per move
        # the stored log replays, and agrees with what each player sees
        self.state["cache"].drop(rid)
        view = self.get(self.ann, code)
        log = [(r["seat"], r["move"]) for r in self.conn.execute(
            "SELECT seat, move FROM mp_moves WHERE room_id = ? ORDER BY n", (rid,))]
        m = Match(generate(5), "race", "classic", [{"name": "ann"}, {"name": "ben"}], log)
        self.assertEqual(view["players"][0]["moves"], m.players[0].moves)

    def test_draft_simultaneous_only_one_wins(self):
        v = self.started("draft", seed=4)
        code = v["code"]
        ann_move = self.next_move(self.get(self.ann, code))
        ben_view = self.get(self.ben, code)
        me = ben_view["players"][1]
        g = Game(generate(4))
        ben_move = "play " + str(g.hand[0])
        res = {}
        barrier = threading.Barrier(2)

        def go(name, client, mv):
            c = self.new_client()
            c.cookies.update(client.cookies)
            barrier.wait()
            res[name] = c.post(f"/api/rooms/{code}/move", json={"move": mv}).status_code

        ths = [threading.Thread(target=go, args=("ann", self.ann, ann_move)),
               threading.Thread(target=go, args=("ben", self.ben, ben_move))]
        for t in ths:
            t.start()
        for t in ths:
            t.join(30)
        self.assertEqual(res, {"ann": 200, "ben": 409})
        self.assertEqual(me["moves"], [])


if __name__ == "__main__":
    unittest.main()

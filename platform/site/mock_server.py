#!/usr/bin/env python3
"""Mock Poker Run backend for developing the static site without the real API.

    python3 platform/site/build.py
    python3 platform/site/mock_server.py              # http://127.0.0.1:8765/

Serves platform/site/dist/ and implements every endpoint of platform/API.md in
memory with fake data. Stdlib only. Nothing is persisted; restart to reset.

Online rooms (/api/rooms, /api/matches) are implemented too, with the real game
logic (poker_run.multiplayer from this repository), minus rate limits, expiry
and removing away players.

Accounts that exist at start:  admin / adminpass (admin)   demo / demopass (has history)
Judging is simulated: a submission is queued for ~2 s, then runs one test at a
time (tests_done climbs), then is done. The verdicts depend on the source:
    contains "raise"         some tests RE
    contains "sleep"         some tests TLE
    contains "WRONG"         some tests WA
    no "print"/"stdout"      every test WA (no output)
    contains "JUDGE_ERROR"   the submission ends with status "error"
Options: --invite CODE (registration needs it), --frozen, --closed (full
submissions get 403), --upcoming (contest starts in 2 h), --no-deadline,
--port N, --dir PATH, --speed X (judge speed multiplier).
"""

import argparse
import hashlib
import json
import os
import re
import random
import secrets
import threading
import time
from datetime import datetime, timezone
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = threading.RLock()
T0 = time.time()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def h(*parts):
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


class State:
    def __init__(self, args):
        self.args = args
        self.users = {}       # username -> {pw, is_admin, created, id}
        self.sessions = {}    # token -> username
        self.subs = []        # dicts, ids ascending
        self.next_id = 1
        self.public_seeds = list(range(1, 11))
        self.hidden = 50
        # best raw score per hidden test among the fake field
        self.best = [40000 + h("best", i) % 160000 for i in range(self.hidden)]
        now = time.time()
        if args.upcoming:
            self.start, self.end = now + 2 * 3600 + 17, now + 9 * 86400
        elif args.closed:
            self.start, self.end = now - 10 * 86400, now - 3600
        elif args.no_deadline:
            self.start = self.end = None
        else:
            self.start, self.end = now - 2 * 86400, now + 3 * 86400 + 5 * 3600 + 42 * 60
        self.freeze = (now - 600) if args.frozen else None
        self.add_user("admin", "adminpass", True)
        self.add_user("demo", "demopass", False)
        self.rooms = Rooms()
        self.seed_field(now)

    # ---- fake data
    def add_user(self, name, pw, admin=False, created=None):
        self.users[name] = {"pw": pw, "is_admin": admin, "created": created or time.time(), "id": len(self.users) + 1}

    def seed_field(self, now):
        names = ["ace_high", "rivalrook", "flushfive", "beam_queen", "mcts_mike", "greedy_gus", "twin_engine",
                 "lastcall", "momentum", "low_road", "hal9000", "paintpromote", "shopkeeper", "kicker"]
        rng = random.Random(7)
        for k, name in enumerate(names):
            self.add_user(name, "x" * 12)
            skill = 0.92 - 0.055 * k + rng.uniform(-0.02, 0.02)
            for j in range(1 + k % 4):
                t = now - 86400 * 1.5 + k * 3000 + j * 900
                self.fake_done(name, "full", t, skill - 0.04 * (k % 4 - j))
        # demo user's history: two public runs and a full run
        self.fake_done("demo", "public", now - 7200, 0.5)
        self.fake_done("demo", "public", now - 5400, 0.6, src_extra="# second try\n")
        self.fake_done("demo", "full", now - 3600, 0.55)

    def fake_done(self, user, kind, t, skill, src_extra=""):
        src = STARTER_FAKE % (user, src_extra)
        sub = self.new_sub(user, kind, src, t)
        sub["fake_skill"] = max(0.05, min(0.99, skill))
        sub["created_t"] = t
        sub["forced_done"] = True

    def new_sub(self, user, kind, source, t=None):
        t = t or time.time()
        sub = {"id": self.next_id, "user": user, "kind": kind, "source": source, "created_t": t, "error_text": None,
               "tests_total": len(self.public_seeds) if kind == "public" else self.hidden, "forced_done": False}
        self.next_id += 1
        self.subs.append(sub)
        return sub

    # ---- simulated judging
    def timeline(self, sub):
        """(status, tests_done, started, finished) at the current time."""
        if sub["forced_done"]:
            return "done", sub["tests_total"], sub["created_t"] + 1, sub["created_t"] + 9
        speed = self.args.speed
        per = (0.45 if sub["kind"] == "public" else 0.12) / speed
        # one at a time: wait for earlier unfinished submissions
        start = sub["created_t"] + 2.0 / speed
        for other in self.earlier(sub):
            o_end = self.finish_time(other)
            if o_end > sub["created_t"]:
                start = max(start, o_end + 0.3 / speed)
        end = start + per * sub["tests_total"]
        now = time.time()
        if now < start:
            return "queued", 0, None, None
        if now < end:
            return "running", min(sub["tests_total"] - 1, int((now - start) / per)), start, None
        return "done", sub["tests_total"], start, end

    def finish_time(self, sub):
        if sub["forced_done"]:
            return sub["created_t"]
        speed = self.args.speed
        per = (0.45 if sub["kind"] == "public" else 0.12) / speed
        start = sub["created_t"] + 2.0 / speed
        for other in self.earlier(sub):
            o_end = self.finish_time(other)
            if o_end > sub["created_t"]:
                start = max(start, o_end + 0.3 / speed)
        return start + per * sub["tests_total"]

    def earlier(self, sub):
        """Simulated submissions enqueued before this one (queue order = enqueue time)."""
        key = (sub["created_t"], sub["id"])
        return [o for o in self.subs if not o["forced_done"] and (o["created_t"], o["id"]) < key]

    def position(self, sub):
        pos = 1
        for other in self.earlier(sub):
            if self.timeline(other)[0] in ("queued", "running"):
                pos += 1
        return pos

    def result(self, sub, k):
        """Result for test k (0-based)."""
        src = sub["source"]
        r = h(sub["id"], sub.get("rejudged", 0), k, src)
        verdict, msg = "AC", ""
        if "print" not in src and "stdout" not in src:
            verdict, msg = "WA", "unparsable output: empty output"
        elif "raise" in src and r % 4 == 0:
            verdict, msg = "RE", "Traceback (most recent call last): ... ValueError"
        elif "sleep" in src and r % 3 == 0:
            verdict, msg = "TLE", "time limit exceeded"
        elif "WRONG" in src and r % 3 == 1:
            verdict, msg = "WA", "move %d ('play 3 17'): card 17 is not in your hand" % (5 + r % 40)
        skill = sub.get("fake_skill", 0.35 + (h(src) % 1000) / 1000 * 0.4)
        if sub["kind"] == "public":
            seed = self.public_seeds[k]
            base = 30000 + h("pub", seed) % 120000
            score = int(base * skill * (0.7 + (r % 600) / 1000)) if verdict == "AC" else 0
            cleared = min(8, max(1, int(8 * skill + (r % 3) - 1))) if verdict == "AC" else 0
            t = round(0.15 + (r % 1700) / 1000, 2) if verdict != "TLE" else 2.0
            return {"seed": seed, "verdict": verdict, "score": score, "cleared": cleared, "time": t,
                    "message": msg or "valid, %d moves" % (60 + r % 90)}
        raw = int(self.best[k] * skill * (0.75 + (r % 500) / 1000)) if verdict == "AC" else 0
        t = round(0.1 + (r % 1800) / 1000, 2) if verdict != "TLE" else 2.0
        return {"test": k + 1, "verdict": verdict, "time": t, "_raw": raw}

    def row(self, sub, with_user=False):
        status, done, started, finished = self.timeline(sub)
        if status == "done" and "JUDGE_ERROR" in sub["source"]:
            status = "error"
        res = [self.result(sub, k) for k in range(done)]
        out = {"id": sub["id"], "kind": sub["kind"], "status": status, "created": iso(sub["created_t"]),
               "finished": iso(finished) if finished and status in ("done", "error") else None,
               "raw_total": None, "relative_total": None, "tests_ac": None, "tests_total": sub["tests_total"],
               "tests_done": done if status != "queued" else 0, "max_time": None, "error": None}
        if status == "queued":
            out["position"] = self.position(sub)
        if status == "error":
            out["error"] = "sandbox failed: container exited with code 137"
        if status == "done":
            out["tests_ac"] = sum(1 for r in res if r["verdict"] == "AC")
            out["max_time"] = max((r["time"] for r in res), default=0)
            if sub["kind"] == "public":
                out["raw_total"] = sum(r["score"] for r in res)
            else:
                out["raw_total"] = sum(r["_raw"] for r in res)
                out["relative_total"] = sum(round(1_000_000 * r["_raw"] / max(r["_raw"], self.best[r["test"] - 1])) for r in res)
        if with_user:
            out["username"] = sub["user"]
        return out, res

    def detail(self, sub, with_user):
        out, res = self.row(sub, with_user)
        out["source"] = sub["source"]
        if sub["kind"] == "public":
            out["results"] = res
        else:
            out["results"] = [{"test": r["test"], "verdict": r["verdict"], "time": r["time"]} for r in res]
        return out

    def leaderboard(self, admin):
        cutoff = self.freeze if (self.freeze and not admin) else None
        best = {}
        counts = {}
        for sub in self.subs:
            if sub["kind"] != "full" or (cutoff and sub["created_t"] >= cutoff):
                continue
            row, _ = self.row(sub)
            if row["status"] != "done":
                continue
            counts[sub["user"]] = counts.get(sub["user"], 0) + 1
            cur = best.get(sub["user"])
            if cur is None or row["relative_total"] > cur["relative_total"]:
                best[sub["user"]] = dict(row, created_t=sub["created_t"])
        # rivalrook keeps improving a little, so rank arrows show up while you watch
        if "rivalrook" in best and not cutoff:
            bump = int((time.time() - T0) / 20) * 900_000
            best["rivalrook"]["relative_total"] += bump
        rows = sorted(best.items(), key=lambda kv: (-kv[1]["relative_total"], kv[1]["created_t"]))
        return {"frozen": bool(cutoff), "rows": [
            {"rank": i + 1, "username": u, "relative_total": r["relative_total"], "raw_total": r["raw_total"],
             "tests_ac": r["tests_ac"], "submitted": r["created"], "submissions": counts[u]}
            for i, (u, r) in enumerate(rows)]}


STARTER_FAKE = '''"""Solver by %s."""
import sys

%s
def solve(text):
    # greedy: play the best hand each turn
    lines = text.split("\\n")
    moves = ["play 0 1 2 3 4"]
    return moves


def main():
    moves = solve(sys.stdin.read())
    print(len(moves))
    for m in moves:
        print(m)


if __name__ == "__main__":
    main()
'''


# ---- online rooms (platform/API.md, "Online multiplayer") ----------------------------------
# A simplified, in-memory copy of prplatform/rooms.py: the same endpoints and room views, and
# the same game authority (poker_run.multiplayer, imported from the repository). Not here: rate
# limits, idle expiry and removing away players (rooms live until the mock restarts).

ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BOT_NAMES = {"easy": "Easy bot", "normal": "Normal bot", "hard": "Hard bot"}


def _mp():
    import sys
    root = os.path.dirname(os.path.dirname(HERE))
    if root not in sys.path:
        sys.path.append(root)
    from poker_run import multiplayer, instance, rules, sim
    return multiplayer, instance, rules, sim


class Rooms:
    def __init__(self):
        self.rooms = {}      # code -> room dict
        self.matches = []    # saved results
        self.seen = {}       # (code, username) -> time of last poll

    def new_code(self):
        while True:
            code = "".join(random.choice(ROOM_ALPHABET) for _ in range(5))
            if code not in self.rooms:
                return code

    def create(self, user, mode, rules, seed, maxp, bots):
        code = self.new_code()
        now = time.time()
        room = {"code": code, "host": user, "mode": mode, "rules": rules, "seed": seed, "max": maxp,
                "status": "lobby", "version": 1, "created": now, "started": None, "finished": None,
                "seats": [{"user": user, "bot": None, "ready": True, "status": "in"}]
                + [{"user": None, "bot": b, "ready": True, "status": "in"} for b in bots],
                "kicked": set(), "match": None, "match_id": None, "rematch": None}
        self.rooms[code] = room
        return room

    @staticmethod
    def name(room, k):
        s = room["seats"][k]
        if s["user"]:
            return s["user"]
        same = [i for i, x in enumerate(room["seats"]) if not x["user"] and x["bot"] == s["bot"]]
        return BOT_NAMES[s["bot"]] + (f" {same.index(k) + 1}" if len(same) > 1 else "")

    def bump(self, room):
        room["version"] += 1

    def start(self, room):
        mp, inst, _, _ = _mp()
        players = [{"name": self.name(room, k), "bot": s["bot"]} for k, s in enumerate(room["seats"])]
        room["match"] = mp.Match(inst.generate(room["seed"]), room["mode"], room["rules"], players)
        room["status"], room["started"] = "playing", time.time()
        self.check_over(room)

    def check_over(self, room):
        m = room["match"]
        if room["status"] != "playing" or not m.over:
            return
        room["status"], room["finished"] = "finished", time.time()
        place = {r["seat"]: r["place"] for r in m.standings()}
        mid = len(self.matches) + 1
        self.matches.append({"id": mid, "code": room["code"], "mode": room["mode"], "rules": room["rules"],
                             "seed": room["seed"], "started": iso(room["started"]), "finished": iso(room["finished"]),
                             "ended": "done", "players": [
                                 {"seat": p.seat, "user": room["seats"][p.seat]["user"], "name": p.name, "bot": p.bot,
                                  "total": p.game.total, "cleared": p.game.cleared, "place": place[p.seat],
                                  "left": p.left, "moves": list(p.moves)} for p in m.players]})
        room["match_id"] = mid

    def view(self, room, user):
        me = next((k for k, s in enumerate(room["seats"]) if s["user"] == user), None)
        out = {"code": room["code"], "mode": room["mode"], "rules": room["rules"], "seed": room["seed"],
               "max_players": room["max"], "status": room["status"], "version": room["version"],
               "created": iso(room["created"]), "started": iso(room["started"]) if room["started"] else None,
               "finished": iso(room["finished"]) if room["finished"] else None, "rematch": room["rematch"],
               "match_id": room["match_id"], "you": me, "turn": None, "shop_order": None, "owners": {},
               "ended": "done" if room["status"] == "finished" else None, "server_time": iso(time.time()),
               "expires_in": 1800, "host": next((k for k, s in enumerate(room["seats"]) if s["user"] == room["host"]), None)}
        online = lambda s: bool(s["bot"]) or time.time() - self.seen.get((room["code"], s["user"]), 0) < 30
        if room["status"] == "lobby":
            out["players"] = [{"seat": k, "name": self.name(room, k), "bot": s["bot"], "host": s["user"] == room["host"],
                               "ready": s["ready"], "status": s["status"], "you": k == me, "online": online(s)}
                              for k, s in enumerate(room["seats"])]
            return out
        m = room["match"]
        fin = room["status"] == "finished"
        out["turn"] = None if fin else m.turn
        if out["turn"] and m.mode == "draft" and out["turn"]["stage"] == "shop":
            out["shop_order"] = m.shop_order(out["turn"]["round"])
        out["owners"] = m.owners()
        mine = m.players[me] if me is not None else None
        reveal = m.inst.rounds if m.mode != "race" or fin or (mine and mine.out) else \
            (sum(1 for r in mine.rounds if r["cleared"] is not None) if mine else 0)
        place = {r["seat"]: r["place"] for r in m.standings()}
        players = []
        for p in m.players:
            s = room["seats"][p.seat]
            shown = len(p.rounds) if p.seat == me else min(len(p.rounds), reveal)
            hidden = shown < len(p.rounds)
            recs = [{"score": r["score"], "cleared": r["cleared"], "total": r["total"], "plays": r["plays"],
                     "discards": r["discards"], "buys": [x[4:] for x in r["shop"] if x.startswith("buy ")],
                     "sells": [x[5:] for x in r["shop"] if x.startswith("sell ")], "shop_moves": len(r["shop"])}
                    for r in p.rounds[:shown]]
            row = {"seat": p.seat, "name": p.name, "bot": p.bot, "host": s["user"] == room["host"],
                   "status": "left" if p.left and s["status"] == "in" else s["status"], "you": p.seat == me,
                   "online": online(s), "idle": 0, "removable": False,
                   "total": sum(r["score"] for r in recs) if hidden else p.game.total,
                   "cleared": sum(1 for r in recs if r["cleared"]) if hidden else p.game.cleared,
                   "rounds": recs, "hidden": hidden, "round": p.game.round, "phase": p.game.phase,
                   "over": p.game.over, "moves_made": len(p.moves),
                   "mods": None if hidden else [n for n, _ in p.game.mods], "place": place[p.seat] if fin else None}
            if p.seat == me or fin:
                row["moves"] = list(p.moves)
            players.append(row)
        out["players"] = players
        return out

    def match_json(self, mt, user, moves=False):
        you = next((p["seat"] for p in mt["players"] if p["user"] == user), None)
        return {**{k: mt[k] for k in ("id", "code", "mode", "rules", "seed", "started", "finished", "ended")}, "you": you,
                "players": [dict({k: p[k] for k in ("seat", "name", "bot", "total", "cleared", "place", "left")},
                                 you=p["seat"] == you, **({"moves": p["moves"]} if moves else {}))
                            for p in mt["players"]]}

    def api(self, h, method, path, user):
        """Returns True if it answered the request."""
        mp, inst, rules_mod, sim = _mp()
        if method == "GET" and path == "/api/matches":
            mine = [m for m in reversed(self.matches) if any(p["user"] == user for p in m["players"])]
            wins = sum(1 for m in mine for p in m["players"] if p["user"] == user and p["place"] == 1)
            h.send_json(200, {"record": {"played": len(mine), "wins": wins}, "matches": [self.match_json(m, user) for m in mine[:50]]})
            return True
        m_ = re.fullmatch(r"/api/matches/(\d+)", path)
        if method == "GET" and m_:
            mt = next((m for m in self.matches if m["id"] == int(m_.group(1))), None)
            if not mt or not any(p["user"] == user for p in mt["players"]):
                h.err(404, "match not found")
            else:
                h.send_json(200, self.match_json(mt, user, True))
            return True
        if method == "GET" and path == "/api/rooms":
            rows = []
            for r in self.rooms.values():
                k = next((k for k, s in enumerate(r["seats"]) if s["user"] == user and s["status"] == "in"), None)
                if k is not None and r["status"] != "finished":
                    rows.append({"code": r["code"], "mode": r["mode"], "rules": r["rules"], "status": r["status"],
                                 "players": len(r["seats"]), "max_players": r["max"], "you": k, "created": iso(r["created"])})
            h.send_json(200, rows)
            return True
        b = h.body() if method == "POST" else {}
        if not isinstance(b, dict):
            h.err(400, "invalid JSON")
            return True
        if method == "POST" and path == "/api/rooms":
            mode, rules, seed = b.get("mode", "race"), b.get("rules", "classic"), b.get("seed")
            maxp, bots = b.get("max_players", 2), b.get("bots") or []
            if mode not in mp.MODES or rules not in rules_mod.RULESETS or maxp not in (2, 3, 4) \
                    or not isinstance(bots, list) or len(bots) > maxp - 1 or any(x not in BOT_NAMES for x in bots) \
                    or not (seed is None or (isinstance(seed, int) and 0 <= seed <= 0xFFFFFFFF)):
                h.err(400, "bad room settings")
                return True
            room = self.create(user, mode, rules, seed if seed is not None else random.randint(1, 1_000_000), maxp, bots)
            h.send_json(201, self.view(room, user))
            return True
        m_ = re.fullmatch(r"/api/rooms/(\w+)(?:/(\w+))?", path)
        if not m_:
            return False
        code, what = m_.group(1).upper(), m_.group(2)
        room = self.rooms.get(code)
        if room is None:
            h.err(404, "room not found (it may have expired)")
            return True
        if user in room["kicked"]:
            h.err(403, "the host removed you from this room")
            return True
        seats = room["seats"]
        me = next((k for k, s in enumerate(seats) if s["user"] == user), None)
        host = room["host"] == user
        lobby = room["status"] == "lobby"
        if method == "GET" and what is None:
            if me is not None:
                self.seen[(code, user)] = time.time()
            h.send_json(200, self.view(room, user))
            return True
        if method != "POST":
            return False
        fail = None
        if what == "join":
            if me is None:
                if not lobby:
                    fail = (409, "the game has already started")
                elif len(seats) >= room["max"]:
                    fail = (409, "the room is full")
                else:
                    seats.append({"user": user, "bot": None, "ready": False, "status": "in"})
        elif what == "ready":
            if me is None or not lobby:
                fail = (409, "not in this lobby")
            else:
                seats[me]["ready"] = bool(b.get("ready", True))
        elif what == "settings":
            if not host or not lobby:
                fail = (403, "only the host can do that, in the lobby")
            else:
                for k in ("mode", "rules"):
                    if b.get(k) is not None:
                        room[k] = b[k]
                if b.get("random_seed"):
                    room["seed"] = random.randint(1, 1_000_000)
                elif isinstance(b.get("seed"), int):
                    room["seed"] = b["seed"]
                if b.get("max_players") in (2, 3, 4):
                    room["max"] = b["max_players"]
                if isinstance(b.get("bots"), list):
                    room["seats"] = seats = [s for s in seats if s["user"]] + \
                        [{"user": None, "bot": x, "ready": True, "status": "in"} for x in b["bots"] if x in BOT_NAMES]
                if len(seats) > room["max"]:
                    fail = (409, "the players don't fit in the seats")
                    room["seats"] = seats[:room["max"]]
                for s in room["seats"]:
                    if s["user"] and s["user"] != user and any(b.get(k) is not None for k in ("mode", "rules", "seed", "random_seed")):
                        s["ready"] = False
        elif what == "kick":
            k = b.get("seat")
            if not host or not lobby or not isinstance(k, int) or not 0 <= k < len(seats) or seats[k]["user"] == user:
                fail = (403, "only the host can kick, in the lobby")
            else:
                if seats[k]["user"]:
                    room["kicked"].add(seats[k]["user"])
                del seats[k]
        elif what == "start":
            if not host or not lobby:
                fail = (403, "only the host can start")
            elif len(seats) < 2:
                fail = (409, "a game needs at least 2 players (people or bots)")
            elif any(s["user"] and s["user"] != user and not s["ready"] for s in seats):
                fail = (409, "waiting for everyone to be ready")
            else:
                self.start(room)
        elif what == "move":
            line = " ".join(str(b.get("move", "")).split())
            if me is None or room["status"] != "playing":
                fail = (409, "the game is not running")
            else:
                try:
                    room["match"].apply(line, me)
                except (sim.IllegalMove, ValueError, IndexError) as e:
                    fail = (400, f"{line}: {e}")
                self.check_over(room)
        elif what == "leave":
            if me is None:
                fail = (403, "you are not in this room")
            elif lobby:
                del seats[me]
                if host:
                    nxt = next((s["user"] for s in seats if s["user"]), None)
                    if nxt is None:
                        del self.rooms[code]
                        h.send_json(200, {"deleted": True})
                        return True
                    room["host"] = nxt
            else:
                if room["status"] == "playing" and not room["match"].players[me].out:
                    room["match"].leave(me)
                seats[me]["status"] = "left"
                self.check_over(room)
        elif what == "remove":
            fail = (409, "the mock server never marks anyone as away")
        elif what == "rematch":
            if room["status"] != "finished" or me is None:
                fail = (409, "the game is not over yet")
            else:
                if not room["rematch"]:
                    new = self.create(user, room["mode"], room["rules"],
                                      room["seed"] if b.get("same_seed") else random.randint(1, 1_000_000),
                                      room["max"], [s["bot"] for s in seats if s["bot"]])
                    room["rematch"] = new["code"]
                    self.bump(room)
                h.send_json(200, {"code": room["rematch"]})
                return True
        else:
            return False
        if fail:
            h.err(*fail)
            return True
        self.bump(room)
        h.send_json(200, self.view(room, user))
        return True


class Handler(SimpleHTTPRequestHandler):
    state = None
    server_version = "PokerRunMock/1.0"

    def log_message(self, fmt, *args):
        if not self.state.args.quiet:
            super().log_message(fmt, *args)

    # ---- helpers
    def send_json(self, status, obj, extra_headers=()):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(status)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def err(self, status, msg):
        self.send_json(status, {"error": msg})

    def user(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        tok = c.get("pr_session")
        if not tok:
            return None
        name = self.state.sessions.get(tok.value)
        return (name, self.state.users[name]) if name in self.state.users else None

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            return "toobig"
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return None

    def login_cookie(self, name):
        tok = secrets.token_urlsafe(24)
        self.state.sessions[tok] = name
        return ("Set-Cookie", "pr_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (tok, 14 * 86400))

    # ---- routing
    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/rooms/"):
            self.wait_for_room(path)
        if path.startswith("/api/"):
            with LOCK:
                return self.api("GET", path)
        return super().do_GET()

    def wait_for_room(self, path):
        """GET /api/rooms/CODE?since=V&wait=S: wait (without the lock) until the version changes."""
        q = dict(p.split("=", 1) for p in (urlsplit(self.path).query or "").split("&") if "=" in p)
        try:
            since, wait = int(q.get("since", "")), min(25.0, float(q.get("wait", "0")))
        except ValueError:
            return
        code = path.rsplit("/", 1)[-1].upper()
        t_end = time.time() + wait
        while time.time() < t_end:
            with LOCK:
                room = self.state.rooms.rooms.get(code)
                if room is None or room["version"] != since:
                    return
            time.sleep(0.25)

    def do_HEAD(self):
        return super().do_HEAD()

    def do_POST(self):
        path = urlsplit(self.path).path
        if not path.startswith("/api/"):
            return self.err(405, "method not allowed")
        if self.headers.get("X-Requested-With") != "fetch":
            return self.err(403, "missing X-Requested-With header")
        with LOCK:
            return self.api("POST", path)

    def api(self, method, path):
        st = self.state
        time.sleep(st.args.latency)
        u = self.user()
        if method == "GET" and path == "/api/contest":
            now = time.time()
            open_ = (st.start is None or now >= st.start) and (st.end is None or now < st.end)
            return self.send_json(200, {
                "name": "Poker Run", "start": iso(st.start) if st.start else None, "end": iso(st.end) if st.end else None,
                "freeze": iso(st.freeze) if st.freeze else None, "now": iso(now), "open": open_,
                "public_seeds": st.public_seeds, "hidden_count": st.hidden, "time_limit": 2.0, "full_per_hour": 6,
                "invite_required": bool(st.args.invite)})
        if method == "GET" and path == "/api/me":
            if not u:
                return self.err(401, "not logged in")
            return self.send_json(200, {"username": u[0], "is_admin": u[1]["is_admin"]})
        if method == "POST" and path == "/api/register":
            b = self.body()
            if not isinstance(b, dict):
                return self.err(400, "invalid JSON")
            name, pw, inv = str(b.get("username", "")), str(b.get("password", "")), str(b.get("invite", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", name):
                return self.err(400, "username must be 3-20 characters: letters, digits, _ or -")
            if len(pw) < 8:
                return self.err(400, "password must be at least 8 characters")
            if st.args.invite and inv != st.args.invite:
                return self.err(403, "wrong invite code")
            if name.lower() in (n.lower() for n in st.users):
                return self.err(409, "that username is taken")
            st.add_user(name, pw)
            return self.send_json(201, {"username": name, "is_admin": False}, [self.login_cookie(name)])
        if method == "POST" and path == "/api/login":
            b = self.body()
            if not isinstance(b, dict):
                return self.err(400, "invalid JSON")
            name, pw = str(b.get("username", "")), str(b.get("password", ""))
            rec = st.users.get(name)
            if not rec or rec["pw"] != pw:
                return self.err(401, "wrong username or password")
            return self.send_json(200, {"username": name, "is_admin": rec["is_admin"]}, [self.login_cookie(name)])
        if method == "POST" and path == "/api/logout":
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            if c.get("pr_session"):
                st.sessions.pop(c["pr_session"].value, None)
            return self.send_json(204, None, [("Set-Cookie", "pr_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")])
        if method == "GET" and path == "/api/leaderboard":
            return self.send_json(200, st.leaderboard(bool(u and u[1]["is_admin"])))

        # everything below needs a session
        if path.startswith(("/api/submissions", "/api/admin/", "/api/rooms", "/api/matches")):
            if not u:
                return self.err(401, "not logged in")
        if path.startswith(("/api/rooms", "/api/matches")) and st.rooms.api(self, method, path, u[0]):
            return
        name, rec = u if u else (None, None)
        if method == "POST" and path == "/api/submissions":
            b = self.body()
            if b == "toobig":
                return self.err(413, "source is larger than 256 KB")
            if not isinstance(b, dict) or b.get("kind") not in ("public", "full") or not isinstance(b.get("source"), str):
                return self.err(400, "send {kind: 'public'|'full', source: '...'}")
            if len(b["source"].encode()) > 256 * 1024:
                return self.err(413, "source is larger than 256 KB")
            if not b["source"].strip():
                return self.err(400, "source is empty")
            now = time.time()
            open_ = (st.start is None or now >= st.start) and (st.end is None or now < st.end)
            if b["kind"] == "full" and not open_ and not rec["is_admin"]:
                return self.err(403, "the contest is not open for full submissions")
            mine = [s for s in st.subs if s["user"] == name]
            if any(st.timeline(s)[0] in ("queued", "running") for s in mine):
                return self.err(429, "you already have a submission in the queue; wait for it to finish")
            recent = [s for s in mine if s["kind"] == "full" and s["created_t"] > now - 3600 and not s["forced_done"]]
            if b["kind"] == "full" and len(recent) >= 6:
                return self.err(429, "at most 6 full submissions per hour; try again later")
            sub = st.new_sub(name, b["kind"], b["source"])
            return self.send_json(202, {"id": sub["id"]})
        if method == "GET" and path == "/api/submissions":
            rows = [st.row(s)[0] for s in st.subs if s["user"] == name]
            return self.send_json(200, sorted(rows, key=lambda r: -r["id"]))
        if method == "GET" and path.startswith("/api/submissions/"):
            try:
                sid = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.err(404, "no such submission")
            sub = next((s for s in st.subs if s["id"] == sid), None)
            if not sub or (sub["user"] != name and not rec["is_admin"]):
                return self.err(404, "no such submission")
            return self.send_json(200, st.detail(sub, rec["is_admin"]))
        if path.startswith("/api/admin/"):
            if not rec["is_admin"]:
                return self.err(403, "admins only")
            if method == "GET" and path == "/api/admin/submissions":
                rows = [st.row(s, True)[0] for s in st.subs]
                return self.send_json(200, sorted(rows, key=lambda r: -r["id"]))
            if method == "POST" and path.startswith("/api/admin/rejudge/"):
                try:
                    sid = int(path.rsplit("/", 1)[1])
                except ValueError:
                    return self.err(404, "no such submission")
                sub = next((s for s in st.subs if s["id"] == sid), None)
                if not sub:
                    return self.err(404, "no such submission")
                sub["forced_done"] = False
                sub["created_t"] = time.time()
                sub["rejudged"] = sub.get("rejudged", 0) + 1
                return self.send_json(200, {"id": sid, "status": "queued"})
        return self.err(404, "no such endpoint")

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    ap = argparse.ArgumentParser(description="Mock Poker Run API + static server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--dir", default=os.path.join(HERE, "dist"))
    ap.add_argument("--invite", default="", help="require this invite code to register")
    ap.add_argument("--frozen", action="store_true", help="leaderboard frozen 10 minutes ago")
    ap.add_argument("--closed", action="store_true", help="contest ended an hour ago")
    ap.add_argument("--upcoming", action="store_true", help="contest starts in about 2 hours")
    ap.add_argument("--no-deadline", action="store_true", help="no start or end time")
    ap.add_argument("--speed", type=float, default=1.0, help="judge speed multiplier")
    ap.add_argument("--latency", type=float, default=0.0, help="seconds of delay per API call")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if not os.path.isdir(args.dir):
        raise SystemExit("%s does not exist: run platform/site/build.py first" % args.dir)
    Handler.state = State(args)

    def factory(*a, **kw):
        return Handler(*a, directory=args.dir, **kw)

    srv = ThreadingHTTPServer((args.host, args.port), factory)
    print("Poker Run mock on http://%s:%d/  (admin/adminpass, demo/demopass%s)" % (
        args.host, args.port, ", invite %r" % args.invite if args.invite else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

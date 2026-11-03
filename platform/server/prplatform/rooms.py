"""Online multiplayer: rooms, live turns and saved matches (platform/API.md, "Online multiplayer").

A signed-in user creates a room (mode race | draft | rounds, ruleset, seed,
2-4 seats, optional bot seats) and shares its code; others join by code. The
server is the authority: it keeps every room's move log in SQLite and checks
each move with poker_run.multiplayer.Match (the Python port of
web/multiplayer.js, itself built on the solo simulator), so every player's
move list stays a legal solo game for the seed and the draft rules, turn order
and shop visit order hold. Bots move on the server (poker_run.bot).

Live updates are long polls: GET /api/rooms/{code}?since=V&wait=25 answers at
once when the room's version is not V, otherwise waits (async, no thread held)
until a change or the timeout. Moves come in on ordinary POSTs, each in one
BEGIN IMMEDIATE transaction, so two players acting at the same moment are
applied one after the other against the latest state.

Presence (online/away) is kept in memory: it is only a hint, reset when the
process restarts, and never written to the database. The api runs as ONE
uvicorn process (see deploy/); the in-memory notifier, presence and rate
limits assume that, and a fallback re-read every few seconds keeps long polls
correct even if a change is missed.
"""

import asyncio
import re
import secrets
import threading
import time
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

from poker_run.instance import generate
from poker_run.multiplayer import LEAVE, MODES, Match
from poker_run.rules import RULESETS
from poker_run.sim import IllegalMove

from . import auth, db

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I
CODE_LEN = 5
CODE_RE = re.compile(f"^[{CODE_ALPHABET}]{{{CODE_LEN}}}$")
MOVE_RE = re.compile(r"^[a-z]{3,10}( [A-Za-z0-9_]{1,20}){0,5}$")
BOT_LEVELS = ("easy", "normal", "hard")
BOT_NAMES = {"easy": "Easy bot", "normal": "Normal bot", "hard": "Hard bot"}


class Limits:
    """Tunables (module-level so tests can change them)."""
    lobby_idle = 30 * 60          # a lobby with no activity for this long is deleted
    game_idle = 2 * 3600          # a game with no move for this long ends ("expired"); results are saved
    finished_keep = 30 * 60       # a finished room stays readable (results, rematch) this long
    online_seconds = 30           # seen (polled) within this many seconds = online
    afk_seconds = 90              # away and no move for this long: other players may remove them
    long_poll_max = 25.0          # longest wait of one GET ?wait=
    recheck_seconds = 5.0         # a waiting long poll re-reads the room at least this often
    create_per_window = 10        # rooms a user may create per window
    create_window = 600
    moves_per_minute = 300        # moves per user per minute
    actions_per_minute = 120      # joins, ready, kicks, leaves, settings per user per minute
    open_rooms_per_user = 3       # lobbies + games a user may be seated in at once
    max_rooms = 500               # lobbies + games on the whole server
    sweep_every = 60              # seconds between expiry sweeps


LIMITS = Limits()
STATE = {}   # database path -> the in-memory parts of its router (for tests and debugging)


# ---- in-memory helpers ---------------------------------------------------------------

class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._hits = {}

    def hit(self, key, limit, window):
        """Record a hit; False if the key is over `limit` hits in the last `window` seconds."""
        t = time.monotonic()
        with self._lock:
            hits = [h for h in self._hits.get(key, ()) if t - h < window]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(t)
            self._hits[key] = hits
            if len(self._hits) > 50_000:
                self._hits.clear()
            return True

    def clear(self):
        with self._lock:
            self._hits.clear()


class Presence:
    """(room id, user id) -> wall-clock time of the user's last poll of that room."""

    def __init__(self):
        self._lock = threading.Lock()
        self.seen = {}

    def touch(self, room_id, user_id):
        """Mark seen; True if the user was away before (others should hear about it)."""
        t = time.time()
        with self._lock:
            prev = self.seen.get((room_id, user_id))
            self.seen[(room_id, user_id)] = t
            if len(self.seen) > 100_000:
                self.seen = {k: v for k, v in self.seen.items() if t - v < 3600}
        return prev is None or t - prev > LIMITS.online_seconds

    def last(self, room_id, user_id):
        with self._lock:
            return self.seen.get((room_id, user_id))

    def online(self, room_id, user_id):
        t = self.last(room_id, user_id)
        return t is not None and time.time() - t <= LIMITS.online_seconds


class Notifier:
    """Wakes the long polls of a room. fire() may be called from any thread."""

    def __init__(self):
        self.loop = None
        self.events = {}

    def _set(self, room_id):
        ev = self.events.pop(room_id, None)
        if ev is not None:
            ev.set()

    def fire(self, room_id):
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._set, room_id)
        except RuntimeError:  # loop shutting down
            pass

    async def wait(self, room_id, timeout):
        self.loop = asyncio.get_running_loop()
        ev = self.events.get(room_id)
        if ev is None:
            ev = self.events[room_id] = asyncio.Event()
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            pass


class MatchCache:
    """room id -> (version, Match) for the latest known version. Matches in the cache are
    never mutated: a move works on a clone, which replaces the entry after its commit."""

    def __init__(self, size=2000):
        self._lock = threading.Lock()
        self._d = {}
        self.size = size

    def get(self, room_id, version):
        with self._lock:
            hit = self._d.get(room_id)
        return hit[1] if hit and hit[0] == version else None

    def put(self, room_id, version, match):
        with self._lock:
            old = self._d.get(room_id)
            if old and old[0] >= version:
                return
            if len(self._d) >= self.size:
                self._d.pop(next(iter(self._d)))
            self._d[room_id] = (version, match)

    def drop(self, room_id):
        with self._lock:
            self._d.pop(room_id, None)


# ---- request bodies ------------------------------------------------------------------

class Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CreateBody(Body):
    mode: str = "race"
    rules: str = "classic"
    seed: Optional[int] = None
    max_players: int = 2
    bots: List[str] = []


class SettingsBody(Body):
    mode: Optional[str] = None
    rules: Optional[str] = None
    seed: Optional[int] = None
    random_seed: bool = False
    max_players: Optional[int] = None
    bots: Optional[List[str]] = None


class ReadyBody(Body):
    ready: bool = True


class SeatBody(Body):
    seat: int = -1


class MoveBody(Body):
    move: str = ""


class RematchBody(Body):
    same_seed: bool = False


# ---- the router ----------------------------------------------------------------------

def make_router(settings, ensure_db, get_conn, require_user, ApiError):
    router = APIRouter()
    limiter = RateLimiter()
    presence = Presence()
    notifier = Notifier()
    cache = MatchCache()
    sweep_at = [0.0]
    STATE[settings.db] = {"limiter": limiter, "presence": presence, "notifier": notifier, "cache": cache,
                          "sweep_at": sweep_at}

    def now_iso():
        return db.now_iso()

    def ago_iso(seconds):
        return db.iso(db.now() - timedelta(seconds=seconds))

    def seconds_since(text):
        return max(0.0, (db.now() - db.parse_iso(text)).total_seconds())

    def limit(user, kind):
        n, window = {"create": (LIMITS.create_per_window, LIMITS.create_window),
                     "move": (LIMITS.moves_per_minute, 60),
                     "action": (LIMITS.actions_per_minute, 60)}[kind]
        if not limiter.hit((user["id"], kind), n, window):
            raise ApiError(429, "too many requests; slow down a little" if kind != "create"
                           else f"at most {n} new rooms per {window // 60} minutes")

    def check_code(code):
        code = (code or "").strip().upper()
        if not CODE_RE.match(code):
            raise ApiError(404, "room not found")
        return code

    def new_code(conn):
        for _ in range(50):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
            if not conn.execute("SELECT 1 FROM mp_rooms WHERE code = ?", (code,)).fetchone():
                return code
        raise ApiError(503, "couldn't find a free room code; try again")

    def check_settings(mode, rules, seed, max_players, bots):
        if mode not in MODES:
            raise ApiError(400, f"mode must be one of {', '.join(MODES)}")
        if rules not in RULESETS:
            raise ApiError(400, f"rules must be one of {', '.join(RULESETS)}")
        if seed is not None and not (0 <= seed <= 0xFFFFFFFF):
            raise ApiError(400, "seed must be a whole number from 0 to 4294967295")
        if not (2 <= max_players <= 4):
            raise ApiError(400, "max_players must be 2, 3 or 4")
        if len(bots) > max_players - 1 or any(b not in BOT_LEVELS for b in bots):
            raise ApiError(400, f"bots: up to {max_players - 1} of {', '.join(BOT_LEVELS)}")

    def random_seed():
        return secrets.randbelow(1_000_000) + 1

    # ---- loading

    def room_by_code(conn, code):
        r = conn.execute("SELECT * FROM mp_rooms WHERE code = ?", (code,)).fetchone()
        if r is None:
            raise ApiError(404, "room not found (it may have expired)")
        return r

    def seats_of(conn, room_id):
        return conn.execute("SELECT s.*, u.username FROM mp_seats s LEFT JOIN users u ON u.id = s.user_id "
                            "WHERE s.room_id = ? ORDER BY s.seat", (room_id,)).fetchall()

    def seat_name(s, seats):
        if s["user_id"] is not None:
            return s["username"] or "?"
        base = BOT_NAMES.get(s["bot"], "Bot")
        same = [x for x in seats if x["user_id"] is None and x["bot"] == s["bot"]]
        if len(same) > 1:
            return f"{base} {[x['seat'] for x in same].index(s['seat']) + 1}"
        return base

    def players_setup(seats):
        return [{"name": seat_name(s, seats), "bot": s["bot"]} for s in seats]

    def match_for(conn, room, seats=None):
        m = cache.get(room["id"], room["version"])
        if m is not None:
            return m
        seats = seats if seats is not None else seats_of(conn, room["id"])
        log = [(r["seat"], r["move"]) for r in
               conn.execute("SELECT seat, move FROM mp_moves WHERE room_id = ? ORDER BY n", (room["id"],))]
        m = Match(generate(room["seed"]), room["mode"], room["rules"], players_setup(seats), log)
        cache.put(room["id"], room["version"], m)
        return m

    def bump(conn, room_id, **cols):
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(f"UPDATE mp_rooms SET version = version + 1, active = ?{', ' + sets if sets else ''} "
                     "WHERE id = ?", (now_iso(), *cols.values(), room_id))
        return conn.execute("SELECT version FROM mp_rooms WHERE id = ?", (room_id,)).fetchone()[0]

    def store_moves(conn, room_id, m, n0):
        t = now_iso()
        conn.executemany("INSERT INTO mp_moves(room_id, n, seat, move, created) VALUES (?, ?, ?, ?, ?)",
                         [(room_id, n0 + i, seat, move, t) for i, (seat, move, _, _) in enumerate(m.history[n0:])])

    def finalize(conn, room, m, seats, ended):
        """Save the match (results for every player's account) and mark the room finished."""
        t = now_iso()
        mid = conn.execute("INSERT INTO mp_matches(code, mode, rules, seed, players, started, finished, ended) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (room["code"], room["mode"], room["rules"], room["seed"], len(seats),
                            room["started"] or t, t, ended)).lastrowid
        place = {r["seat"]: r["place"] for r in m.standings()}
        for s in seats:
            p = m.players[s["seat"]]
            conn.execute("INSERT INTO mp_match_players(match_id, seat, user_id, name, bot, total, cleared, place, "
                         "left_early, moves) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (mid, s["seat"], s["user_id"], p.name, p.bot, p.game.total, p.game.cleared,
                          place[s["seat"]], int(p.left), "\n".join(p.moves)))
        return bump(conn, room["id"], status="finished", finished=t, match_id=mid)

    def expire_room(conn, room):
        """Apply idle expiry to one room. Returns True if the room is gone."""
        st, active = room["status"], room["active"]
        if st == "lobby" and active < ago_iso(LIMITS.lobby_idle):
            conn.execute("DELETE FROM mp_rooms WHERE id = ?", (room["id"],))
            cache.drop(room["id"])
            notifier.fire(room["id"])
            return True
        if st == "playing" and active < ago_iso(LIMITS.game_idle):
            seats = seats_of(conn, room["id"])
            m = match_for(conn, room, seats)
            v = finalize(conn, room, m, seats, "expired")
            cache.put(room["id"], v, m)
            notifier.fire(room["id"])
            return False
        if st == "finished" and (room["finished"] or active) < ago_iso(LIMITS.finished_keep):
            conn.execute("DELETE FROM mp_rooms WHERE id = ?", (room["id"],))
            cache.drop(room["id"])
            return True
        return False

    def sweep(conn, force=False):
        t = time.monotonic()
        if not force and t - sweep_at[0] < LIMITS.sweep_every:
            return
        sweep_at[0] = t
        with db.transaction(conn, immediate=True):
            rows = conn.execute(
                "SELECT * FROM mp_rooms WHERE (status = 'lobby' AND active < ?) OR (status = 'playing' AND active < ?) "
                "OR (status = 'finished' AND COALESCE(finished, active) < ?) LIMIT 200",
                (ago_iso(LIMITS.lobby_idle), ago_iso(LIMITS.game_idle), ago_iso(LIMITS.finished_keep))).fetchall()
            for r in rows:
                expire_room(conn, r)

    def is_expired(room):
        st = room["status"]
        if st == "lobby":
            return room["active"] < ago_iso(LIMITS.lobby_idle)
        if st == "playing":
            return room["active"] < ago_iso(LIMITS.game_idle)
        return (room["finished"] or room["active"]) < ago_iso(LIMITS.finished_keep)

    def load(conn, code):
        """The room (after its own expiry check, inside a write transaction); 404 if gone."""
        room = room_by_code(conn, code)
        if is_expired(room):
            if expire_room(conn, room):
                raise ApiError(404, "room not found (it may have expired)")
            room = room_by_code(conn, code)
        return room

    def my_seat(seats, user):
        return next((s for s in seats if s["user_id"] == user["id"]), None)

    def open_rooms(conn, user):
        return conn.execute("SELECT COUNT(*) FROM mp_seats s JOIN mp_rooms r ON r.id = s.room_id "
                            "WHERE s.user_id = ? AND s.status = 'in' AND r.status IN ('lobby', 'playing')",
                            (user["id"],)).fetchone()[0]

    # ---- views

    def round_view(rec):
        return {"score": rec["score"], "cleared": rec["cleared"], "total": rec["total"],
                "plays": [dict(p) for p in rec["plays"]], "discards": rec["discards"],
                "buys": [mv[4:] for mv in rec["shop"] if mv.startswith("buy ")],
                "sells": [mv[5:] for mv in rec["shop"] if mv.startswith("sell ")],
                "shop_moves": len(rec["shop"])}

    def view(conn, room, user, seats=None):
        seats = seats if seats is not None else seats_of(conn, room["id"])
        me = my_seat(seats, user)
        you = me["seat"] if me is not None else None
        out = {"code": room["code"], "mode": room["mode"], "rules": room["rules"], "seed": room["seed"],
               "max_players": room["max_players"], "status": room["status"], "version": room["version"],
               "created": room["created"], "started": room["started"], "finished": room["finished"],
               "rematch": room["rematch_code"], "match_id": room["match_id"], "you": you,
               "turn": None, "shop_order": None, "owners": {}, "ended": None, "server_time": now_iso()}
        idle_limit = {"lobby": LIMITS.lobby_idle, "playing": LIMITS.game_idle,
                      "finished": LIMITS.finished_keep}[room["status"]]
        since = room["finished"] if room["status"] == "finished" and room["finished"] else room["active"]
        out["expires_in"] = max(0, int(idle_limit - seconds_since(since)))
        host_seat = next((s["seat"] for s in seats if s["user_id"] == room["host_id"]), None)
        out["host"] = host_seat
        if room["status"] == "finished" and room["match_id"]:
            e = conn.execute("SELECT ended FROM mp_matches WHERE id = ?", (room["match_id"],)).fetchone()
            out["ended"] = e["ended"] if e else "done"
        players = []
        if room["status"] == "lobby":
            for s in seats:
                players.append({"seat": s["seat"], "name": seat_name(s, seats), "bot": s["bot"],
                                "host": s["seat"] == host_seat, "ready": bool(s["ready"] or s["bot"]),
                                "status": s["status"], "you": me is not None and s["seat"] == me["seat"],
                                "online": s["bot"] is not None or presence.online(room["id"], s["user_id"])})
            out["players"] = players
            return out
        m = match_for(conn, room, seats)
        finished = room["status"] == "finished"
        t = m.turn
        out["turn"] = t if not finished else None
        if t and m.mode == "draft" and t["stage"] == "shop":
            out["shop_order"] = m.shop_order(t["round"])
        out["owners"] = m.owners()
        last_move = {r["seat"]: r["t"] for r in conn.execute(
            "SELECT seat, MAX(created) AS t FROM mp_moves WHERE room_id = ? AND move != ? GROUP BY seat",
            (room["id"], LEAVE))}
        room_last = max(last_move.values()) if last_move else room["started"] or room["active"]
        mine = m.players[me["seat"]] if me is not None else None
        # race: other players' rounds show up once you have finished the same round
        if m.mode != "race" or finished or (mine is not None and mine.out):
            reveal = m.inst.rounds
        else:
            reveal = sum(1 for r in mine.rounds if r["cleared"] is not None) if mine is not None else 0
        place = {r["seat"]: r["place"] for r in m.standings()}
        can_remove = me is not None and me["status"] == "in" and not finished
        for s in seats:
            p = m.players[s["seat"]]
            g = p.game
            is_me = me is not None and s["seat"] == me["seat"]
            shown = len(p.rounds) if is_me else min(len(p.rounds), reveal)
            hidden = shown < len(p.rounds)
            recs = [round_view(r) for r in p.rounds[:shown]]
            online = p.bot is not None or presence.online(room["id"], s["user_id"])
            # how long they have kept the game waiting: since their own last move (race),
            # or since the last move of anyone while it is their turn (draft, rounds)
            if m.mode == "race":
                idle = seconds_since(last_move.get(s["seat"]) or room["started"] or room["active"])
            else:
                idle = seconds_since(room_last) if t and t["seat"] == s["seat"] else 0
            in_game = s["status"] == "in" and not p.out
            row = {"seat": s["seat"], "name": p.name, "bot": p.bot, "host": s["seat"] == host_seat,
                   "status": "left" if p.left and s["status"] == "in" else s["status"],
                   "you": is_me, "online": online, "idle": int(idle),
                   "total": sum(r["score"] for r in recs) if hidden else g.total,
                   "cleared": sum(1 for r in recs if r["cleared"]) if hidden else g.cleared,
                   "rounds": recs, "hidden": hidden,
                   "round": g.round, "phase": g.phase, "over": g.over, "moves_made": len(p.moves),
                   "mods": None if hidden else [n for n, _ in g.mods],
                   "place": place[s["seat"]] if finished else None,
                   "removable": bool(can_remove and not is_me and p.bot is None and in_game and not online
                                     and idle >= LIMITS.afk_seconds)}
            if is_me or finished:
                row["moves"] = list(p.moves)
            players.append(row)
        out["players"] = players
        return out

    # ---- endpoints

    @router.post("/api/rooms", status_code=201)
    def create_room(body: CreateBody, user=Depends(require_user), conn=Depends(get_conn)):
        check_settings(body.mode, body.rules, body.seed, body.max_players, body.bots)
        limit(user, "create")
        sweep(conn)
        seed = body.seed if body.seed is not None else random_seed()
        with db.transaction(conn, immediate=True):
            if open_rooms(conn, user) >= LIMITS.open_rooms_per_user:
                raise ApiError(429, f"you are already in {LIMITS.open_rooms_per_user} open rooms; leave one first")
            n = conn.execute("SELECT COUNT(*) FROM mp_rooms WHERE status IN ('lobby', 'playing')").fetchone()[0]
            if n >= LIMITS.max_rooms:
                raise ApiError(503, "the server has too many open rooms right now; try again later")
            code = new_code(conn)
            t = now_iso()
            rid = conn.execute("INSERT INTO mp_rooms(code, host_id, mode, rules, seed, max_players, status, version, "
                               "created, active) VALUES (?, ?, ?, ?, ?, ?, 'lobby', 1, ?, ?)",
                               (code, user["id"], body.mode, body.rules, seed, body.max_players, t, t)).lastrowid
            conn.execute("INSERT INTO mp_seats(room_id, seat, user_id, ready, joined) VALUES (?, 0, ?, 1, ?)",
                         (rid, user["id"], t))
            for i, b in enumerate(body.bots, 1):
                conn.execute("INSERT INTO mp_seats(room_id, seat, bot, ready, joined) VALUES (?, ?, ?, 1, ?)",
                             (rid, i, b, t))
            room = room_by_code(conn, code)
            presence.touch(rid, user["id"])
            return view(conn, room, user)

    @router.get("/api/rooms")
    def my_rooms(user=Depends(require_user), conn=Depends(get_conn)):
        sweep(conn)
        rows = conn.execute(
            "SELECT r.code, r.mode, r.rules, r.status, r.max_players, r.created, s.seat, "
            "(SELECT COUNT(*) FROM mp_seats x WHERE x.room_id = r.id) AS seats "
            "FROM mp_seats s JOIN mp_rooms r ON r.id = s.room_id "
            "WHERE s.user_id = ? AND s.status = 'in' AND r.status IN ('lobby', 'playing') ORDER BY r.id DESC",
            (user["id"],)).fetchall()
        return [{"code": r["code"], "mode": r["mode"], "rules": r["rules"], "status": r["status"],
                 "players": r["seats"], "max_players": r["max_players"], "you": r["seat"], "created": r["created"]}
                for r in rows]

    def poll_once(code, user_id, since):
        conn = None
        try:
            ensure_db()
            conn = db.connect(settings.db)
            sweep(conn)
            user = conn.execute("SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
            if user is None:
                raise ApiError(401, "not logged in")
            room = room_by_code(conn, code)
            if is_expired(room):
                with db.transaction(conn, immediate=True):
                    room = load(conn, code)
            if conn.execute("SELECT 1 FROM mp_kicked WHERE room_id = ? AND user_id = ?",
                            (room["id"], user_id)).fetchone():
                raise ApiError(403, "the host removed you from this room")
            seats = seats_of(conn, room["id"])
            if my_seat(seats, user) is not None and presence.touch(room["id"], user_id):
                notifier.fire(room["id"])   # others see us come online
            if since is not None and room["version"] == since:
                return room["id"], None
            return room["id"], view(conn, room, user, seats)
        finally:
            if conn is not None:
                conn.close()

    def session_user_id(request):
        ensure_db()
        conn = db.connect(settings.db)
        try:
            u = auth.session_user(conn, request.cookies.get(auth.COOKIE))
            return u["id"] if u else None
        finally:
            conn.close()

    @router.get("/api/rooms/{code}")
    async def get_room(code: str, request: Request, since: Optional[int] = None, wait: float = 0):
        uid = await run_in_threadpool(session_user_id, request)
        if uid is None:
            raise ApiError(401, "not logged in")
        code = check_code(code)
        wait = max(0.0, min(float(wait), LIMITS.long_poll_max)) if since is not None else 0.0
        deadline = time.monotonic() + wait
        while True:
            room_id, out = await run_in_threadpool(poll_once, code, uid, since)
            left = deadline - time.monotonic()
            if out is not None:
                return out
            if left <= 0 or await request.is_disconnected():
                _, out = await run_in_threadpool(poll_once, code, uid, None)
                return out
            await notifier.wait(room_id, min(left, LIMITS.recheck_seconds))

    def act(code, user, fn, kind="action"):
        """Run fn(conn, room, seats, me) in one write transaction; returns the new view."""
        code = check_code(code)
        limit(user, kind)
        conn = db.connect(settings.db)
        try:
            with db.transaction(conn, immediate=True):
                room = load(conn, code)
                seats = seats_of(conn, room["id"])
                me = my_seat(seats, user)
                result = fn(conn, room, seats, me)
                if result == "deleted":
                    out = None
                else:
                    room = room_by_code(conn, code)
                    out = view(conn, room, user)
                rid = room["id"]
        finally:
            conn.close()
        notifier.fire(rid)
        return out

    def need_seat(me):
        if me is None:
            raise ApiError(403, "you are not in this room")
        return me

    def need_lobby(room):
        if room["status"] != "lobby":
            raise ApiError(409, "the game has already started" if room["status"] == "playing" else "the game is over")

    def need_host(room, user):
        if room["host_id"] != user["id"]:
            raise ApiError(403, "only the host can do that")

    @router.post("/api/rooms/{code}/join")
    def join(code: str, user=Depends(require_user)):
        ensure_db()

        def fn(conn, room, seats, me):
            if conn.execute("SELECT 1 FROM mp_kicked WHERE room_id = ? AND user_id = ?",
                            (room["id"], user["id"])).fetchone():
                raise ApiError(403, "the host removed you from this room")
            if me is not None:
                if me["status"] != "in" and room["status"] == "lobby":
                    conn.execute("UPDATE mp_seats SET status = 'in' WHERE room_id = ? AND seat = ?",
                                 (room["id"], me["seat"]))
                presence.touch(room["id"], user["id"])
                return bump(conn, room["id"])   # resume the seat
            need_lobby(room)
            if len(seats) >= room["max_players"]:
                raise ApiError(409, "the room is full")
            if open_rooms(conn, user) >= LIMITS.open_rooms_per_user:
                raise ApiError(429, f"you are already in {LIMITS.open_rooms_per_user} open rooms; leave one first")
            used = {s["seat"] for s in seats}
            seat = min(k for k in range(4) if k not in used)
            conn.execute("INSERT INTO mp_seats(room_id, seat, user_id, ready, joined) VALUES (?, ?, ?, 0, ?)",
                         (room["id"], seat, user["id"], now_iso()))
            presence.touch(room["id"], user["id"])
            return bump(conn, room["id"])
        return act(code, user, fn)

    @router.post("/api/rooms/{code}/ready")
    def ready(code: str, body: ReadyBody, user=Depends(require_user)):
        ensure_db()

        def fn(conn, room, seats, me):
            need_seat(me)
            need_lobby(room)
            conn.execute("UPDATE mp_seats SET ready = ? WHERE room_id = ? AND seat = ?",
                         (int(body.ready), room["id"], me["seat"]))
            return bump(conn, room["id"])
        return act(code, user, fn)

    @router.post("/api/rooms/{code}/settings")
    def change_settings(code: str, body: SettingsBody, user=Depends(require_user)):
        ensure_db()

        def fn(conn, room, seats, me):
            need_host(room, user)
            need_lobby(room)
            humans = [s for s in seats if s["user_id"] is not None]
            mode = body.mode if body.mode is not None else room["mode"]
            rules = body.rules if body.rules is not None else room["rules"]
            seed = random_seed() if body.random_seed else body.seed if body.seed is not None else room["seed"]
            maxp = body.max_players if body.max_players is not None else room["max_players"]
            bots = body.bots if body.bots is not None else [s["bot"] for s in seats if s["user_id"] is None]
            check_settings(mode, rules, seed, maxp, bots)
            if len(humans) + len(bots) > maxp:
                raise ApiError(409, f"{len(humans)} players and {len(bots)} bots don't fit in {maxp} seats")
            if body.bots is not None:
                conn.execute("DELETE FROM mp_seats WHERE room_id = ? AND user_id IS NULL", (room["id"],))
                used = {s["seat"] for s in humans}
                free = [k for k in range(4) if k not in used]
                for b, k in zip(bots, free):
                    conn.execute("INSERT INTO mp_seats(room_id, seat, bot, ready, joined) VALUES (?, ?, ?, 1, ?)",
                                 (room["id"], k, b, now_iso()))
            changed = (mode, rules, seed) != (room["mode"], room["rules"], room["seed"])
            if changed:  # new terms: everyone confirms again
                conn.execute("UPDATE mp_seats SET ready = 0 WHERE room_id = ? AND user_id IS NOT NULL "
                             "AND user_id != ?", (room["id"], user["id"]))
            return bump(conn, room["id"], mode=mode, rules=rules, seed=seed, max_players=maxp)
        return act(code, user, fn)

    @router.post("/api/rooms/{code}/kick")
    def kick(code: str, body: SeatBody, user=Depends(require_user)):
        ensure_db()

        def fn(conn, room, seats, me):
            need_host(room, user)
            need_lobby(room)
            target = next((s for s in seats if s["seat"] == body.seat), None)
            if target is None:
                raise ApiError(404, "no player in that seat")
            if target["user_id"] == user["id"]:
                raise ApiError(400, "you can't kick yourself; leave the room instead")
            conn.execute("DELETE FROM mp_seats WHERE room_id = ? AND seat = ?", (room["id"], body.seat))
            if target["user_id"] is not None:
                conn.execute("INSERT OR IGNORE INTO mp_kicked(room_id, user_id) VALUES (?, ?)",
                             (room["id"], target["user_id"]))
            return bump(conn, room["id"])
        return act(code, user, fn)

    def pass_host(conn, room, seats, leaving_user):
        nxt = next((s for s in seats if s["user_id"] is not None and s["user_id"] != leaving_user
                    and s["status"] == "in"), None)
        if nxt is not None:
            conn.execute("UPDATE mp_rooms SET host_id = ? WHERE id = ?", (nxt["user_id"], room["id"]))
        return nxt

    @router.post("/api/rooms/{code}/leave")
    def leave(code: str, user=Depends(require_user)):
        ensure_db()

        def fn(conn, room, seats, me):
            need_seat(me)
            if room["status"] == "lobby":
                conn.execute("DELETE FROM mp_seats WHERE room_id = ? AND seat = ?", (room["id"], me["seat"]))
                if room["host_id"] == user["id"] and pass_host(conn, room, seats, user["id"]) is None:
                    conn.execute("DELETE FROM mp_rooms WHERE id = ?", (room["id"],))
                    cache.drop(room["id"])
                    return "deleted"
                return bump(conn, room["id"])
            if room["status"] == "finished":
                conn.execute("UPDATE mp_seats SET status = 'left' WHERE room_id = ? AND seat = ?",
                             (room["id"], me["seat"]))
                return bump(conn, room["id"])
            m = match_for(conn, room, seats).clone()
            n0 = len(m.history)
            if not m.players[me["seat"]].out:
                m.leave(me["seat"])
            store_moves(conn, room["id"], m, n0)
            conn.execute("UPDATE mp_seats SET status = 'left' WHERE room_id = ? AND seat = ?",
                         (room["id"], me["seat"]))
            if room["host_id"] == user["id"]:
                pass_host(conn, room, seats, user["id"])
            v = finalize(conn, room, m, seats, "done") if m.over else bump(conn, room["id"])
            cache.put(room["id"], v, m)
            return v
        out = act(code, user, fn)
        return out if out is not None else {"deleted": True}

    @router.post("/api/rooms/{code}/remove")
    def remove(code: str, body: SeatBody, user=Depends(require_user)):
        """Remove a player who is away (offline and no move for LIMITS.afk_seconds): their run stops."""
        ensure_db()

        def fn(conn, room, seats, me):
            need_seat(me)
            if room["status"] != "playing":
                raise ApiError(409, "the game is not running")
            target = next((s for s in seats if s["seat"] == body.seat), None)
            if target is None or target["user_id"] is None:
                raise ApiError(404, "no player in that seat")
            if me["status"] != "in":
                raise ApiError(403, "only players still in the game can remove someone")
            if target["seat"] == me["seat"]:
                raise ApiError(400, "use leave to stop playing yourself")
            row = next(x for x in view(conn, room, user, seats)["players"] if x["seat"] == body.seat)
            m = match_for(conn, room, seats).clone()
            p = m.players[body.seat]
            if p.out or target["status"] != "in":
                raise ApiError(409, f"{p.name} is not playing any more")
            if not row["removable"]:
                raise ApiError(409, f"{p.name} is still here; wait until they have been away for "
                                    f"{LIMITS.afk_seconds} seconds")
            n0 = len(m.history)
            m.leave(body.seat)
            store_moves(conn, room["id"], m, n0)
            conn.execute("UPDATE mp_seats SET status = 'removed' WHERE room_id = ? AND seat = ?",
                         (room["id"], body.seat))
            if room["host_id"] == target["user_id"]:
                pass_host(conn, room, seats, target["user_id"])
            v = finalize(conn, room, m, seats, "done") if m.over else bump(conn, room["id"])
            cache.put(room["id"], v, m)
            return v
        return act(code, user, fn)

    @router.post("/api/rooms/{code}/start")
    def start(code: str, user=Depends(require_user)):
        ensure_db()

        def fn(conn, room, seats, me):
            need_host(room, user)
            need_lobby(room)
            if len(seats) < 2:
                raise ApiError(409, "a game needs at least 2 players (people or bots)")
            waiting = [s["username"] for s in seats if s["user_id"] is not None and s["user_id"] != user["id"]
                       and not s["ready"]]
            if waiting:
                raise ApiError(409, f"waiting for {', '.join(waiting)} to be ready")
            # seats 0..n-1 in join order
            order = list(seats)
            conn.execute("DELETE FROM mp_seats WHERE room_id = ?", (room["id"],))
            for k, s in enumerate(order):
                conn.execute("INSERT INTO mp_seats(room_id, seat, user_id, bot, ready, status, joined) "
                             "VALUES (?, ?, ?, ?, 1, 'in', ?)", (room["id"], k, s["user_id"], s["bot"], s["joined"]))
            seats2 = seats_of(conn, room["id"])
            m = Match(generate(room["seed"]), room["mode"], room["rules"], players_setup(seats2))
            store_moves(conn, room["id"], m, 0)   # the bots' opening moves
            v = bump(conn, room["id"], status="playing", started=now_iso())
            if m.over:   # only bots (can't happen: the host is a person), kept for safety
                room2 = room_by_code(conn, room["code"])
                v = finalize(conn, room2, m, seats2, "done")
            cache.put(room["id"], v, m)
            return v
        return act(code, user, fn)

    @router.post("/api/rooms/{code}/move")
    def move(code: str, body: MoveBody, user=Depends(require_user)):
        ensure_db()
        line = " ".join(body.move.split())
        if len(body.move) > 200 or not MOVE_RE.match(line):
            raise ApiError(400, "that is not a move (see the problem statement for the move syntax)")

        def fn(conn, room, seats, me):
            need_seat(me)
            if room["status"] != "playing":
                raise ApiError(409, "the game has not started" if room["status"] == "lobby" else "the game is over")
            if me["status"] != "in":
                raise ApiError(409, "you left this game")
            m = match_for(conn, room, seats).clone()
            n0 = len(m.history)
            try:
                m.apply(line, me["seat"])
            except IllegalMove as e:
                raise ApiError(400 if m.can_act(me["seat"]) else 409, f"{line}: {e}") from None
            store_moves(conn, room["id"], m, n0)
            v = finalize(conn, room, m, seats, "done") if m.over else bump(conn, room["id"])
            cache.put(room["id"], v, m)
            return v
        return act(code, user, fn, kind="move")

    @router.post("/api/rooms/{code}/rematch")
    def rematch(code: str, body: RematchBody, user=Depends(require_user)):
        """A new room with the same settings and bots; the others see its code and can join it."""
        ensure_db()
        code = check_code(code)
        limit(user, "action")
        conn = db.connect(settings.db)
        try:
            with db.transaction(conn, immediate=True):
                room = load(conn, code)
                seats = seats_of(conn, room["id"])
                if my_seat(seats, user) is None:
                    raise ApiError(403, "you are not in this room")
                if room["status"] != "finished":
                    raise ApiError(409, "the game is not over yet")
                if room["rematch_code"]:
                    new = conn.execute("SELECT code FROM mp_rooms WHERE code = ?", (room["rematch_code"],)).fetchone()
                    if new:
                        return {"code": new["code"]}
                if not limiter.hit((user["id"], "create"), LIMITS.create_per_window, LIMITS.create_window):
                    raise ApiError(429, "too many new rooms; wait a few minutes")
                if open_rooms(conn, user) >= LIMITS.open_rooms_per_user:
                    raise ApiError(429, f"you are already in {LIMITS.open_rooms_per_user} open rooms; leave one first")
                new_code_ = new_code(conn)
                t = now_iso()
                seed = room["seed"] if body.same_seed else random_seed()
                rid = conn.execute("INSERT INTO mp_rooms(code, host_id, mode, rules, seed, max_players, status, "
                                   "version, created, active) VALUES (?, ?, ?, ?, ?, ?, 'lobby', 1, ?, ?)",
                                   (new_code_, user["id"], room["mode"], room["rules"], seed, room["max_players"],
                                    t, t)).lastrowid
                conn.execute("INSERT INTO mp_seats(room_id, seat, user_id, ready, joined) VALUES (?, 0, ?, 1, ?)",
                             (rid, user["id"], t))
                k = 1
                for s in seats:
                    if s["user_id"] is None:
                        conn.execute("INSERT INTO mp_seats(room_id, seat, bot, ready, joined) VALUES (?, ?, ?, 1, ?)",
                                     (rid, k, s["bot"], t))
                        k += 1
                bump(conn, room["id"], rematch_code=new_code_)
                old_id = room["id"]
        finally:
            conn.close()
        notifier.fire(old_id)
        return {"code": new_code_}

    # ---- history

    def match_rows(conn, where, args):
        ms = conn.execute(f"SELECT * FROM mp_matches m WHERE {where} ORDER BY m.id DESC LIMIT 50", args).fetchall()
        out = []
        for mt in ms:
            ps = conn.execute("SELECT * FROM mp_match_players WHERE match_id = ? ORDER BY seat", (mt["id"],)).fetchall()
            out.append((mt, ps))
        return out

    def match_json(mt, ps, user, with_moves=False):
        you = next((p["seat"] for p in ps if p["user_id"] == user["id"]), None)
        return {"id": mt["id"], "code": mt["code"], "mode": mt["mode"], "rules": mt["rules"], "seed": mt["seed"],
                "started": mt["started"], "finished": mt["finished"], "ended": mt["ended"], "you": you,
                "players": [dict({"seat": p["seat"], "name": p["name"], "bot": p["bot"], "total": p["total"],
                                  "cleared": p["cleared"], "place": p["place"], "left": bool(p["left_early"]),
                                  "you": p["seat"] == you},
                                 **({"moves": p["moves"].split("\n") if p["moves"] else []} if with_moves else {}))
                            for p in ps]}

    @router.get("/api/matches")
    def my_matches(user=Depends(require_user), conn=Depends(get_conn)):
        rows = match_rows(conn, "m.id IN (SELECT match_id FROM mp_match_players WHERE user_id = ?)", (user["id"],))
        rec = conn.execute("SELECT COUNT(*) AS played, COALESCE(SUM(CASE WHEN p.place = 1 AND m.players > 1 THEN 1 ELSE 0 END), 0) "
                           "AS wins FROM mp_match_players p JOIN mp_matches m ON m.id = p.match_id WHERE p.user_id = ?",
                           (user["id"],)).fetchone()
        return {"record": {"played": rec["played"], "wins": rec["wins"]},
                "matches": [match_json(mt, ps, user) for mt, ps in rows]}

    @router.get("/api/matches/{match_id}")
    def match_detail(match_id: int, user=Depends(require_user), conn=Depends(get_conn)):
        rows = match_rows(conn, "m.id = ?", (match_id,))
        if not rows or not any(p["user_id"] == user["id"] for p in rows[0][1]):
            raise ApiError(404, "match not found")
        mt, ps = rows[0]
        return match_json(mt, ps, user, with_moves=True)

    return router

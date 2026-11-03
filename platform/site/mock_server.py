#!/usr/bin/env python3
"""Mock Poker Run backend for developing the static site without the real API.

    python3 platform/site/build.py
    python3 platform/site/mock_server.py              # http://127.0.0.1:8765/

Serves platform/site/dist/ and implements every endpoint of platform/API.md in
memory with fake data. Stdlib only. Nothing is persisted; restart to reset.

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
        if path.startswith("/api/"):
            with LOCK:
                return self.api("GET", path)
        return super().do_GET()

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
        if path.startswith("/api/submissions") or path.startswith("/api/admin/"):
            if not u:
                return self.err(401, "not logged in")
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

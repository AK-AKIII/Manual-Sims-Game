#!/usr/bin/env python3
"""Mock Poker Run backend for developing the static site without the real API.

    python3 platform/site/build.py
    python3 platform/site/mock_server.py              # http://127.0.0.1:8765/

Serves platform/site/dist/ and implements every endpoint of platform/API.md in
memory with fake data, including the whole Admin API. Stdlib only. Nothing is
persisted; restart to reset.

Accounts that exist at start (sign in with the username or the email):
    admin  / adminpass   admin, treated as listed in PR_ADMINS (so "remove admin" gives 409)
    helper / helperpass  admin via the database flag (can be de-admined, disabled, deleted)
    demo   / demopass    student with history, demo@example.com
    Most accounts have a fake email (<name>@example.com); helper, kicker and
    low_road are "old" accounts without one.
    14 fake students (password "xxxxxxxxxxxx"); `lastcall` copied `momentum` and
    `hal9000` copied most of `ace_high`, so the similarity check has pairs to show.
    `greedy_gus` has one errored submission ("Rejudge all errored" has work to do).

Online rooms (/api/rooms, /api/matches) are implemented too, with the real game
logic (poker_run.multiplayer from this repository), minus rate limits, expiry
and removing away players.

Judging is simulated: a submission is queued for ~2 s, then runs one test at a
time (tests_done climbs), then is done. The verdicts depend on the source:
    contains "raise"         some tests RE
    contains "sleep"         some tests TLE
    contains "WRONG"         some tests WA
    no "print"/"stdout"      every test WA (no output)
    contains "JUDGE_ERROR"   the submission ends with status "error"

Admin API notes: the safety rules of API.md are enforced (self-actions, the last
active admin, PR_ADMINS); disabling deletes sessions and cancels queued runs;
disabled students drop off the public leaderboard (admins see them after the
ranked rows with rank null). The per-test bests are a fixed fake field, so
disabling doesn't change anyone's relative score here. Settings overrides apply
to /api/contest, registration, the hourly limit and the freeze. Similarity uses
Python's tokenize on the latest full submission per student, as API.md says.
The worker heartbeat is always fresh unless --stale-worker.

Options: --invite CODE (registration needs it), --frozen, --closed (full
submissions get 403), --upcoming (contest starts in 2 h), --no-deadline,
--port N, --dir PATH, --speed X (judge speed multiplier), --latency S,
--stale-worker, --quiet.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import random
import secrets
import string
import threading
import time
import tokenize
import zipfile
from datetime import datetime, timezone
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
STARTER_PATH = os.path.join(ROOT, "poker_run", "starters", "starter.py")
LOCK = threading.RLock()
T0 = time.time()
ENV_ADMINS = {"admin"}          # the mock's PR_ADMINS
EDITABLE = ("name", "start", "end", "freeze", "invite", "full_per_hour", "announcement", "announcement_on")
CANCELLED_ADMIN = "cancelled by an admin"
CANCELLED_DISABLED = "cancelled: account disabled"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NO_EMAIL = {"helper", "kicker", "low_road"}


def norm_email(v):
    """-> lowercase-trimmed email, or None if it isn't a sensible address."""
    if not isinstance(v, str):
        return None
    v = v.strip().lower()
    return v if len(v) <= 254 and EMAIL_RE.match(v) else None


def mask_email(v):
    if not v:
        return None
    local, _, dom = v.partition("@")
    return local[:1] + "***@" + dom


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    """ISO 8601 -> epoch seconds (naive = UTC); None for None/""; raises ValueError otherwise."""
    if s is None or s == "":
        return None
    if not isinstance(s, str):
        raise ValueError("not a string")
    d = datetime.fromisoformat(s.strip().replace("Z", "+00:00").replace(" ", "T"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def h(*parts):
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def csv_cell(v):
    """Formula-injection guard from API.md."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v)
    if s and s[0] in "=+-@\t\r" and isinstance(v, str):
        return "'" + s
    return s


def to_csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([csv_cell(r.get(k)) for k in header])
    return buf.getvalue().encode("utf-8")


# ------------------------------------------------------------------ similarity (API.md "Similarity")

def tokens(src):
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                            tokenize.ENDMARKER, tokenize.ENCODING):
                continue
            if tok.type == tokenize.STRING or (hasattr(tokenize, "FSTRING_START") and tok.type in (
                    tokenize.FSTRING_START, tokenize.FSTRING_MIDDLE, tokenize.FSTRING_END)):
                if not out or out[-1] != "STR":
                    out.append("STR")
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        src = re.sub(r"#[^\n]*", "", src)
        src = re.sub(r"(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"[^\"\n]*\"|'[^'\n]*')", " STR ", src)
        out = re.findall(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|\S", src)
    return out


def grams(src, n=5):
    t = tokens(src)
    return {tuple(t[i:i + n]) for i in range(len(t) - n + 1)}


_STARTER_GRAMS = None


def starter_grams():
    global _STARTER_GRAMS
    if _STARTER_GRAMS is None:
        try:
            with open(STARTER_PATH, encoding="utf-8") as f:
                _STARTER_GRAMS = grams(f.read())
        except OSError:
            _STARTER_GRAMS = set()
    return _STARTER_GRAMS


# ------------------------------------------------------------------ fake sources

TEMPLATES = [
    '''"""Solver by {user}: greedy over the best-scoring play."""
import sys


def {score}({hand}):
    # count ranks and pick the largest group
    counts = {{}}
    for c in {hand}:
        counts[c % 13] = counts.get(c % 13, 0) + 1
    return max(counts.values()) * {k1} + len({hand})


def solve(text):
    lines = text.split("\\n")
    {moves} = []
    {hand} = [int(x) for x in lines[1].split()[:8]] if len(lines) > 1 else []
    for {i} in range({k2}):
        {best} = sorted({hand}, key=lambda c: -(c % 13))[:5]
        {moves}.append("play " + " ".join(map(str, {best})))
        if {score}({best}) > {k3}:
            break
    return {moves}


def main():
    out = solve(sys.stdin.read())
    print(len(out))
    for m in out:
        print(m)


if __name__ == "__main__":
    main()
''',
    '''"""{user}'s solver: beam search with a cheap evaluation."""
import sys
from heapq import nlargest


WIDTH = {k1}


def {score}(state):
    total, {hand} = state
    return total + sum(c % 13 for c in {hand}) * {k2}


def expand(state):
    total, {hand} = state
    for {i} in range(len({hand})):
        rest = {hand}[:{i}] + {hand}[{i} + 1:]
        yield (total + {hand}[{i}] % 13, rest), "discard %d" % {hand}[{i}]


def solve(text):
    nums = list(map(int, text.split()[:12]))
    beam = [((0, nums), [])]
    for _ in range({k3} % 7 + 2):
        cand = []
        for state, {moves} in beam:
            for nxt, mv in expand(state):
                cand.append((nxt, {moves} + [mv]))
        beam = nlargest(WIDTH, cand, key=lambda x: {score}(x[0])) or beam
    {best} = beam[0][1]
    return {best}


if __name__ == "__main__":
    res = solve(sys.stdin.read())
    sys.stdout.write("%d\\n" % len(res) + "".join(r + "\\n" for r in res))
''',
    '''"""{user} - rule-based: keep pairs, buy levels, never waste discards."""
import sys
from collections import Counter


def {score}({hand}, levels):
    ranks = Counter(c % 13 for c in {hand})
    top = ranks.most_common(1)[0][1] if ranks else 0
    return top * {k1} + levels.get("PR", 0) * {k2}


def plan({hand}, money):
    {moves} = []
    keep = [c for c in {hand} if Counter(x % 13 for x in {hand})[c % 13] > 1]
    toss = [c for c in {hand} if c not in keep][:5]
    if toss:
        {moves}.append("discard " + " ".join(str(c) for c in toss))
    {best} = keep[:5] or {hand}[:1]
    {moves}.append("play " + " ".join(str(c) for c in {best}))
    if money >= {k3}:
        {moves}.append("buy level PR")
    return {moves}


def main():
    words = sys.stdin.read().split()
    {hand} = [int(w) for w in words[1:9] if w.isdigit()]
    for {i}, line in enumerate(plan({hand}, {k3})):
        print(line)


main()
''',
]
VOCAB = {
    "score": ["score", "value_of", "rate", "evaluate", "heur", "pts", "goodness"],
    "hand": ["hand", "cards", "hold", "h", "pool", "row"],
    "moves": ["moves", "out_lines", "plan_out", "acts", "steps", "seq"],
    "best": ["best", "pick", "top5", "chosen", "winner", "sel"],
    "i": ["i", "j", "k", "idx", "n", "step_no"],
}


LOCALS = {
    "counts": ["counts", "freq", "tally", "by_rank", "hist", "cnt"], "lines": ["lines", "rows", "ls", "text_lines", "parts", "chunks"],
    "out": ["out", "result", "answer", "lines_out", "emitted", "plan_lines"], "c": ["c", "card", "cid", "x0", "q", "cc"],
    "total": ["total", "acc", "sum_so_far", "tot", "running", "score0"], "rest": ["rest", "remaining", "others", "left", "tail", "rem"],
    "state": ["state", "node", "st", "pos", "config", "snap"], "nxt": ["nxt", "child", "succ", "after", "new_state", "follow"],
    "mv": ["mv", "move", "action", "op", "act", "cmd"], "cand": ["cand", "pool2", "frontier", "options", "nexts", "cands"],
    "beam": ["beam", "layer", "front", "keepers", "survivors", "queue"], "WIDTH": ["WIDTH", "BEAM", "K", "TOPK", "MAX_KEEP", "BW"],
    "nums": ["nums", "values", "ints", "data", "numbers", "vals"], "keep": ["keep", "pairs", "held", "save", "stay", "kept"],
    "toss": ["toss", "junk", "drop", "throw", "bin", "away"], "ranks": ["ranks", "rk", "rank_count", "rc", "groups", "byr"],
    "top": ["top", "most", "peak", "maxc", "big", "mx"], "levels": ["levels", "lv", "lvl", "upgrades", "tiers", "boosts"],
    "money": ["money", "cash", "gold", "coins", "bank", "funds"], "words": ["words", "toks", "fields", "items", "pieces", "wds"],
    "line": ["line", "ln", "row_out", "entry", "s", "msg"], "expand": ["expand", "children", "successors", "neighbours", "branch", "grow"],
    "plan": ["plan", "decide", "choose", "strategy", "think", "policy"], "res": ["res", "got", "answer_list", "found", "final", "rr"],
    "m": ["m", "mm", "each", "one", "it", "z"],
}


def fake_source(user):
    rng = random.Random(h("src", user))
    words = {k: v[rng.randrange(len(v))] for k, v in VOCAB.items()}
    tpl = TEMPLATES[h("tpl", user) % len(TEMPLATES)]
    src = tpl.format(user=user, k1=rng.randint(2, 99), k2=rng.randint(2, 99), k3=rng.randint(10, 999), **words)
    for name, pool in LOCALS.items():
        new = pool[rng.randrange(len(pool))]
        src = re.sub(r"(?<![\w.\"])%s(?![\w\"])" % re.escape(name), new, src)
    return src


def copied_source(src, extra):
    """A copy with renamed comments and a few extra lines: what the similarity check should catch."""
    src = re.sub(r"#[^\n]*", "# my own idea", src)
    return src.replace('"""', '"""(edited) ', 1) + extra


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


class State:
    def __init__(self, args):
        self.args = args
        self.users = {}       # username -> {id, pw, is_admin, created, last_login, disabled_at, disabled_reason}
        self.sessions = {}    # token -> username
        self.subs = []        # dicts, ids ascending
        self.audit = []       # dicts, ids ascending
        self.overrides = {}   # settings key -> value (admin-panel overrides)
        self.next_id = 1
        self.next_uid = 1
        self.version, self._sched_v, self._sched = 0, -1, {}
        self.public_seeds = list(range(1, 11))
        self.hidden = 50
        self.hidden_seeds = list(range(1001, 1001 + self.hidden))
        # best raw score per hidden test among the fake field
        self.best = [40000 + h("best", i) % 160000 for i in range(self.hidden)]
        now = time.time()
        if args.upcoming:
            start, end = now + 2 * 3600 + 17, now + 9 * 86400
        elif args.closed:
            start, end = now - 10 * 86400, now - 3600
        elif args.no_deadline:
            start = end = None
        else:
            start, end = now - 2 * 86400, now + 3 * 86400 + 5 * 3600 + 42 * 60
        freeze = (now - 600) if args.frozen else None
        self.env = {"name": "Poker Run", "start": iso(start) if start else None, "end": iso(end) if end else None,
                    "freeze": iso(freeze) if freeze else None, "invite": args.invite, "full_per_hour": 6,
                    "announcement": "", "announcement_on": False}
        self.add_user("admin", "adminpass", False, created=now - 13 * 86400, last_login=now - 3600)
        self.add_user("helper", "helperpass", True, created=now - 12 * 86400, last_login=now - 5 * 86400)
        self.add_user("demo", "demopass", False, created=now - 9 * 86400, last_login=now - 7300)
        self.rooms = Rooms()
        self.seed_field(now)
        self.log("(cli)", "settings.update", None, {"full_per_hour": [6, 6]}, t=now - 11 * 86400)
        self.log("admin", "submission.rejudge", "kicker", {"id": 3, "kind": "full"}, t=now - 2 * 86400)

    # ---- settings
    def setting(self, key):
        return self.overrides.get(key, self.env[key])

    @property
    def start(self):
        return parse_iso(self.setting("start"))

    @property
    def end(self):
        return parse_iso(self.setting("end"))

    @property
    def freeze(self):
        return parse_iso(self.setting("freeze"))

    def is_open(self, now=None):
        now = now or time.time()
        return (self.start is None or now >= self.start) and (self.end is None or now < self.end)

    def contest(self):
        ann = self.setting("announcement") or ""
        return {"name": self.setting("name"), "start": self.setting("start"), "end": self.setting("end"),
                "freeze": self.setting("freeze"), "now": iso(time.time()), "open": self.is_open(),
                "public_seeds": self.public_seeds, "hidden_count": self.hidden, "time_limit": 2.0,
                "full_per_hour": self.setting("full_per_hour"), "invite_required": bool(self.setting("invite")),
                "max_source_kb": 256,
                "announcement": ann if (self.setting("announcement_on") and ann.strip()) else None}

    def settings_json(self):
        return {"editable": {k: {"value": self.setting(k), "env": self.env[k], "overridden": k in self.overrides} for k in EDITABLE},
                "readonly": {"hidden_count": self.hidden, "public_seeds": self.public_seeds, "time_limit": 2.0,
                             "memory_mb": 1024, "max_source_kb": 256, "sandbox": "local", "workers": 1}}

    def validate_settings(self, set_, clear):
        """-> (new overrides, None) or (None, error message). Validated together."""
        if not isinstance(set_, dict) or not isinstance(clear, list):
            return None, "send {set: {KEY: value}, clear: [KEY]}"
        new = dict(self.overrides)
        for k in clear:
            if k not in EDITABLE:
                return None, "unknown setting %r" % (k,)
            new.pop(k, None)
        for k, v in set_.items():
            if k not in EDITABLE:
                return None, "unknown setting %r" % (k,)
            if k == "name":
                if not isinstance(v, str) or not 1 <= len(v.strip()) <= 80:
                    return None, "name must be 1-80 characters"
                v = v.strip()
            elif k in ("start", "end", "freeze"):
                if v is not None:
                    try:
                        v = iso(parse_iso(v)) if v != "" else None
                    except (ValueError, TypeError):
                        return None, "%s must be an ISO 8601 time or null" % k
            elif k == "invite":
                if not isinstance(v, str):
                    return None, "invite must be a string"
                v = v.strip()
                if v and (not 4 <= len(v) <= 64 or not v.isprintable()):
                    return None, "invite must be empty (open registration) or 4-64 printable characters"
            elif k == "full_per_hour":
                if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 1000:
                    return None, "full_per_hour must be an integer from 0 to 1000"
            elif k == "announcement":
                if not isinstance(v, str) or len(v) > 500:
                    return None, "announcement must be at most 500 characters"
            elif k == "announcement_on":
                if not isinstance(v, bool):
                    return None, "announcement_on must be true or false"
            new[k] = v
        eff = lambda k: new.get(k, self.env[k])  # noqa: E731
        s, e, f = parse_iso(eff("start")), parse_iso(eff("end")), parse_iso(eff("freeze"))
        if s is not None and e is not None and not s < e:
            return None, "start must be before end"
        if f is not None and s is not None and f < s:
            return None, "freeze must be at or after start"
        if f is not None and e is not None and not f < e:
            return None, "freeze must be before end"
        return new, None

    @staticmethod
    def mask(key, v):
        if key != "invite":
            return v
        if not v:
            return "(open)"
        return v[:2] + "…" + v[-2:]

    # ---- audit
    def log(self, actor, action, target, details, t=None):
        self.audit.append({"id": len(self.audit) + 1, "created": iso(t or time.time()), "actor": actor, "action": action,
                           "target": target, "details": details or {}})

    # ---- users
    def add_user(self, name, pw, admin=False, created=None, last_login=None, email=False):
        if email is False:   # fake data: <name>@example.com unless it's an "old" account
            email = None if name in NO_EMAIL else "%s@example.com" % name.lower().replace("_", ".")
        self.users[name] = {"pw": pw, "is_admin": admin, "created": created or time.time(), "id": self.next_uid, "email": email,
                            "last_login": last_login, "disabled_at": None, "disabled_reason": None}
        self.next_uid += 1

    def is_admin(self, name):
        return name in self.users and (self.users[name]["is_admin"] or name in ENV_ADMINS)

    def active(self, name):
        return name in self.users and self.users[name]["disabled_at"] is None

    def by_id(self, uid):
        return next((n for n, r in self.users.items() if r["id"] == uid), None)

    def find_user(self, name):
        return next((n for n in self.users if n.lower() == str(name).lower()), None)

    def find_email(self, email):
        return next((n for n, r in self.users.items() if r["email"] and r["email"] == email), None)

    def me_json(self, name):
        return {"username": name, "is_admin": self.is_admin(name), "email": self.users[name]["email"]}

    def active_admins(self):
        return [n for n in self.users if self.is_admin(n) and self.active(n)]

    def urow(self, name, live=None):
        rec = self.users[name]
        live = live if live is not None else self.standings_map()
        mine = [s for s in self.subs if s["user"] == name]
        best = live.get(name)
        return {"id": rec["id"], "username": name, "email": rec["email"], "created": iso(rec["created"]), "is_admin": self.is_admin(name),
                "admin_env": name in ENV_ADMINS, "status": "active" if rec["disabled_at"] is None else "disabled",
                "disabled_at": iso(rec["disabled_at"]) if rec["disabled_at"] else None, "disabled_reason": rec["disabled_reason"],
                "last_login": iso(rec["last_login"]) if rec["last_login"] else None,
                "public_submissions": sum(1 for s in mine if s["kind"] == "public"),
                "full_submissions": sum(1 for s in mine if s["kind"] == "full"),
                "best_relative_total": best["relative_total"] if best else None,
                "best_submission_id": best["best_submission_id"] if best else None,
                "rank": best["rank"] if best else None,
                "last_submission": iso(max(s["created_t"] for s in mine)) if mine else None}

    # ---- fake data
    def seed_field(self, now):
        names = ["ace_high", "rivalrook", "flushfive", "beam_queen", "mcts_mike", "greedy_gus", "twin_engine",
                 "lastcall", "momentum", "low_road", "hal9000", "paintpromote", "shopkeeper", "kicker"]
        copies = {"lastcall": ("momentum", "\n# tweak\nTUNE = 3\n"),
                  "hal9000": ("ace_high", "\n\ndef unused_helper(xs):\n    return [x * 2 for x in xs if x % 3]\n")}
        rng = random.Random(7)
        for k, name in enumerate(names):
            created = now - 86400 * (12.5 - 0.75 * k)
            self.add_user(name, "x" * 12, created=created)
            skill = 0.92 - 0.055 * k + rng.uniform(-0.02, 0.02)
            if name in copies:
                src_of, extra = copies[name]
                src = copied_source(fake_source(src_of), extra)
            else:
                src = fake_source(name)
            last = None
            for j in range(1 + k % 4):
                t = min(now - 1800, created + 86400 * 0.45 * (j + 1) + k * 300)
                self.fake_done(name, "full", t, skill - 0.04 * (k % 4 - j), source=src if j == k % 4 else STARTER_FAKE % (name, ""))
                last = t
            if k % 3:
                self.users[name]["last_login"] = last
        # an errored run for "Rejudge all errored"
        self.fake_done("greedy_gus", "public", now - 5000, 0.4, source=STARTER_FAKE % ("greedy_gus", "# JUDGE_ERROR\n"))
        # demo user's history: two public runs and a full run
        self.fake_done("demo", "public", now - 7200, 0.5)
        self.fake_done("demo", "public", now - 5400, 0.6, src_extra="# second try\n")
        self.fake_done("demo", "full", now - 3600, 0.55)

    def fake_done(self, user, kind, t, skill, src_extra="", source=None):
        src = source or STARTER_FAKE % (user, src_extra)
        sub = self.new_sub(user, kind, src, t)
        sub["fake_skill"] = max(0.05, min(0.99, skill))
        sub["created_t"] = t
        sub["forced_done"] = True
        self.bump()

    def new_sub(self, user, kind, source, t=None):
        t = t or time.time()
        sub = {"id": self.next_id, "user": user, "kind": kind, "source": source, "created_t": t, "error_text": None,
               "tests_total": len(self.public_seeds) if kind == "public" else self.hidden, "forced_done": False,
               "cancelled": None}
        self.next_id += 1
        self.subs.append(sub)
        self.bump()
        return sub

    def sub(self, sid):
        return next((s for s in self.subs if s["id"] == sid), None)

    # ---- simulated judging
    def timeline(self, sub):
        """(status, tests_done, started, finished) at the current time."""
        if sub["cancelled"]:
            return "error", 0, None, sub.get("cancelled_t")
        if sub["forced_done"]:
            return "done", sub["tests_total"], sub["created_t"] + 1, sub["created_t"] + 9
        speed = self.args.speed
        per = (0.45 if sub["kind"] == "public" else 0.12) / speed
        start = self.start_time(sub)
        end = start + per * sub["tests_total"]
        now = time.time()
        if now < start:
            return "queued", 0, None, None
        if now < end:
            return "running", min(sub["tests_total"] - 1, int((now - start) / per)), start, None
        return "done", sub["tests_total"], start, end

    def bump(self):
        """Call after anything that changes the simulated queue."""
        self.version += 1

    def schedule(self):
        """id -> (start, end) for every simulated run: one at a time, in enqueue order.
        Recomputed in one pass whenever the queue changed (cached by version)."""
        if self._sched_v != self.version:
            speed = self.args.speed
            sched, prev_end = {}, None
            for o in sorted((o for o in self.subs if not o["forced_done"] and not o["cancelled"]), key=lambda o: (o["created_t"], o["id"])):
                start = o["created_t"] + 2.0 / speed
                if prev_end is not None and prev_end > o["created_t"]:
                    start = max(start, prev_end + 0.3 / speed)
                per = (0.45 if o["kind"] == "public" else 0.12) / speed
                prev_end = start + per * o["tests_total"]
                sched[o["id"]] = (start, prev_end)
            self._sched, self._sched_v = sched, self.version
        return self._sched

    def start_time(self, sub):
        return self.schedule()[sub["id"]][0]

    def finish_time(self, sub):
        if sub["forced_done"]:
            return sub["created_t"]
        if sub["cancelled"]:
            return sub.get("cancelled_t") or sub["created_t"]
        return self.schedule()[sub["id"]][1]

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
        cleared = min(8, max(1, int(8 * skill + (r % 3) - 1))) if verdict == "AC" else 0
        return {"test": k + 1, "verdict": verdict, "time": t, "_raw": raw, "_cleared": cleared, "_msg": msg or "valid"}

    def row(self, sub, with_user=False):
        status, done, started, finished = self.timeline(sub)
        if status == "done" and "JUDGE_ERROR" in sub["source"]:
            status = "error"
        res = [self.result(sub, k) for k in range(done)]
        out = {"id": sub["id"], "kind": sub["kind"], "status": status, "created": iso(sub["created_t"]),
               "started": iso(started) if started else None,
               "finished": iso(finished) if finished and status in ("done", "error") else None,
               "raw_total": None, "relative_total": None, "tests_ac": None, "tests_total": sub["tests_total"],
               "tests_done": done if status != "queued" else 0, "position": None, "max_time": None, "error": None}
        if status == "queued":
            out["position"] = self.position(sub)
        if status == "error":
            out["error"] = sub["cancelled"] or "sandbox failed: container exited with code 137"
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
            out["user_id"] = self.users[sub["user"]]["id"] if sub["user"] in self.users else None
            out["user_disabled"] = not self.active(sub["user"])
        return out, res

    def detail(self, sub, admin):
        out, res = self.row(sub, admin)
        out["source"] = sub["source"]
        if sub["kind"] == "public":
            out["results"] = res
        elif admin:   # admins see seeds and scores of full runs too
            out["results"] = [{"test": r["test"], "seed": self.hidden_seeds[r["test"] - 1], "verdict": r["verdict"],
                               "score": r["_raw"], "cleared": r["_cleared"], "time": r["time"], "message": r["_msg"]} for r in res]
        else:
            out["results"] = [{"test": r["test"], "verdict": r["verdict"], "time": r["time"]} for r in res]
        return out

    def standings(self, cutoff=None, include_disabled=False):
        """Ranked rows (+ disabled ones after them with rank None when include_disabled)."""
        best, counts = {}, {}
        for sub in self.subs:
            if sub["kind"] != "full" or (cutoff and sub["created_t"] >= cutoff) or sub["user"] not in self.users:
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
        order = sorted(best.items(), key=lambda kv: (-kv[1]["relative_total"], kv[1]["created_t"]))
        rows, off = [], []
        for u, r in order:
            item = {"rank": None, "username": u, "relative_total": r["relative_total"], "raw_total": r["raw_total"],
                    "tests_ac": r["tests_ac"], "submitted": r["created"], "submissions": counts[u],
                    "best_submission_id": r["id"]}
            if self.active(u):
                item["rank"] = len(rows) + 1
                rows.append(item)
            elif include_disabled:
                item["disabled"] = True
                off.append(item)
        return rows + off

    def standings_map(self):
        return {r["username"]: r for r in self.standings(None, True)}

    def leaderboard(self, admin):
        cutoff = self.freeze if (self.freeze and not admin and time.time() >= self.freeze) else None
        rows = self.standings(cutoff, include_disabled=admin)
        return {"frozen": bool(cutoff), "rows": [{k: v for k, v in r.items() if k != "best_submission_id"} for r in rows]}

    # ---- admin helpers
    def cancel(self, sub, reason):
        sub["cancelled"] = reason
        sub["cancelled_t"] = time.time()
        self.bump()

    def rejudge(self, sub):
        sub["forced_done"] = False
        sub["cancelled"] = None
        sub["created_t"] = time.time()
        sub["rejudged"] = sub.get("rejudged", 0) + 1
        self.bump()

    def overview(self):
        now = time.time()
        rows = [(s, self.row(s, True)) for s in self.subs]
        live = self.standings(None, True)
        counts = {"registered": len(self.users), "active": sum(1 for n in self.users if self.active(n)),
                  "disabled": sum(1 for n in self.users if not self.active(n)), "admins": sum(1 for n in self.users if self.is_admin(n)),
                  "active_24h": 0, "submissions": len(self.subs),
                  "submissions_24h": sum(1 for s in self.subs if s["created_t"] > now - 86400),
                  "full": sum(1 for s in self.subs if s["kind"] == "full"), "public": sum(1 for s in self.subs if s["kind"] == "public"),
                  "queued": sum(1 for _, (r, _) in rows if r["status"] == "queued"),
                  "running": sum(1 for _, (r, _) in rows if r["status"] == "running"),
                  "errors": sum(1 for _, (r, _) in rows if r["status"] == "error")}
        recent = {s["user"] for s in self.subs if s["created_t"] > now - 86400}
        counts["active_24h"] = sum(1 for n, rec in self.users.items() if n in recent or (rec["last_login"] or 0) > now - 86400)
        verdicts = {"AC": 0, "WA": 0, "TLE": 0, "RE": 0}
        for _, (_, res) in rows:
            for r in res:
                verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        today = datetime.fromtimestamp(now, timezone.utc).date()
        days = [datetime.fromordinal(today.toordinal() - i).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
        day = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
        daily = {d: {"date": d, "registrations": 0, "submissions": 0, "full": 0} for d in days}
        for rec in self.users.values():
            d = day(rec["created"])
            if d in daily:
                daily[d]["registrations"] += 1
        for s in self.subs:
            d = day(s["created_t"])
            if d in daily:
                daily[d]["submissions"] += 1
                daily[d]["full"] += s["kind"] == "full"
        last_seen = (T0 - 240) if self.args.stale_worker else now - (now % 4)
        return {"counts": counts,
                "judge": {"queue": counts["queued"],
                          "running": [{"id": r["id"], "username": r["username"], "kind": r["kind"], "tests_done": r["tests_done"],
                                       "tests_total": r["tests_total"], "started": r["started"]} for _, (r, _) in rows if r["status"] == "running"],
                          "worker_last_seen": iso(last_seen), "worker_alive": now - last_seen < 30},
                "verdicts": verdicts, "daily": [daily[d] for d in days],
                "top": [{k: v for k, v in r.items() if k != "best_submission_id"} for r in live if r["rank"] is not None][:5],
                "contest": self.contest()}

    def similarity(self, limit):
        latest = {}
        for s in self.subs:
            if s["kind"] == "full" and s["user"] in self.users:
                if s["user"] not in latest or s["created_t"] > latest[s["user"]]["created_t"]:
                    latest[s["user"]] = s
        subs = sorted(latest.values(), key=lambda s: -s["created_t"])
        truncated = len(subs) > 600
        subs = subs[:600]
        base = starter_grams()
        sets, skipped = [], 0
        for s in subs:
            g = grams(s["source"]) - base
            if len(g) < 20:
                skipped += 1
                continue
            sets.append((s, g))
        pairs = []
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                (sa, ga), (sb, gb) = sets[i], sets[j]
                inter = len(ga & gb)
                sim = inter / len(ga | gb) if (ga or gb) else 0
                if sim >= 0.2:
                    pairs.append({"a": {"username": sa["user"], "submission_id": sa["id"]},
                                  "b": {"username": sb["user"], "submission_id": sb["id"]},
                                  "similarity": round(sim, 4), "shared": inter, "a_size": len(ga), "b_size": len(gb)})
        pairs.sort(key=lambda p: -p["similarity"])
        return {"pairs": pairs[:limit], "compared": len(sets), "skipped": skipped, "truncated": truncated,
                "method": "token 5-grams (Python tokenize; comments, whitespace and string contents ignored), "
                          "minus 5-grams from starter.py; Jaccard similarity of each pair; latest full submission per student"}


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

    def send_file(self, data, ctype, filename):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def err(self, status, msg):
        self.send_json(status, {"error": msg})

    def token(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        tok = c.get("pr_session")
        return tok.value if tok else None

    def user(self):
        name = self.state.sessions.get(self.token() or "")
        if name not in self.state.users or not self.state.active(name):
            return None   # leftover sessions of disabled users are rejected
        return name, self.state.users[name]

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
        self.state.users[name]["last_login"] = time.time()
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

    def state_changing(self, method):
        path = urlsplit(self.path).path
        if not path.startswith("/api/"):
            return self.err(405, "method not allowed")
        if self.headers.get("X-Requested-With") != "fetch":
            return self.err(403, "missing X-Requested-With header")
        with LOCK:
            return self.api(method, path)

    def do_POST(self):
        return self.state_changing("POST")

    def do_DELETE(self):
        return self.state_changing("DELETE")

    def query(self):
        return {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def api(self, method, path):
        st = self.state
        time.sleep(st.args.latency)
        u = self.user()
        if method == "GET" and path == "/api/health":
            return self.send_json(200, {"ok": True})
        if method == "GET" and path == "/api/contest":
            return self.send_json(200, st.contest())
        if method == "GET" and path == "/api/me":
            if not u:
                return self.err(401, "not logged in")
            return self.send_json(200, st.me_json(u[0]))
        if method == "POST" and path == "/api/register":
            b = self.body()
            if not isinstance(b, dict):
                return self.err(400, "invalid JSON")
            name, pw, inv = str(b.get("username", "")), str(b.get("password", "")), str(b.get("invite", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", name):
                return self.err(400, "username must be 3-20 characters: letters, digits, _ or -")
            if len(pw) < 8:
                return self.err(400, "password must be at least 8 characters")
            email = norm_email(b.get("email"))
            if not email:
                return self.err(400, "enter a valid email address")
            if st.setting("invite") and inv != st.setting("invite"):
                return self.err(403, "wrong invite code")
            if st.find_user(name):
                return self.err(409, "that username is taken")
            if st.find_email(email):
                return self.err(409, "email already registered")
            st.add_user(name, pw, email=email)
            return self.send_json(201, st.me_json(name), [self.login_cookie(name)])
        if method == "POST" and path == "/api/login":
            b = self.body()
            if not isinstance(b, dict):
                return self.err(400, "invalid JSON")
            ident = str(b.get("username") or b.get("login") or "").strip()
            name = st.find_user(ident) or (st.find_email(ident.lower()) if "@" in ident else None)
            pw = str(b.get("password", ""))
            rec = st.users.get(name)
            if not rec or rec["pw"] != pw:
                return self.err(401, "wrong username or password")
            if rec["disabled_at"] is not None:
                return self.err(403, "this account is disabled")
            return self.send_json(200, st.me_json(name), [self.login_cookie(name)])
        if method == "POST" and path == "/api/logout":
            st.sessions.pop(self.token() or "", None)
            return self.send_json(204, None, [("Set-Cookie", "pr_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")])
        if method == "GET" and path == "/api/leaderboard":
            return self.send_json(200, st.leaderboard(bool(u and st.is_admin(u[0]))))

        # everything below needs a session
        if path.startswith(("/api/submissions", "/api/admin/", "/api/account/", "/api/rooms", "/api/matches")):
            if not u:
                return self.err(401, "not logged in")
        if path.startswith(("/api/rooms", "/api/matches")) and st.rooms.api(self, method, path, u[0]):
            return
        name, rec = u if u else (None, None)
        admin = bool(name and st.is_admin(name))
        if method == "POST" and path == "/api/account/email":
            b = self.body()
            if not isinstance(b, dict):
                return self.err(400, "invalid JSON")
            if str(b.get("password", "")) != rec["pw"]:
                return self.err(401, "wrong password")
            email = norm_email(b.get("email"))
            if not email:
                return self.err(400, "enter a valid email address")
            other = st.find_email(email)
            if other and other != name:
                return self.err(409, "email already registered")
            rec["email"] = email
            return self.send_json(200, st.me_json(name))
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
            if b["kind"] == "full" and not st.is_open(now) and not admin:
                return self.err(403, "the contest is not open for full submissions")
            mine = [s for s in st.subs if s["user"] == name]
            if any(st.timeline(s)[0] in ("queued", "running") for s in mine):
                return self.err(429, "you already have a submission in the queue; wait for it to finish")
            limit = st.setting("full_per_hour")
            recent = [s for s in mine if s["kind"] == "full" and s["created_t"] > now - 3600 and not s["forced_done"]
                      and st.row(s)[0]["status"] != "error"]
            if b["kind"] == "full" and len(recent) >= limit:
                return self.err(429, "at most %d full submissions per hour; try again later" % limit)
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
            sub = st.sub(sid)
            if not sub or (sub["user"] != name and not admin):
                return self.err(404, "no such submission")
            return self.send_json(200, st.detail(sub, admin))
        if path.startswith("/api/admin/"):
            if not admin:
                return self.err(403, "admins only")
            return self.admin_api(method, path, name)
        return self.err(404, "no such endpoint")

    # ------------------------------------------------------------------ Admin API
    def admin_api(self, method, path, me):
        st = self.state
        q = self.query()
        parts = path[len("/api/admin/"):].strip("/").split("/")

        def int_arg(s):
            try:
                return int(s)
            except (TypeError, ValueError):
                return None

        # ---- overview, users
        if method == "GET" and path == "/api/admin/overview":
            return self.send_json(200, st.overview())
        if method == "GET" and path in ("/api/admin/users", "/api/admin/users.csv"):
            live = st.standings_map()
            users = sorted((st.urow(n, live) for n in st.users), key=lambda r: r["id"])
            if path.endswith(".csv"):
                header = ["id", "username", "email", "created", "is_admin", "admin_env", "status", "disabled_at", "disabled_reason", "last_login",
                          "public_submissions", "full_submissions", "best_relative_total", "best_submission_id", "rank", "last_submission"]
                return self.send_file(to_csv(header, users), "text/csv; charset=utf-8", "users.csv")
            summary = {"registered": len(users), "active": sum(1 for r in users if r["status"] == "active"),
                       "disabled": sum(1 for r in users if r["status"] == "disabled"), "admins": sum(1 for r in users if r["is_admin"]),
                       "with_full": sum(1 for r in users if r["full_submissions"])}
            return self.send_json(200, {"users": users, "summary": summary})
        if parts[0] == "users" and len(parts) >= 2:
            uid = int_arg(parts[1])
            target = st.by_id(uid) if uid is not None else None
            if not target:
                return self.err(404, "no such user")
            trec = st.users[target]
            action = parts[2] if len(parts) > 2 else None
            if method == "GET" and action is None:
                out = st.urow(target)
                out["submissions"] = sorted((st.row(s, True)[0] for s in st.subs if s["user"] == target), key=lambda r: -r["id"])
                return self.send_json(200, out)
            last_admin = st.is_admin(target) and st.active(target) and len(st.active_admins()) <= 1
            if method == "POST" and action == "disable":
                b = self.body()
                reason = (b or {}).get("reason") if isinstance(b, dict) else None
                if reason is not None and (not isinstance(reason, str) or len(reason) > 200):
                    return self.err(400, "reason must be at most 200 characters")
                if target == me:
                    return self.err(409, "you can't disable yourself")
                if trec["disabled_at"] is not None:
                    return self.send_json(200, st.urow(target))
                if last_admin:
                    return self.err(409, "%s is the last active admin" % target)
                trec["disabled_at"], trec["disabled_reason"] = time.time(), (reason or "").strip() or None
                for tok in [t for t, n in st.sessions.items() if n == target]:
                    del st.sessions[tok]
                cancelled = 0
                for s in st.subs:
                    if s["user"] == target and st.timeline(s)[0] == "queued":
                        st.cancel(s, CANCELLED_DISABLED)
                        cancelled += 1
                st.log(me, "user.disable", target, {"reason": trec["disabled_reason"], "cancelled": cancelled})
                return self.send_json(200, st.urow(target))
            if method == "POST" and action == "email":
                b = self.body()
                raw = b.get("email") if isinstance(b, dict) else None
                if raw is None or (isinstance(raw, str) and not raw.strip()):
                    email = None
                else:
                    email = norm_email(raw)
                    if not email:
                        return self.err(400, "enter a valid email address")
                    other = st.find_email(email)
                    if other and other != target:
                        return self.err(409, "email already registered")
                if trec["email"] != email:
                    st.log(me, "user.email", target, {"old": mask_email(trec["email"]), "new": mask_email(email)})
                    trec["email"] = email
                return self.send_json(200, st.urow(target))
            if method == "POST" and action == "enable":
                if trec["disabled_at"] is not None:
                    trec["disabled_at"] = trec["disabled_reason"] = None
                    st.log(me, "user.enable", target, {})
                return self.send_json(200, st.urow(target))
            if method == "POST" and action == "password":
                b = self.body()
                pw = b.get("password") if isinstance(b, dict) else None
                if not isinstance(pw, str) or not 8 <= len(pw) <= 256:
                    return self.err(400, "password must be 8-256 characters")
                trec["pw"] = pw
                keep = self.token() if target == me else None
                toks = [t for t, n in st.sessions.items() if n == target and t != keep]
                for t in toks:
                    del st.sessions[t]
                st.log(me, "user.password", target, {"sessions_revoked": len(toks)})
                return self.send_json(200, {"ok": True, "sessions_revoked": len(toks)})
            if method == "POST" and action == "admin":
                b = self.body()
                want = b.get("is_admin") if isinstance(b, dict) else None
                if not isinstance(want, bool):
                    return self.err(400, "send {is_admin: true|false}")
                if not want:
                    if target in ENV_ADMINS:
                        return self.err(409, "listed in PR_ADMINS; edit PR_ADMINS and restart the api")
                    if target == me:
                        return self.err(409, "you can't remove your own admin rights")
                    if last_admin:
                        return self.err(409, "%s is the last active admin" % target)
                if trec["is_admin"] != want:
                    old = st.is_admin(target)
                    trec["is_admin"] = want
                    st.log(me, "user.admin", target, {"is_admin": [old, st.is_admin(target)]})
                return self.send_json(200, st.urow(target))
            if method == "DELETE" and action is None:
                b = self.body()
                conf = b.get("confirm") if isinstance(b, dict) else None
                if not isinstance(conf, str) or conf.strip().lower() != target.lower():
                    return self.err(400, "type the username to confirm")
                if target == me:
                    return self.err(409, "you can't delete yourself")
                if last_admin:
                    return self.err(409, "%s is the last active admin" % target)
                mine = [s for s in st.subs if s["user"] == target]
                if any(st.timeline(s)[0] == "running" for s in mine):
                    return self.err(409, "one of their submissions is running; disable the account, wait for it to finish and retry")
                st.subs = [s for s in st.subs if s["user"] != target]
                st.bump()
                for tok in [t for t, n in st.sessions.items() if n == target]:
                    del st.sessions[tok]
                del st.users[target]
                st.log(me, "user.delete", target, {"submissions": len(mine)})
                return self.send_json(200, {"deleted": target, "submissions": len(mine)})
            return self.err(404, "no such endpoint")

        # ---- submissions
        if method == "GET" and path == "/api/admin/submissions":
            kind, status, verdict = q.get("kind"), q.get("status"), q.get("verdict")
            if kind and kind not in ("public", "full"):
                return self.err(400, "kind must be public or full")
            if status and status not in ("queued", "running", "done", "error"):
                return self.err(400, "status must be queued, running, done or error")
            if verdict and verdict not in ("AC", "WA", "TLE", "RE"):
                return self.err(400, "verdict must be AC, WA, TLE or RE")
            try:
                since, until = parse_iso(q.get("since")), parse_iso(q.get("until"))
            except ValueError:
                return self.err(400, "since/until must be ISO 8601 times")
            limit = int_arg(q.get("limit", "1000"))
            if limit is None or not 1 <= limit <= 5000:
                return self.err(400, "limit must be 1-5000")
            want_user = q.get("user", "").lower()
            out = []
            for s in sorted(st.subs, key=lambda s: -s["id"]):
                if want_user and s["user"].lower() != want_user:
                    continue
                if kind and s["kind"] != kind:
                    continue
                if since is not None and s["created_t"] < since:
                    continue
                if until is not None and s["created_t"] > until:
                    continue
                row, res = st.row(s, True)
                if status and row["status"] != status:
                    continue
                if verdict and not any(r["verdict"] == verdict for r in res):
                    continue
                out.append(row)
                if len(out) >= limit:
                    break
            return self.send_json(200, out)
        if method == "POST" and parts[0] == "rejudge" and len(parts) == 2:
            sub = st.sub(int_arg(parts[1]))
            if not sub:
                return self.err(404, "no such submission")
            if st.timeline(sub)[0] == "running":
                return self.err(409, "it is running; wait for it to finish")
            st.rejudge(sub)
            st.log(me, "submission.rejudge", sub["user"], {"id": sub["id"], "kind": sub["kind"]})
            return self.send_json(200, {"id": sub["id"], "status": "queued"})
        if method == "POST" and path == "/api/admin/rejudge-bulk":
            b = self.body()
            which = b.get("which") if isinstance(b, dict) else None
            if which not in ("errored", "full"):
                return self.err(400, "which must be errored or full")
            ids = []
            for s in st.subs:
                row = st.row(s)[0]
                if s["cancelled"] or not st.active(s["user"]):
                    continue
                if (which == "errored" and row["status"] == "error") or \
                        (which == "full" and s["kind"] == "full" and row["status"] in ("done", "error")):
                    ids.append(s["id"])
            for sid in ids:
                st.rejudge(st.sub(sid))
            st.log(me, "submission.rejudge_bulk", None, {"which": which, "count": len(ids)})
            return self.send_json(200, {"count": len(ids), "ids": ids})
        if parts[0] == "submissions" and len(parts) >= 2 and parts[1] != "submissions.zip":
            sub = st.sub(int_arg(parts[1]))
            if not sub:
                return self.err(404, "no such submission")
            action = parts[2] if len(parts) > 2 else None
            row = st.row(sub, True)[0]
            if method == "DELETE" and action is None:
                if row["status"] == "running":
                    return self.err(409, "it is running; wait for it to finish, then delete it")
                st.subs.remove(sub)
                st.bump()
                st.log(me, "submission.delete", sub["user"], {"id": sub["id"], "kind": sub["kind"], "status": row["status"],
                                                              "raw_total": row["raw_total"], "relative_total": row["relative_total"]})
                return self.send_json(200, {"deleted": sub["id"]})
            if method == "POST" and action == "cancel":
                if row["status"] != "queued":
                    return self.err(409, "only queued submissions can be cancelled")
                st.cancel(sub, CANCELLED_ADMIN)
                st.log(me, "submission.cancel", sub["user"], {"id": sub["id"], "kind": sub["kind"]})
                return self.send_json(200, st.row(sub, True)[0])
            return self.err(404, "no such endpoint")
        if method == "GET" and path == "/api/admin/submissions.zip":
            pick = q.get("pick", "best")
            if pick not in ("best", "latest"):
                return self.err(400, "pick must be best or latest")
            chosen = {}
            for s in st.subs:
                if s["kind"] != "full":
                    continue
                row = st.row(s)[0]
                cur = chosen.get(s["user"])
                if pick == "best":
                    if row["status"] == "done" and (cur is None or row["relative_total"] > cur[1]["relative_total"]):
                        chosen[s["user"]] = (s, row)
                elif cur is None or s["created_t"] > cur[0]["created_t"]:
                    chosen[s["user"]] = (s, row)
            buf = io.BytesIO()
            manifest = []
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for user in sorted(chosen):
                    s, row = chosen[user]
                    z.writestr("%s.py" % user, s["source"])
                    manifest.append({"username": user, "status": row["status"], "submission_id": s["id"], "created": row["created"],
                                     "relative_total": row["relative_total"], "raw_total": row["raw_total"]})
                z.writestr("manifest.csv", to_csv(["username", "status", "submission_id", "created", "relative_total", "raw_total"], manifest))
            return self.send_file(buf.getvalue(), "application/zip", "submissions-%s.zip" % pick)
        if method == "GET" and path == "/api/admin/leaderboard.csv":
            view = q.get("view", "live")
            if view not in ("live", "public"):
                return self.err(400, "view must be live or public")
            cutoff = st.freeze if (view == "public" and st.freeze and time.time() >= st.freeze) else None
            rows = st.standings(cutoff, include_disabled=False)
            for r in rows:
                r["best_submitted"] = r["submitted"]
            header = ["rank", "username", "relative_total", "raw_total", "tests_ac", "submissions", "best_submission_id", "best_submitted"]
            return self.send_file(to_csv(header, rows), "text/csv; charset=utf-8", "leaderboard-%s.csv" % view)
        if method == "GET" and path == "/api/admin/similarity":
            limit = int_arg(q.get("limit", "50"))
            if limit is None or not 1 <= limit <= 500:
                return self.err(400, "limit must be 1-500")
            return self.send_json(200, st.similarity(limit))

        # ---- settings
        if method == "GET" and path == "/api/admin/settings":
            return self.send_json(200, st.settings_json())
        if method == "POST" and path == "/api/admin/settings":
            b = self.body()
            if not isinstance(b, dict):
                return self.err(400, "invalid JSON")
            new, msg = st.validate_settings(b.get("set") or {}, b.get("clear") or [])
            if msg:
                return self.err(400, msg)
            changes = {}
            for k in EDITABLE:
                old, nv = st.setting(k), new.get(k, st.env[k])
                if old != nv:
                    changes[k] = [st.mask(k, old), st.mask(k, nv)]
            st.overrides = new
            if changes:
                st.log(me, "settings.update", None, changes)
            return self.send_json(200, st.settings_json())
        if method == "POST" and path == "/api/admin/settings/invite/regenerate":
            alpha = string.ascii_lowercase + string.digits
            code = "-".join("".join(secrets.choice(alpha) for _ in range(4)) for _ in range(3))
            old = st.setting("invite")
            st.overrides["invite"] = code
            st.log(me, "settings.invite", None, {"invite": [st.mask("invite", old), st.mask("invite", code)]})
            return self.send_json(200, st.settings_json())

        # ---- audit
        if method == "GET" and path == "/api/admin/audit":
            limit = int_arg(q.get("limit", "200"))
            if limit is None or not 1 <= limit <= 2000:
                return self.err(400, "limit must be 1-2000")
            actor, action, target = q.get("actor", "").lower(), q.get("action", ""), q.get("target", "").lower()
            out = []
            for a in reversed(st.audit):
                if actor and (a["actor"] or "").lower() != actor:
                    continue
                if target and (a["target"] or "").lower() != target:
                    continue
                if action and not (a["action"] == action or (action.endswith(".") and a["action"].startswith(action))):
                    continue
                out.append(a)
                if len(out) >= limit:
                    break
            return self.send_json(200, out)
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
    ap.add_argument("--stale-worker", action="store_true", help="the worker heartbeat is minutes old")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if not os.path.isdir(args.dir):
        raise SystemExit("%s does not exist: run platform/site/build.py first" % args.dir)
    Handler.state = State(args)

    def factory(*a, **kw):
        return Handler(*a, directory=args.dir, **kw)

    srv = ThreadingHTTPServer((args.host, args.port), factory)
    print("Poker Run mock on http://%s:%d/  (admin/adminpass, helper/helperpass, demo/demopass%s)" % (
        args.host, args.port, ", invite %r" % args.invite if args.invite else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

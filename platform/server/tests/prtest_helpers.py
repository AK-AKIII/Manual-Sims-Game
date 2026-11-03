"""Shared helpers for the platform tests (local sandbox, temp DB, small seed sets)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parents[1]
for p in (str(SERVER), str(REPO)):
    if p not in sys.path:
        sys.path.append(p)

from fastapi.testclient import TestClient  # noqa: E402

from prplatform import config, db, worker  # noqa: E402
from prplatform.app import create_app  # noqa: E402

STARTER = (REPO / "poker_run" / "starters" / "starter.py").read_text()
HIDDEN = "777001,777002,777003"
PUBLIC = "5,6"

TLE_SOLVER = "while True:\n    pass\n"
RE_SOLVER = "import sys\nsys.stdin.read()\nraise ValueError('boom from the solver')\n"
WA_SOLVER = "import sys\nsys.stdin.read()\nprint(1)\nprint('fly to the moon')\n"
EMPTY_SOLVER = "import sys\nsys.stdin.read()\n"


def make_settings(tmp, **env):
    base = {
        "PR_DB": str(Path(tmp) / "test.db"),
        "PR_HIDDEN_SEEDS": HIDDEN,
        "PR_PUBLIC_SEEDS": PUBLIC,
        "PR_TIME_LIMIT": "1.0",
        "PR_FULL_PER_HOUR": "6",
        "PR_SANDBOX": "local",
        "PR_ADMINS": "boss",
        "PR_SECRET": "test-secret",
    }
    base.update({k: str(v) for k, v in env.items()})
    return config.load(base)


class PlatformTest(unittest.TestCase):
    env = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prtest-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = make_settings(self.tmp, **self.env)
        self.app = create_app(self.settings)
        self.client = self.new_client()
        self.conn = db.connect(self.settings.db)
        self.addCleanup(self.conn.close)

    def new_client(self):
        c = TestClient(self.app)
        c.headers.update({"X-Requested-With": "fetch"})
        return c

    def register(self, username, password="password123", client=None, invite=""):
        client = client or self.client
        r = client.post("/api/register", json={"username": username, "password": password, "invite": invite})
        self.assertEqual(r.status_code, 201, r.text)
        return r

    def user_client(self, username):
        c = self.new_client()
        self.register(username, client=c)
        return c

    def submit(self, kind, source, client=None, expect=202):
        client = client or self.client
        r = client.post("/api/submissions", json={"kind": kind, "source": source})
        self.assertEqual(r.status_code, expect, r.text)
        return r.json().get("id") if expect == 202 else r

    def judge_all(self, on_progress=None):
        return worker.drain(self.conn, self.settings, on_progress=on_progress)

    def detail(self, sid, client=None):
        r = (client or self.client).get(f"/api/submissions/{sid}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def user_id(self, username):
        return self.conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[0]

    def fake_full(self, username, scores, created=None, status="done"):
        """Insert a finished full submission with given per-seed raw scores (hidden seed order)."""
        uid = self.user_id(username)
        created = created or db.now_iso()
        seeds = self.settings.hidden_seeds
        sid = self.conn.execute(
            "INSERT INTO submissions(user_id, kind, source, created, status, finished, raw_total, "
            "cleared_total, max_time, tests_ac, tests_total, tests_done) "
            "VALUES (?, 'full', 'pass', ?, ?, ?, ?, 0, 0.1, ?, ?, ?)",
            (uid, created, status, db.now_iso(), sum(scores), sum(1 for s in scores if s > 0),
             len(seeds), len(seeds))).lastrowid
        for seed, sc in zip(seeds, scores):
            self.conn.execute("INSERT INTO results VALUES (?, ?, ?, ?, 0, 0.1, '')",
                              (sid, seed, "AC" if sc > 0 else "WA", sc))
        return sid

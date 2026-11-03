import unittest
from datetime import timedelta

from prtest_helpers import STARTER, WA_SOLVER, PlatformTest

from prplatform import db, scoring


def iso_offset(**kw):
    return db.iso(db.now() + timedelta(**kw))


class ContestTest(PlatformTest):
    def test_contest_info(self):
        c = self.client.get("/api/contest").json()
        self.assertEqual(set(c), {"name", "start", "end", "freeze", "now", "open", "public_seeds", "hidden_count",
                                  "time_limit", "full_per_hour", "invite_required", "max_source_kb",
                                  "announcement"})
        self.assertIsNone(c["announcement"])
        self.assertEqual(c["name"], "Poker Run")
        self.assertTrue(c["open"])
        self.assertEqual(c["public_seeds"], [5, 6])
        self.assertEqual(c["hidden_count"], 3)
        self.assertEqual(c["time_limit"], 1.0)
        self.assertFalse(c["invite_required"])
        self.assertIsNone(c["start"])

    def test_errors_are_json(self):
        r = self.client.get("/api/nope")
        self.assertEqual((r.status_code, r.json()), (404, {"error": "not found"}))
        r = self.client.get("/api/submissions")
        self.assertEqual((r.status_code, r.json()), (401, {"error": "not logged in"}))
        r = self.client.post("/api/submissions", json={"kind": "public", "source": "x"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/login", content=b"not json", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    def test_submission_validation(self):
        self.register("alice")
        self.submit("weird", "print(1)", expect=400)
        self.submit("public", "   ", expect=400)
        self.submit("public", "x" * (256 * 1024 + 1), expect=413)
        r = self.client.post("/api/submissions", content=b"{" + b" " * (3 << 20) + b"}",
                             headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        self.submit("public", "x" * (256 * 1024))  # exactly at the cap is fine


class RateLimitTest(PlatformTest):
    env = {"PR_FULL_PER_HOUR": "2"}

    def test_one_at_a_time_and_hourly(self):
        self.register("alice")
        self.submit("public", STARTER)
        r = self.submit("public", STARTER, expect=429)
        self.assertIn("error", r.json())
        self.submit("full", STARTER, expect=429)
        self.judge_all()
        self.submit("full", WA_SOLVER)
        self.judge_all()
        self.submit("full", WA_SOLVER)
        self.judge_all()
        r = self.submit("full", WA_SOLVER, expect=429)
        self.assertIn("per hour", r.json()["error"])
        self.submit("public", WA_SOLVER)  # public runs don't count against the hourly limit
        self.judge_all()
        # An hour later the old ones no longer count.
        self.conn.execute("UPDATE submissions SET created = ? WHERE kind = 'full'", (iso_offset(hours=-1, seconds=-5),))
        self.submit("full", WA_SOLVER)


class ClosedContestTest(PlatformTest):
    def setUp(self):
        self.env = {"PR_START": iso_offset(hours=1), "PR_END": iso_offset(hours=2)}
        super().setUp()

    def test_window(self):
        self.assertFalse(self.client.get("/api/contest").json()["open"])
        self.register("alice")
        r = self.submit("full", STARTER, expect=403)
        self.assertIn("not open", r.json()["error"])
        self.submit("public", STARTER)  # public always allowed while logged in
        boss = self.user_client("boss")
        self.submit("full", STARTER, client=boss)  # admins exempt


class EndedContestTest(PlatformTest):
    def setUp(self):
        self.env = {"PR_START": iso_offset(hours=-2), "PR_END": iso_offset(seconds=-1)}
        super().setUp()

    def test_after_end(self):
        self.register("alice")
        self.submit("full", STARTER, expect=403)


class LeaderboardTest(PlatformTest):
    def setUp(self):
        super().setUp()
        for u in ("alice", "bob", "carol", "dave"):
            self.register(u, client=self.new_client())

    def test_relative_scores_and_ties(self):
        t0 = iso_offset(minutes=-30)
        t1 = iso_offset(minutes=-20)
        t2 = iso_offset(minutes=-10)
        # seeds: s1, s2, s3
        a1 = self.fake_full("alice", [100, 50, 0], created=t0)
        self.fake_full("alice", [10, 10, 0], created=t2)         # worse; not her best
        b1 = self.fake_full("bob", [50, 100, 0], created=t1)
        c1 = self.fake_full("carol", [100, 50, 0], created=t2)  # ties alice but later
        self.fake_full("dave", [999, 999, 999], created=t0, status="error")  # not finished: ignored
        lb = self.client.get("/api/leaderboard").json()
        self.assertFalse(lb["frozen"])
        rows = lb["rows"]
        self.assertEqual([r["username"] for r in rows], ["alice", "bob", "carol"])
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3])
        # alice: 1e6 + 5e5 + 0 (best on s3 is 0 -> 0); bob: 5e5 + 1e6; carol same as alice.
        self.assertEqual([r["relative_total"] for r in rows], [1_500_000] * 3)
        self.assertEqual([r["score"] for r in rows], [50.0] * 3)  # 100 * mean(1, 0.5, 0)
        self.assertEqual(rows[0]["raw_total"], 150)
        self.assertEqual(rows[0]["submissions"], 2)
        self.assertEqual(rows[0]["submitted"], t0)
        self.assertEqual(rows[0]["tests_ac"], 2)
        # Rounding: 1e6 * 1/3.
        self.assertEqual(scoring.relative(1, 3), 333333)
        self.assertEqual(scoring.relative(2, 3), 666667)
        self.assertEqual(scoring.relative(5, 0), 0)

        # Bob improves seed 3: everyone else's s3 stays 0, his gets 1e6; s1 best still 100.
        self.fake_full("bob", [50, 100, 7])
        rows = self.client.get("/api/leaderboard").json()["rows"]
        self.assertEqual(rows[0]["username"], "bob")
        self.assertEqual(rows[0]["relative_total"], 2_500_000)
        # The relative total in a user's own list follows the live bests.
        c = self.new_client()
        c.post("/api/login", json={"username": "alice", "password": "password123"})
        mine = {s["id"]: s["relative_total"] for s in c.get("/api/submissions").json()}
        self.assertEqual(mine[a1], 1_500_000)
        self.assertNotIn(b1, mine)
        self.assertNotIn(c1, mine)


class NormalisedScoreTest(PlatformTest):
    """score = 100 * mean over hidden tests of raw / best, 2 decimals (3 hidden tests here)."""

    def setUp(self):
        super().setUp()
        for u in ("alice", "bob", "carol", "dave"):
            self.register(u, client=self.new_client())

    def rows(self):
        return [(r["username"], r["score"]) for r in self.client.get("/api/leaderboard").json()["rows"]]

    def test_single_full_is_100(self):
        self.fake_full("alice", [37, 5, 900])
        self.assertEqual(self.rows(), [("alice", 100.0)])

    def test_half_zero_and_ranking(self):
        t = iso_offset(minutes=-10)
        self.fake_full("alice", [100, 40, 8], created=t)
        self.fake_full("bob", [50, 20, 4], created=t)     # half the best on every test
        self.fake_full("carol", [0, 0, 0], created=t)     # nothing
        self.fake_full("dave", [100, 0, 0], created=t)    # 100 * (1 + 0 + 0) / 3
        lb = self.client.get("/api/leaderboard").json()["rows"]
        self.assertEqual([(r["username"], r["score"]) for r in lb],
                         [("alice", 100.0), ("bob", 50.0), ("dave", 33.33), ("carol", 0.0)])
        # Ranking is by relative total, and score is a monotonic rescaling of it.
        self.assertEqual([r["rank"] for r in lb], [1, 2, 3, 4])
        self.assertEqual(sorted(lb, key=lambda r: -r["relative_total"]), lb)
        self.assertEqual([r["score"] for r in lb], sorted((r["score"] for r in lb), reverse=True))

    def test_best_zero_test_contributes_zero(self):
        self.fake_full("alice", [10, 10, 0])
        self.assertEqual(self.rows(), [("alice", 66.67)])

    def test_helpers(self):
        self.assertEqual(scoring.normalised(50 * 1_000_000, 50), 100.0)
        self.assertEqual(scoring.normalised(23_655_000, 50), 47.31)
        self.assertEqual(scoring.normalised(0, 50), 0.0)
        self.assertIsNone(scoring.normalised(None, 50))
        self.assertEqual(scoring.normalised(5, 0), 0.0)
        self.assertEqual(scoring.relative_pct(1, 3), 33.33)
        self.assertEqual(scoring.relative_pct(4, 0), 0.0)

    def test_own_submissions_have_score(self):
        c = self.new_client()
        c.post("/api/login", json={"username": "alice", "password": "password123"})
        self.fake_full("alice", [100, 100, 100])
        self.fake_full("bob", [200, 50, 100])
        mine = c.get("/api/submissions").json()
        self.assertEqual([s["score"] for s in mine], [round(100 * (0.5 + 1 + 1) / 3, 2)])


class FreezeTest(PlatformTest):
    def setUp(self):
        self.env = {"PR_FREEZE": iso_offset(minutes=-5)}
        super().setUp()
        self.alice = self.user_client("alice")
        self.bob = self.user_client("bob")
        self.boss = self.user_client("boss")

    def test_freeze(self):
        self.fake_full("alice", [100, 100, 100], created=iso_offset(minutes=-10))
        self.fake_full("bob", [50, 50, 50], created=iso_offset(minutes=-10))
        late = self.fake_full("bob", [200, 200, 200], created=iso_offset(minutes=-1))  # after the freeze
        public = self.client.get("/api/leaderboard").json()
        self.assertTrue(public["frozen"])
        self.assertEqual([(r["username"], r["relative_total"]) for r in public["rows"]],
                         [("alice", 3_000_000), ("bob", 1_500_000)])
        self.assertEqual(public["rows"][1]["submissions"], 1)
        admin = self.boss.get("/api/leaderboard").json()
        self.assertFalse(admin["frozen"])
        self.assertEqual([(r["username"], r["relative_total"]) for r in admin["rows"]],
                         [("bob", 3_000_000), ("alice", 1_500_000)])
        # Bob sees his own late submission scored against pre-freeze bests plus his own.
        mine = {s["id"]: s["relative_total"] for s in self.bob.get("/api/submissions").json()}
        self.assertEqual(mine[late], 3_000_000)
        # Alice can't learn about bob's late submission from her own relative total.
        alice_rel = self.alice.get("/api/submissions").json()[0]["relative_total"]
        self.assertEqual(alice_rel, 3_000_000)
        self.assertTrue(self.alice.get("/api/leaderboard").json()["frozen"])


class AdminTest(PlatformTest):
    def test_admin_endpoints_and_rejudge(self):
        alice = self.user_client("alice")
        boss = self.user_client("boss")
        sid = self.submit("public", WA_SOLVER, client=alice)
        self.judge_all()
        self.assertEqual(alice.get("/api/admin/submissions").status_code, 403)
        self.assertEqual(alice.post(f"/api/admin/rejudge/{sid}").status_code, 403)
        self.assertEqual(self.new_client().get("/api/admin/submissions").status_code, 401)
        rows = boss.get("/api/admin/submissions").json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "alice")
        self.assertEqual(rows[0]["status"], "done")
        # Admins can read anyone's submission.
        self.assertEqual(self.detail(sid, client=boss)["source"], WA_SOLVER)
        self.assertEqual(boss.post("/api/admin/rejudge/99999").status_code, 404)

        # Rejudge after "fixing" the source in the DB: back to queued, results cleared.
        self.conn.execute("UPDATE submissions SET source = ? WHERE id = ?", (STARTER, sid))
        r = boss.post(f"/api/admin/rejudge/{sid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"id": sid, "status": "queued"})
        d = self.detail(sid, client=alice)
        self.assertEqual((d["status"], d["results"], d["tests_done"], d["position"]), ("queued", [], 0, 1))
        self.judge_all()
        d = self.detail(sid, client=alice)
        self.assertEqual([x["verdict"] for x in d["results"]], ["AC", "AC"])

        # A running submission can't be rejudged.
        sid2 = self.submit("public", STARTER, client=alice)
        from prplatform import worker
        self.assertEqual(worker.claim(self.conn), sid2)
        self.assertEqual(boss.post(f"/api/admin/rejudge/{sid2}").status_code, 409)

    def test_admin_via_db_flag(self):
        alice = self.user_client("alice")
        self.assertFalse(alice.get("/api/me").json()["is_admin"])
        self.conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'alice'")
        self.assertTrue(alice.get("/api/me").json()["is_admin"])
        self.assertEqual(alice.get("/api/admin/submissions").status_code, 200)


class ConnectionThreadTest(unittest.TestCase):
    def test_connection_usable_from_another_thread(self):
        # Under uvicorn, FastAPI opens a request's connection in one threadpool thread
        # and runs the dependencies and endpoint that use it in others.
        import threading
        conn = db.connect(":memory:")
        out = []
        t = threading.Thread(target=lambda: out.append(conn.execute("SELECT 1").fetchone()[0]))
        t.start(); t.join()
        conn.close()
        self.assertEqual(out, [1])


if __name__ == "__main__":
    unittest.main()

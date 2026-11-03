"""Admin panel API: users, submissions, settings, audit, similarity, overview,
email accounts, the CLI and the migration of a database made by the old schema."""

import csv
import io
import json
import os
import sqlite3
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from prtest_helpers import STARTER, WA_SOLVER, PlatformTest, make_settings

from prplatform import admin as admin_ops
from prplatform import auth, db, manage, similarity, worker
from prplatform.app import create_app


def iso_offset(**kw):
    return db.iso(db.now() + timedelta(**kw))


class AdminBase(PlatformTest):
    def setUp(self):
        super().setUp()
        self.boss = self.user_client("boss")  # admin via PR_ADMINS

    def uid(self, name):
        return self.user_id(name)

    def login(self, name, password="password123", expect=200):
        c = self.new_client()
        r = c.post("/api/login", json={"username": name, "password": password})
        self.assertEqual(r.status_code, expect, r.text)
        return c

    def users(self, client=None):
        r = (client or self.boss).get("/api/admin/users")
        self.assertEqual(r.status_code, 200, r.text)
        return {u["username"]: u for u in r.json()["users"]}

    def board(self, client=None):
        return [(r["username"], r["rank"], r["relative_total"], r.get("disabled", False))
                for r in (client or self.client).get("/api/leaderboard").json()["rows"]]

    def audit(self, **q):
        return self.boss.get("/api/admin/audit", params=q).json()


class PermissionTest(AdminBase):
    ENDPOINTS = [
        ("GET", "/api/admin/overview"), ("GET", "/api/admin/users"), ("GET", "/api/admin/users.csv"),
        ("GET", "/api/admin/users/1"), ("POST", "/api/admin/users/1/disable"), ("POST", "/api/admin/users/1/enable"),
        ("POST", "/api/admin/users/1/password"), ("POST", "/api/admin/users/1/admin"),
        ("POST", "/api/admin/users/1/email"), ("DELETE", "/api/admin/users/1"),
        ("GET", "/api/admin/submissions"), ("DELETE", "/api/admin/submissions/1"),
        ("POST", "/api/admin/submissions/1/cancel"), ("POST", "/api/admin/rejudge/1"),
        ("POST", "/api/admin/rejudge-bulk"), ("POST", "/api/admin/submissions/1/run-full"),
        ("POST", "/api/admin/run-full-bulk"), ("GET", "/api/admin/submissions.zip"),
        ("GET", "/api/admin/leaderboard.csv"), ("GET", "/api/admin/similarity"), ("GET", "/api/admin/settings"),
        ("POST", "/api/admin/settings"), ("POST", "/api/admin/settings/invite/regenerate"),
        ("GET", "/api/admin/audit"),
    ]

    def test_logged_out_401_student_403(self):
        alice = self.user_client("alice")
        anon = self.new_client()
        for method, path in self.ENDPOINTS:
            r = anon.request(method, path, json={})
            self.assertEqual(r.status_code, 401, (method, path, r.text))
            self.assertIn("error", r.json())
            r = alice.request(method, path, json={})
            self.assertEqual(r.status_code, 403, (method, path, r.text))
            self.assertEqual(r.json(), {"error": "admins only"})

    def test_csrf_required(self):
        self.user_client("alice")
        alice_id = self.uid("alice")
        c = self.new_client()
        del c.headers["X-Requested-With"]
        c.cookies.set("pr_session", self.boss.cookies.get("pr_session"))
        for method, path in self.ENDPOINTS:
            if method == "GET":
                continue
            r = c.request(method, path.replace("/1", f"/{alice_id}"), json={})
            self.assertEqual(r.status_code, 403, (method, path))
            self.assertIn("X-Requested-With", r.json()["error"])
        self.assertEqual(self.users()["alice"]["status"], "active")

    def test_unknown_ids_404(self):
        for method, path, body in [("GET", "/api/admin/users/999", None), ("POST", "/api/admin/users/999/disable", {}),
                                   ("POST", "/api/admin/users/999/enable", {}),
                                   ("POST", "/api/admin/users/999/password", {"password": "password999"}),
                                   ("POST", "/api/admin/users/999/admin", {"is_admin": True}),
                                   ("POST", "/api/admin/users/999/email", {"email": "a@b.co"}),
                                   ("DELETE", "/api/admin/users/999", {"confirm": "x"}),
                                   ("DELETE", "/api/admin/submissions/999", None),
                                   ("POST", "/api/admin/submissions/999/cancel", None),
                                   ("POST", "/api/admin/rejudge/999", None),
                                   ("POST", "/api/admin/submissions/999/run-full", None)]:
            r = self.boss.request(method, path, json=body)
            self.assertEqual(r.status_code, 404, (method, path, r.text))
            self.assertIn("not found", r.json()["error"])

    def test_bad_input_400(self):
        aid = self.uid("boss")
        self.user_client("alice")
        a = self.uid("alice")
        self.assertEqual(self.boss.post(f"/api/admin/users/{a}/admin", json={"is_admin": "yes"}).status_code, 400)
        self.assertEqual(self.boss.post(f"/api/admin/users/{a}/disable", json={"reason": "x" * 201}).status_code, 400)
        self.assertEqual(self.boss.post("/api/admin/rejudge-bulk", json={"which": "all"}).status_code, 400)
        self.assertEqual(self.boss.post("/api/admin/run-full-bulk", json={"which": "all"}).status_code, 400)
        for q in ({"kind": "x"}, {"status": "x"}, {"verdict": "OK"}, {"since": "yesterday"}):
            self.assertEqual(self.boss.get("/api/admin/submissions", params=q).status_code, 400, q)
        self.assertEqual(self.boss.get("/api/admin/submissions.zip?pick=worst").status_code, 400)
        self.assertEqual(self.boss.get("/api/admin/leaderboard.csv?view=x").status_code, 400)
        self.assertEqual(self.boss.get(f"/api/admin/users/{aid}").status_code, 200)


class UsersListTest(AdminBase):
    def test_rows_summary_and_rank(self):
        alice, bob = self.user_client("alice"), self.user_client("bob")
        self.user_client("carol")
        self.fake_full("alice", [100, 100, 100], created=iso_offset(minutes=-5))
        self.fake_full("alice", [10, 10, 10])
        self.fake_full("bob", [50, 50, 50])
        self.submit("public", STARTER, client=bob)
        u = self.users()
        self.assertEqual(set(u["alice"]), {
            "id", "username", "email", "created", "is_admin", "admin_env", "status", "disabled_at", "disabled_reason",
            "last_login", "public_submissions", "full_submissions", "best_score", "best_relative_total",
            "best_submission_id", "rank", "last_submission"})
        self.assertEqual((u["alice"]["rank"], u["alice"]["best_relative_total"], u["alice"]["full_submissions"]),
                         (1, 3_000_000, 2))
        self.assertEqual((u["alice"]["best_score"], u["bob"]["best_score"]), (100.0, 50.0))
        self.assertIsNone(u["carol"]["best_score"])
        self.assertEqual((u["bob"]["rank"], u["bob"]["public_submissions"], u["bob"]["full_submissions"]), (2, 1, 1))
        self.assertIsNone(u["carol"]["rank"])
        self.assertIsNone(u["carol"]["best_relative_total"])
        self.assertIsNone(u["carol"]["last_submission"])
        self.assertTrue(u["boss"]["is_admin"] and u["boss"]["admin_env"])
        self.assertEqual(u["alice"]["email"], "alice@example.com")
        self.assertIsNotNone(u["alice"]["last_login"])
        lb = {r["username"]: r["rank"] for r in self.client.get("/api/leaderboard").json()["rows"]}
        self.assertEqual(lb, {"alice": 1, "bob": 2})
        summary = self.boss.get("/api/admin/users").json()["summary"]
        self.assertEqual(summary, {"registered": 4, "active": 4, "disabled": 0, "admins": 1, "with_full": 2})
        # last_login moves on login
        self.conn.execute("UPDATE users SET last_login = NULL WHERE username = 'alice'")
        self.login("alice")
        self.assertIsNotNone(self.users()["alice"]["last_login"])
        self.assertIsNotNone(alice)

    def test_detail(self):
        alice = self.user_client("alice")
        sid = self.submit("public", WA_SOLVER, client=alice)
        self.judge_all()
        d = self.boss.get(f"/api/admin/users/{self.uid('alice')}").json()
        self.assertEqual(d["username"], "alice")
        self.assertEqual([s["id"] for s in d["submissions"]], [sid])
        self.assertEqual(d["submissions"][0]["username"], "alice")
        self.assertFalse(d["submissions"][0]["user_disabled"])

    def test_csv_export_and_formula_guard(self):
        self.user_client("-cmd")
        self.user_client("alice")
        self.fake_full("alice", [1, 2, 3])
        r = self.boss.get("/api/admin/users.csv")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        self.assertRegex(r.headers["content-disposition"], r'^attachment; filename="poker-run-students-[\d-]+\.csv"$')
        rows = list(csv.DictReader(io.StringIO(r.text)))
        names = [x["username"] for x in rows]
        self.assertIn("'-cmd", names)
        self.assertNotIn("-cmd", names)
        alice = next(x for x in rows if x["username"] == "alice")
        self.assertEqual((alice["rank"], alice["status"], alice["email"]), ("1", "active", "alice@example.com"))
        self.assertEqual((alice["best_score"], alice["best_relative_total"]), ("100.00", "3000000"))
        from prplatform.app import csv_cell
        for bad in ("=1+1", "+x", "-x", "@x", "\tx", "\rx"):
            self.assertEqual(csv_cell(bad), "'" + bad)
        self.assertEqual(csv_cell(-5), -5)
        self.assertEqual(csv_cell("ok"), "ok")


class DisableTest(AdminBase):
    def test_disable_semantics(self):
        alice = self.user_client("alice")
        other_session = self.login("alice")
        self.user_client("bob")
        a, b = self.uid("alice"), self.uid("bob")
        self.fake_full("alice", [100, 100, 100], created=iso_offset(minutes=-5))
        self.fake_full("bob", [50, 50, 50], created=iso_offset(minutes=-4))
        queued = self.submit("public", STARTER, client=alice)
        self.assertEqual(self.board(), [("alice", 1, 3_000_000, False), ("bob", 2, 1_500_000, False)])

        r = self.boss.post(f"/api/admin/users/{a}/disable", json={"reason": "copied code"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["status"], r.json()["disabled_reason"], r.json()["rank"]),
                         ("disabled", "copied code", None))
        # sessions revoked, can't log in (403 with right password, 401 with wrong), can't submit
        self.assertEqual(alice.get("/api/me").status_code, 401)
        self.assertEqual(other_session.get("/api/me").status_code, 401)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (a,)).fetchone()[0], 0)
        r = self.new_client().post("/api/login", json={"username": "alice", "password": "password123"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("disabled", r.json()["error"])
        self.assertEqual(self.new_client().post("/api/login", json={"username": "alice", "password": "nope12345"})
                         .status_code, 401)
        self.assertEqual(alice.post("/api/submissions", json={"kind": "public", "source": STARTER}).status_code, 401)
        # a leftover session row (e.g. created concurrently) is still rejected
        token = auth.create_session(self.conn, a)
        c = self.new_client()
        c.cookies.set("pr_session", token)
        self.assertEqual(c.get("/api/me").status_code, 401)
        # queued submission cancelled
        st = self.conn.execute("SELECT status, error FROM submissions WHERE id = ?", (queued,)).fetchone()
        self.assertEqual(tuple(st), ("error", admin_ops.CANCELLED_DISABLED))
        self.assertEqual(self.judge_all(), [])
        # leaderboard: hidden publicly, bests recomputed without her
        self.assertEqual(self.board(), [("bob", 1, 3_000_000, False)])
        self.assertEqual(self.board(self.boss), [("bob", 1, 3_000_000, False), ("alice", None, 6_000_000, True)])
        bob = self.login("bob")
        self.assertEqual(bob.get("/api/submissions").json()[0]["relative_total"], 3_000_000)
        # disabling again is a no-op
        self.assertEqual(self.boss.post(f"/api/admin/users/{a}/disable").status_code, 200)
        self.assertEqual(len(self.audit(action="user.disable")), 1)
        entry = self.audit(action="user.disable")[0]
        self.assertEqual((entry["actor"], entry["target"]), ("boss", "alice"))
        self.assertEqual(entry["details"]["cancelled"], [queued])
        self.assertEqual(entry["details"]["sessions_revoked"], 2)

        # enable restores everything
        r = self.boss.post(f"/api/admin/users/{a}/enable")
        self.assertEqual(r.json()["status"], "active")
        self.assertEqual(self.board(), [("alice", 1, 3_000_000, False), ("bob", 2, 1_500_000, False)])
        self.login("alice")
        self.assertEqual(self.audit()[0]["action"], "user.enable")
        self.assertEqual(b, self.uid("bob"))

    def test_running_submission_left_to_finish(self):
        alice = self.user_client("alice")
        sid = self.submit("full", STARTER, client=alice)
        self.assertEqual(worker.claim(self.conn), sid)
        self.boss.post(f"/api/admin/users/{self.uid('alice')}/disable")
        self.assertEqual(self.conn.execute("SELECT status FROM submissions WHERE id = ?", (sid,)).fetchone()[0],
                         "running")
        worker.process(self.conn, self.settings, sid)
        self.assertEqual(self.conn.execute("SELECT status FROM submissions WHERE id = ?", (sid,)).fetchone()[0], "done")
        self.assertEqual(self.board(), [])
        self.assertEqual(self.audit(action="user.disable")[0]["details"]["running_left"], 1)


class SafetyRuleTest(AdminBase):
    def test_self_actions_refused(self):
        me = self.uid("boss")
        for method, path, body in [("POST", f"/api/admin/users/{me}/disable", {}),
                                   ("DELETE", f"/api/admin/users/{me}", {"confirm": "boss"}),
                                   ("POST", f"/api/admin/users/{me}/admin", {"is_admin": False})]:
            r = self.boss.request(method, path, json=body)
            self.assertEqual(r.status_code, 409, (path, r.text))
            self.assertIn("error", r.json())
        self.assertEqual(self.users()["boss"]["status"], "active")

    def test_pr_admins_user_cannot_be_deadmined(self):
        helper = self.user_client("helper")
        h = self.uid("helper")
        self.assertEqual(self.boss.post(f"/api/admin/users/{h}/admin", json={"is_admin": True}).status_code, 200)
        self.assertTrue(helper.get("/api/me").json()["is_admin"])
        r = helper.post(f"/api/admin/users/{self.uid('boss')}/admin", json={"is_admin": False})
        self.assertEqual(r.status_code, 409)
        self.assertIn("PR_ADMINS", r.json()["error"])
        # ...but the DB admin can be removed by the env admin
        r = self.boss.post(f"/api/admin/users/{h}/admin", json={"is_admin": False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["is_admin"])
        self.assertEqual(helper.get("/api/admin/users").status_code, 403)
        self.assertEqual([e["details"] for e in self.audit(action="user.admin")],
                         [{"is_admin": False}, {"is_admin": True}])

    def test_admin_can_disable_another_admin(self):
        helper = self.user_client("helper")
        h = self.uid("helper")
        self.boss.post(f"/api/admin/users/{h}/admin", json={"is_admin": True})
        r = helper.post(f"/api/admin/users/{self.uid('boss')}/disable")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.boss.get("/api/me").status_code, 401)

    def test_last_admin_rule(self):
        # Through the API the self-rule fires first; the last-admin rule protects the CLI path.
        b = self.uid("boss")
        for fn in (lambda: admin_ops.disable(self.conn, self.settings, admin_ops.CLI_ACTOR, b),
                   lambda: admin_ops.delete_user(self.conn, self.settings, admin_ops.CLI_ACTOR, b, "boss")):
            with self.assertRaises(admin_ops.AdminError) as cm:
                fn()
            self.assertEqual(cm.exception.status, 409)
            self.assertIn("last active admin", cm.exception.message)
        self.user_client("helper")
        h = self.uid("helper")
        self.conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (h,))
        admin_ops.disable(self.conn, self.settings, admin_ops.CLI_ACTOR, b)  # helper remains
        with self.assertRaises(admin_ops.AdminError):
            admin_ops.set_admin(self.conn, self.settings, admin_ops.CLI_ACTOR, h, False)


class DeleteUserTest(AdminBase):
    def test_delete_cascade_and_audit(self):
        alice = self.user_client("alice")
        self.user_client("bob")
        a = self.uid("alice")
        self.fake_full("alice", [100, 100, 100])
        self.fake_full("bob", [50, 50, 50])
        self.submit("public", WA_SOLVER, client=alice)
        self.judge_all()
        r = self.boss.request("DELETE", f"/api/admin/users/{a}", json={"confirm": "bob"})
        self.assertEqual(r.status_code, 400)
        r = self.boss.request("DELETE", f"/api/admin/users/{a}", json={})
        self.assertEqual(r.status_code, 400)
        r = self.boss.request("DELETE", f"/api/admin/users/{a}", json={"confirm": "ALICE"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"deleted": "alice", "submissions": 2})
        for table, col in (("users", "id"), ("sessions", "user_id"), ("submissions", "user_id")):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (a,)).fetchone()[0], 0)
        orphan = self.conn.execute("SELECT COUNT(*) FROM results WHERE submission_id NOT IN "
                                   "(SELECT id FROM submissions)").fetchone()[0]
        self.assertEqual(orphan, 0)
        self.assertEqual(alice.get("/api/me").status_code, 401)
        self.assertEqual(self.board(), [("bob", 1, 3_000_000, False)])
        entry = self.audit(target="alice")[0]
        self.assertEqual((entry["action"], entry["details"]["submissions"]), ("user.delete", 2))
        self.assertEqual(self.boss.get(f"/api/admin/users/{a}").status_code, 404)
        # the name is free again
        self.user_client("alice")

    def test_delete_refused_while_running(self):
        alice = self.user_client("alice")
        sid = self.submit("public", STARTER, client=alice)
        worker.claim(self.conn)
        r = self.boss.request("DELETE", f"/api/admin/users/{self.uid('alice')}", json={"confirm": "alice"})
        self.assertEqual(r.status_code, 409)
        worker.process(self.conn, self.settings, sid)
        r = self.boss.request("DELETE", f"/api/admin/users/{self.uid('alice')}", json={"confirm": "alice"})
        self.assertEqual(r.status_code, 200)


class PasswordResetTest(AdminBase):
    def test_reset(self):
        alice = self.user_client("alice")
        a = self.uid("alice")
        r = self.boss.post(f"/api/admin/users/{a}/password", json={"password": "short"})
        self.assertEqual(r.status_code, 400)
        r = self.boss.post(f"/api/admin/users/{a}/password", json={"password": "brand-new-pass"})
        self.assertEqual(r.json(), {"ok": True, "sessions_revoked": 1})
        self.assertEqual(alice.get("/api/me").status_code, 401)
        self.login("alice", expect=401)
        self.login("alice", "brand-new-pass")
        e = self.audit(action="user.password")[0]
        self.assertEqual((e["target"], e["details"]), ("alice", {"sessions_revoked": 1}))
        self.assertNotIn("brand-new-pass", json.dumps(self.audit()))

    def test_own_reset_keeps_current_session(self):
        other = self.login("boss")
        r = self.boss.post(f"/api/admin/users/{self.uid('boss')}/password", json={"password": "another-pass1"})
        self.assertEqual(r.json()["sessions_revoked"], 1)
        self.assertEqual(self.boss.get("/api/me").status_code, 200)
        self.assertEqual(other.get("/api/me").status_code, 401)


class SubmissionAdminTest(AdminBase):
    def setUp(self):
        super().setUp()
        for u in ("alice", "bob"):
            self.user_client(u)

    def test_delete_best_recomputes(self):
        a1 = self.fake_full("alice", [100, 100, 100], created=iso_offset(minutes=-9))
        self.fake_full("alice", [80, 80, 80], created=iso_offset(minutes=-8))
        self.fake_full("bob", [50, 50, 50], created=iso_offset(minutes=-7))
        self.assertEqual(self.board(), [("alice", 1, 3_000_000, False), ("bob", 2, 1_500_000, False)])
        r = self.boss.delete(f"/api/admin/submissions/{a1}")
        self.assertEqual((r.status_code, r.json()), (200, {"deleted": a1}))
        # alice falls back to her next best; the per-seed best is now 80, so bob gains.
        self.assertEqual(self.board(), [("alice", 1, 3_000_000, False), ("bob", 2, 1_875_000, False)])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM results WHERE submission_id = ?", (a1,)).fetchone()[0], 0)
        e = self.audit(action="submission.delete")[0]
        self.assertEqual(e["target"], "alice")
        self.assertEqual({k: e["details"][k] for k in ("submission_id", "kind", "raw_total", "relative_total")},
                         {"submission_id": a1, "kind": "full", "raw_total": 300, "relative_total": 3_000_000})
        self.assertEqual(self.boss.delete(f"/api/admin/submissions/{a1}").status_code, 404)

    def test_delete_queued_and_running(self):
        alice = self.login("alice")
        q = self.submit("public", STARTER, client=alice)
        self.assertEqual(self.boss.delete(f"/api/admin/submissions/{q}").status_code, 200)
        self.assertEqual(self.judge_all(), [])
        q2 = self.submit("public", STARTER, client=alice)  # no longer blocked by the deleted one
        worker.claim(self.conn)
        r = self.boss.delete(f"/api/admin/submissions/{q2}")
        self.assertEqual(r.status_code, 409)
        self.assertIn("being judged", r.json()["error"])

    def test_cancel(self):
        alice = self.login("alice")
        q = self.submit("public", STARTER, client=alice)
        r = self.boss.post(f"/api/admin/submissions/{q}/cancel")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["status"], r.json()["error"]), ("error", admin_ops.CANCELLED_ADMIN))
        self.assertEqual(self.boss.post(f"/api/admin/submissions/{q}/cancel").status_code, 409)
        self.assertEqual(self.audit()[0]["action"], "submission.cancel")

    def test_rejudge_audited_and_bulk(self):
        bob = self.login("bob")
        e1 = self.fake_full("alice", [1, 1, 1], status="error")
        f1 = self.fake_full("alice", [5, 5, 5])
        cancelled = self.fake_full("bob", [0, 0, 0], status="error")
        self.conn.execute("UPDATE submissions SET error = ? WHERE id = ?", (admin_ops.CANCELLED_ADMIN, cancelled))
        self.submit("public", WA_SOLVER, client=bob)
        self.judge_all()
        r = self.boss.post(f"/api/admin/rejudge/{f1}")
        self.assertEqual(r.json(), {"id": f1, "status": "queued"})
        self.assertEqual(self.audit()[0]["action"], "submission.rejudge")
        self.judge_all()
        r = self.boss.post("/api/admin/rejudge-bulk", json={"which": "errored"})
        self.assertEqual(r.json(), {"count": 1, "ids": [e1]})
        self.judge_all()
        r = self.boss.post("/api/admin/rejudge-bulk", json={"which": "full"})
        self.assertEqual(sorted(r.json()["ids"]), sorted([e1, f1]))
        # disabled users are skipped
        self.judge_all()
        self.boss.post(f"/api/admin/users/{self.uid('alice')}/disable")
        self.assertEqual(self.boss.post("/api/admin/rejudge-bulk", json={"which": "full"}).json()["count"], 0)
        e = self.audit(action="submission.rejudge_bulk")
        self.assertEqual([x["details"]["which"] for x in e], ["full", "full", "errored"])

    def test_filters(self):
        alice = self.login("alice")
        p = self.submit("public", WA_SOLVER, client=alice)
        self.judge_all()
        f = self.fake_full("bob", [5, 0, 5], created=iso_offset(days=-2))
        get = lambda **q: [s["id"] for s in self.boss.get("/api/admin/submissions", params=q).json()]  # noqa: E731
        self.assertEqual(get(), [f, p])
        self.assertEqual(get(user="ALICE"), [p])
        self.assertEqual(get(kind="full"), [f])
        self.assertEqual(get(status="done", kind="public"), [p])
        self.assertEqual(get(verdict="WA"), [f, p])
        self.assertEqual(get(verdict="AC"), [f])
        self.assertEqual(get(since=iso_offset(days=-1)), [p])
        self.assertEqual(get(until=iso_offset(days=-1)), [f])
        row = self.boss.get("/api/admin/submissions").json()[0]
        self.assertEqual((row["username"], row["user_id"], row["user_disabled"]), ("bob", self.uid("bob"), False))

    def test_zip_best_and_latest(self):
        best = self.fake_full("alice", [100, 100, 100])
        self.conn.execute("UPDATE submissions SET source = 'print(\"best\")' WHERE id = ?", (best,))
        latest = self.fake_full("alice", [1, 1, 1])
        self.conn.execute("UPDATE submissions SET source = 'print(\"latest\")' WHERE id = ?", (latest,))
        self.fake_full("bob", [50, 50, 50])
        for pick, want in (("best", 'print("best")'), ("latest", 'print("latest")')):
            r = self.boss.get(f"/api/admin/submissions.zip?pick={pick}")
            self.assertEqual(r.headers["content-type"], "application/zip")
            self.assertIn("attachment;", r.headers["content-disposition"])
            z = zipfile.ZipFile(io.BytesIO(r.content))
            self.assertEqual(sorted(z.namelist()), ["alice.py", "bob.py", "manifest.csv"])
            self.assertEqual(z.read("alice.py").decode(), want)
            manifest = list(csv.DictReader(io.StringIO(z.read("manifest.csv").decode())))
            self.assertEqual([m["username"] for m in manifest], ["alice", "bob"])
            self.assertEqual(manifest[0]["submission_id"], str(best if pick == "best" else latest))
            self.assertIn("score", manifest[0])
            if manifest[0]["status"] == "done":
                self.assertRegex(manifest[0]["score"], r"^\d{1,3}\.\d\d$")

    def test_leaderboard_csv(self):
        self.fake_full("alice", [100, 100, 100])
        b = self.fake_full("bob", [50, 50, 50])
        r = self.boss.get("/api/admin/leaderboard.csv")
        rows = list(csv.reader(io.StringIO(r.text)))
        self.assertEqual(rows[0], ["rank", "username", "score", "relative_total", "raw_total", "tests_ac",
                                   "submissions", "best_submission_id", "best_submitted"])
        self.assertEqual(rows[1][:3], ["1", "alice", "100.00"])
        self.assertEqual(rows[2][:8], ["2", "bob", "50.00", "1500000", "150", "3", "1", str(b)])
        self.boss.post(f"/api/admin/users/{self.uid('alice')}/disable")
        rows = list(csv.reader(io.StringIO(self.boss.get("/api/admin/leaderboard.csv?view=public").text)))
        self.assertEqual([r[1] for r in rows[1:]], ["bob"])


class RunFullTest(AdminBase):
    def setUp(self):
        super().setUp()
        self.alice = self.user_client("alice")
        self.bob = self.user_client("bob")

    def sub_row(self, sid):
        return self.conn.execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone()

    def test_run_full_creates_copy(self):
        p = self.submit("public", STARTER, client=self.alice)
        self.judge_all()
        before = dict(self.sub_row(p))
        # the user has a submission waiting: that doesn't block it
        busy = self.submit("public", WA_SOLVER, client=self.alice)
        r = self.boss.post(f"/api/admin/submissions/{p}/run-full")
        self.assertEqual(r.status_code, 200, r.text)
        new = r.json()["id"]
        self.assertEqual(r.json(), {"id": new, "from": p, "status": "queued"})
        n = self.sub_row(new)
        self.assertEqual((n["kind"], n["status"], n["user_id"], n["source"], n["tests_total"], n["copied_from"],
                          n["created"]),
                         ("full", "queued", before["user_id"], STARTER, len(self.settings.hidden_seeds), p,
                          before["created"]))
        self.assertEqual(dict(self.sub_row(p)), before)  # original untouched
        self.assertEqual(self.board(), [])
        self.judge_all()
        self.assertEqual(self.sub_row(new)["status"], "done")
        self.assertEqual(self.sub_row(busy)["status"], "done")
        self.assertEqual([b[:2] for b in self.board()], [("alice", 1)])
        self.assertEqual(self.detail(new, client=self.alice)["kind"], "full")  # the user sees it as theirs
        self.assertEqual(len(self.detail(new, client=self.alice)["results"]), len(self.settings.hidden_seeds))
        e = self.audit(action="submission.run_full")[0]
        self.assertEqual((e["target"], e["details"]), ("alice", {"from_id": p, "new_id": new}))
        rows = self.boss.get("/api/admin/submissions", params={"kind": "full"}).json()
        self.assertEqual(rows[0]["copied_from"], p)

    def test_refusals(self):
        f = self.fake_full("alice", [1, 1, 1])
        r = self.boss.post(f"/api/admin/submissions/{f}/run-full")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already a full", r.json()["error"])
        p = self.submit("public", STARTER, client=self.bob)
        self.judge_all()
        self.boss.post(f"/api/admin/users/{self.uid('bob')}/disable")
        r = self.boss.post(f"/api/admin/submissions/{p}/run-full")
        self.assertEqual(r.status_code, 409)
        self.assertIn("disabled", r.json()["error"])
        self.assertEqual(self.audit(action="submission.run_full"), [])

    def test_ignores_contest_window_and_hourly_limit(self):
        self.boss.post("/api/admin/settings", json={"set": {"full_per_hour": 0,
                                                            "end": iso_offset(minutes=-1),
                                                            "start": iso_offset(hours=-2)}})
        p = self.submit("public", STARTER, client=self.alice)
        self.submit("full", STARTER, client=self.alice, expect=403)
        self.judge_all()
        self.assertEqual(self.boss.post(f"/api/admin/submissions/{p}/run-full").status_code, 200)

    def bulk(self, which, dry=False):
        r = self.boss.post("/api/admin/run-full-bulk" + ("?dry_run=1" if dry else ""), json={"which": which})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_bulk_modes_and_dedupe(self):
        a1 = self.submit("public", WA_SOLVER, client=self.alice)
        self.judge_all()
        a2 = self.submit("public", STARTER, client=self.alice)
        self.judge_all()
        b1 = self.submit("public", STARTER, client=self.bob)
        self.judge_all()
        carol = self.user_client("carol")
        c1 = self.submit("public", STARTER, client=carol)
        self.judge_all()
        self.boss.post(f"/api/admin/users/{self.uid('carol')}/disable")
        self.fake_full("bob", [1, 1, 1])  # having a full already doesn't matter
        # dry run writes nothing
        d = self.bulk("latest_public", dry=True)
        self.assertEqual((d["count"], d["ids"], d["skipped"], d["from_ids"], d["dry_run"]), (2, [], 0, [a2, b1], True))
        self.assertEqual(self.audit(action="submission.run_full_bulk"), [])
        r = self.bulk("latest_public")
        self.assertEqual((r["count"], r["skipped"], len(r["ids"])), (2, 0, 2))
        self.assertEqual([self.sub_row(i)["copied_from"] for i in r["ids"]], [a2, b1])
        # pressing it again creates nothing
        self.assertEqual(self.bulk("latest_public"), {"count": 0, "ids": [], "skipped": 2})
        # all_public adds only alice's older one (carol is disabled)
        self.assertEqual(self.bulk("all_public", dry=True)["from_ids"], [a1])
        r = self.bulk("all_public")
        self.assertEqual((r["count"], r["skipped"]), (1, 2))
        self.assertEqual(self.sub_row(r["ids"][0])["copied_from"], a1)
        self.assertEqual(self.bulk("all_public"), {"count": 0, "ids": [], "skipped": 3})
        self.assertNotIn(c1, [x["copied_from"] for x in self.boss.get("/api/admin/submissions").json()])
        # a single run-full also counts as a copy for the dedupe
        self.judge_all()
        a3 = self.submit("public", STARTER, client=self.alice)
        self.judge_all()
        self.boss.post(f"/api/admin/submissions/{a3}/run-full")
        self.assertEqual(self.bulk("all_public")["count"], 0)
        self.judge_all()
        self.assertEqual([b[:2] for b in self.board()], [("alice", 1), ("bob", 2)])
        e = self.audit(action="submission.run_full_bulk")
        self.assertEqual([(x["details"]["which"], x["details"]["count"], x["details"]["skipped"]) for x in e],
                         [("all_public", 0, 4), ("all_public", 0, 3), ("all_public", 1, 2),
                          ("latest_public", 0, 2), ("latest_public", 2, 0)])
        self.assertEqual(e[-1]["details"]["pairs"][0][0], a2)


class FreezeExportTest(AdminBase):
    def setUp(self):
        self.env = {"PR_FREEZE": iso_offset(minutes=-5)}
        super().setUp()

    def test_public_view_is_frozen(self):
        self.user_client("alice")
        self.fake_full("alice", [100, 100, 100], created=iso_offset(minutes=-1))
        live = list(csv.reader(io.StringIO(self.boss.get("/api/admin/leaderboard.csv?view=live").text)))
        public = list(csv.reader(io.StringIO(self.boss.get("/api/admin/leaderboard.csv?view=public").text)))
        self.assertEqual((len(live), len(public)), (2, 1))


class SettingsTest(AdminBase):
    def put(self, expect=200, **body):
        r = self.boss.post("/api/admin/settings", json=body)
        self.assertEqual(r.status_code, expect, r.text)
        return r.json()

    def test_get_shape(self):
        s = self.boss.get("/api/admin/settings").json()
        self.assertEqual(set(s["editable"]), {"name", "start", "end", "freeze", "invite", "full_per_hour",
                                              "announcement", "announcement_on"})
        self.assertEqual(s["editable"]["name"], {"value": "Poker Run", "env": "Poker Run", "overridden": False})
        self.assertEqual(s["readonly"]["hidden_count"], 3)
        self.assertEqual(s["readonly"]["time_limit"], 1.0)

    def test_overrides_apply_and_clear(self):
        start, end = iso_offset(hours=-1), iso_offset(hours=5)
        s = self.put(set={"name": "Spring Cup", "start": start, "end": end, "full_per_hour": 1,
                          "announcement": "Judge maintenance at 5pm", "announcement_on": True})
        self.assertEqual(s["editable"]["name"], {"value": "Spring Cup", "env": "Poker Run", "overridden": True})
        c = self.client.get("/api/contest").json()
        self.assertEqual((c["name"], c["start"], c["end"], c["full_per_hour"], c["announcement"]),
                         ("Spring Cup", start, end, 1, "Judge maintenance at 5pm"))
        self.assertTrue(c["open"])
        # the hourly limit override is enforced
        alice = self.user_client("alice")
        self.submit("full", WA_SOLVER, client=alice)
        self.judge_all()
        r = self.submit("full", WA_SOLVER, client=alice, expect=429)
        self.assertIn("at most 1", r.json()["error"])
        # contest window override is enforced
        self.put(set={"start": iso_offset(hours=1)})
        self.assertFalse(self.client.get("/api/contest").json()["open"])
        self.submit("full", WA_SOLVER, client=alice, expect=403)
        # announcement off -> null
        self.put(set={"announcement_on": False})
        self.assertIsNone(self.client.get("/api/contest").json()["announcement"])
        # clear back to env
        s = self.put(clear=["name", "start", "end", "full_per_hour"])
        self.assertEqual(s["editable"]["name"], {"value": "Poker Run", "env": "Poker Run", "overridden": False})
        c = self.client.get("/api/contest").json()
        self.assertEqual((c["name"], c["start"], c["full_per_hour"]), ("Poker Run", None, 6))
        changes = self.audit(action="settings.update")
        self.assertEqual(changes[0]["details"]["name"], ["Spring Cup", "Poker Run", "reset to env"])
        self.assertEqual(changes[-1]["details"]["full_per_hour"], [6, 1])

    def test_explicit_unset_differs_from_clear(self):
        alice = self.user_client("alice")
        self.put(set={"end": iso_offset(hours=-1)})
        self.submit("full", WA_SOLVER, client=alice, expect=403)
        s = self.put(set={"end": None})
        self.assertEqual(s["editable"]["end"], {"value": None, "env": None, "overridden": True})
        self.submit("full", WA_SOLVER, client=alice)

    def test_validation(self):
        bad = [{"name": ""}, {"name": "x" * 81}, {"start": "tomorrow"}, {"full_per_hour": -1},
               {"full_per_hour": "3"}, {"full_per_hour": True}, {"invite": "a b"}, {"invite": "abc"},
               {"announcement": "x" * 501}, {"announcement_on": "yes"}, {"nope": 1},
               {"start": iso_offset(hours=2), "end": iso_offset(hours=1)},
               {"start": iso_offset(hours=1), "end": iso_offset(hours=3), "freeze": iso_offset(hours=0)},
               {"end": iso_offset(hours=3), "freeze": iso_offset(hours=4)}]
        for body in bad:
            r = self.boss.post("/api/admin/settings", json={"set": body})
            self.assertEqual(r.status_code, 400, (body, r.text))
            self.assertIn("error", r.json())
        self.assertEqual(self.boss.post("/api/admin/settings", json={"clear": ["bogus"]}).status_code, 400)
        self.assertEqual(self.boss.post("/api/admin/settings", json={"set": {"name": "x"}, "clear": ["name"]})
                         .status_code, 400)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0], 0)
        self.assertEqual(self.audit(action="settings.update"), [])
        # times are normalised to UTC
        s = self.put(set={"start": "2026-11-03T10:00:00+02:00"})
        self.assertEqual(s["editable"]["start"]["value"], "2026-11-03T08:00:00.000000Z")

    def test_invite_override_masked_in_audit(self):
        self.put(set={"invite": "secret-code-123"})
        self.assertTrue(self.client.get("/api/contest").json()["invite_required"])
        r = self.new_client().post("/api/register", json={"username": "alice", "password": "password123",
                                                          "email": "a@example.com", "invite": "wrong"})
        self.assertEqual(r.status_code, 403)
        self.register("alice", client=self.new_client(), invite="secret-code-123")
        s = self.boss.post("/api/admin/settings/invite/regenerate").json()
        code = s["editable"]["invite"]["value"]
        self.assertRegex(code, r"^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$")
        self.register("bob", client=self.new_client(), invite=code)
        self.put(set={"invite": ""})
        self.assertFalse(self.client.get("/api/contest").json()["invite_required"])
        self.register("carol", client=self.new_client())
        log = json.dumps(self.audit(), ensure_ascii=False)
        self.assertNotIn("secret-code-123", log)
        self.assertNotIn(code, log)
        self.assertIn("se…23", log)
        self.assertIn("(open)", log)
        self.assertEqual(self.audit(action="settings.invite")[0]["details"]["invite"][0], "se…23")

    def test_freeze_override_affects_leaderboard(self):
        self.user_client("alice")
        self.fake_full("alice", [1, 1, 1], created=iso_offset(minutes=-1))
        self.put(set={"freeze": iso_offset(minutes=-5)})
        lb = self.client.get("/api/leaderboard").json()
        self.assertEqual((lb["frozen"], lb["rows"]), (True, []))
        self.assertEqual(len(self.boss.get("/api/leaderboard").json()["rows"]), 1)


class AuditTest(AdminBase):
    def test_filters_and_order(self):
        self.user_client("alice")
        self.user_client("bob")
        a, b = self.uid("alice"), self.uid("bob")
        self.boss.post(f"/api/admin/users/{a}/disable")
        self.boss.post(f"/api/admin/users/{b}/disable")
        self.boss.post(f"/api/admin/users/{a}/enable")
        self.boss.post("/api/admin/settings", json={"set": {"name": "X"}})
        log = self.audit()
        self.assertEqual([e["action"] for e in log], ["settings.update", "user.enable", "user.disable", "user.disable"])
        self.assertEqual(set(log[0]), {"id", "created", "actor", "action", "target", "details"})
        self.assertEqual(len(self.audit(action="user.")), 3)
        self.assertEqual(len(self.audit(action="user.disable")), 2)
        self.assertEqual(len(self.audit(target="ALICE")), 2)
        self.assertEqual(len(self.audit(actor="boss", limit=1)), 1)
        self.assertEqual(self.audit(actor="nobody"), [])


class OverviewTest(AdminBase):
    def test_overview(self):
        alice = self.user_client("alice")
        self.user_client("bob")
        self.fake_full("alice", [100, 100, 100])
        self.submit("public", WA_SOLVER, client=alice)
        o = self.boss.get("/api/admin/overview").json()
        self.assertIsNone(o["judge"]["worker_last_seen"])
        self.assertFalse(o["judge"]["worker_alive"])
        self.assertEqual(o["judge"]["queue"], 1)
        worker.heartbeat(self.conn, force=True)
        running = worker.claim(self.conn)
        o = self.boss.get("/api/admin/overview").json()
        self.assertTrue(o["judge"]["worker_alive"])
        self.assertEqual([r["username"] for r in o["judge"]["running"]], ["alice"])
        worker.process(self.conn, self.settings, running)
        self.boss.post(f"/api/admin/users/{self.uid('bob')}/disable")
        o = self.boss.get("/api/admin/overview").json()
        c = o["counts"]
        self.assertEqual((c["registered"], c["active"], c["disabled"], c["admins"], c["active_24h"]), (3, 2, 1, 1, 3))
        self.assertEqual((c["submissions"], c["submissions_24h"], c["full"], c["public"], c["queued"], c["running"]),
                         (2, 2, 1, 1, 0, 0))
        self.assertEqual(o["verdicts"], {"AC": 3, "WA": 2, "TLE": 0, "RE": 0})
        self.assertEqual(len(o["daily"]), 14)
        self.assertEqual(o["daily"][-1]["date"], db.now().date().isoformat())
        self.assertEqual((o["daily"][-1]["registrations"], o["daily"][-1]["submissions"], o["daily"][-1]["full"]),
                         (3, 2, 1))
        self.assertEqual([r["username"] for r in o["top"]], ["alice"])
        self.assertEqual(o["contest"]["name"], "Poker Run")

    def test_worker_writes_heartbeat(self):
        worker._last_beat[0] = 0.0
        worker.run_once(self.conn, self.settings)
        hb = self.conn.execute("SELECT value FROM meta WHERE key = 'worker_heartbeat'").fetchone()
        self.assertIsNotNone(hb)


class SimilarityTest(AdminBase):
    SOLVER = '''import sys

def solve(text):
    nums = [int(x) for x in text.split()]
    best = 0
    for i, v in enumerate(nums):
        if v % 7 == 3 and v > best:
            best = v + i * 2
        elif v % 5 == 1:
            best = max(best, v // 3)
    moves = []
    for k in range(best % 9):
        moves.append("discard %d" % k)
    return moves


def main():
    out = solve(sys.stdin.read())
    print(len(out))
    for m in out:
        print(m)


main()
'''

    def test_unit_metric(self):
        renamed = self.SOLVER.replace("nums", "values").replace("# x", "")
        commented = "# my own work!\n" + self.SOLVER.replace("    best = 0", "    best = 0  # start")
        different = "\n".join(f"x{i} = {i} * {i + 1} - {i % 3}" for i in range(60))
        res = similarity.compare([("a", 1, self.SOLVER), ("b", 2, commented), ("c", 3, renamed),
                                  ("d", 4, different), ("e", 5, STARTER), ("f", 6, "print(1)")], limit=50)
        pairs = {(p["a"]["username"], p["b"]["username"]): p["similarity"] for p in res["pairs"]}
        self.assertEqual(pairs[("a", "b")], 1.0)  # comments/whitespace ignored
        self.assertGreater(pairs[("a", "c")], 0.2)
        self.assertLess(pairs[("a", "c")], 1.0)
        self.assertFalse(any("d" in k for k in pairs))
        self.assertEqual(res["skipped"], 2)  # the starter itself and a one-liner
        self.assertEqual(res["compared"], 4)
        self.assertFalse(res["truncated"])
        res = similarity.compare([("a", 1, self.SOLVER), ("b", 2, self.SOLVER), ("c", 3, self.SOLVER)],
                                 max_students=2)
        self.assertTrue(res["truncated"])
        self.assertEqual(res["compared"], 2)
        # invalid Python still tokenises (fallback)
        self.assertTrue(similarity.grams("def f(:\n  'unterminated\n" + self.SOLVER))

    def test_endpoint_uses_latest_full(self):
        for u in ("alice", "bob", "carol"):
            self.user_client(u)
        for u, src in (("alice", self.SOLVER), ("bob", self.SOLVER), ("carol", self.SOLVER)):
            sid = self.fake_full(u, [1, 1, 1])
            self.conn.execute("UPDATE submissions SET source = ? WHERE id = ?", (src, sid))
        latest = self.fake_full("carol", [2, 2, 2])
        self.conn.execute("UPDATE submissions SET source = ? WHERE id = ?",
                          ("\n".join(f"y{i} = {i} ** 2 + {i}" for i in range(80)), latest))
        r = self.boss.get("/api/admin/similarity").json()
        self.assertEqual([(p["a"]["username"], p["b"]["username"], p["similarity"]) for p in r["pairs"]],
                         [("bob", "alice", 1.0)])
        self.assertEqual(r["compared"], 3)
        self.assertIn("Jaccard", r["method"])


class EmailTest(AdminBase):
    def test_register_and_login_by_email(self):
        r = self.register("alice", email="  Alice.Smith@Example.COM ")
        self.assertEqual(r.json()["email"], "alice.smith@example.com")
        c = self.new_client()
        for ident in ("alice.smith@example.com", "ALICE.SMITH@example.com", "alice"):
            r = c.post("/api/login", json={"username": ident, "password": "password123"})
            self.assertEqual(r.status_code, 200, ident)
            self.assertEqual(r.json()["username"], "alice")
        r = c.post("/api/login", json={"login": "alice.smith@example.com", "password": "password123"})
        self.assertEqual(r.status_code, 200)
        r = c.post("/api/login", json={"username": "alice.smith@example.com", "password": "wrong-pass"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(c.post("/api/login", json={"username": "nobody@example.com", "password": "password123"})
                         .status_code, 401)
        self.assertEqual(c.get("/api/me").json(), {"username": "alice", "is_admin": False,
                                                   "email": "alice.smith@example.com"})

    def test_validation_and_uniqueness(self):
        for bad in ("", "nope", "a@b", "a b@c.de", "a@@c.de", "x" * 250 + "@ab.cd"):
            r = self.new_client().post("/api/register", json={"username": "alice", "password": "password123",
                                                              "email": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("email", r.json()["error"])
        self.register("alice", email="same@example.com")
        r = self.new_client().post("/api/register", json={"username": "bob", "password": "password123",
                                                          "email": "SAME@example.com"})
        self.assertEqual((r.status_code, r.json()["error"]), (409, "email already registered"))
        with self.assertRaises(sqlite3.IntegrityError):  # the index backs it up
            self.conn.execute("UPDATE users SET email = 'same@example.com' WHERE username = 'boss'")

    def test_rate_limit_keyed_on_identifier(self):
        self.register("alice", email="al@example.com")
        c = self.new_client()
        for _ in range(10):
            c.post("/api/login", json={"username": "AL@example.com", "password": "wrongpass1"})
        self.assertEqual(c.post("/api/login", json={"username": "al@example.com", "password": "password123"})
                         .status_code, 429)
        self.assertEqual(c.post("/api/login", json={"username": "alice", "password": "password123"}).status_code, 200)

    def test_change_own_email(self):
        alice = self.user_client("alice")
        self.user_client("bob")
        r = alice.post("/api/account/email", json={"email": "new@example.com", "password": "wrong"})
        self.assertEqual(r.status_code, 401)
        r = alice.post("/api/account/email", json={"email": "bad", "password": "password123"})
        self.assertEqual(r.status_code, 400)
        r = alice.post("/api/account/email", json={"email": "BOB@example.com", "password": "password123"})
        self.assertEqual(r.status_code, 409)
        r = alice.post("/api/account/email", json={"email": "New@Example.com", "password": "password123"})
        self.assertEqual(r.json(), {"username": "alice", "is_admin": False, "email": "new@example.com"})
        self.assertEqual(alice.get("/api/me").json()["email"], "new@example.com")
        self.assertEqual(self.new_client().post("/api/account/email", json={"email": "a@b.co", "password": "x"})
                         .status_code, 401)

    def test_admin_edit_and_privacy(self):
        alice = self.user_client("alice")
        self.user_client("bob")
        a = self.uid("alice")
        self.fake_full("alice", [1, 1, 1])
        r = self.boss.post(f"/api/admin/users/{a}/email", json={"email": "bob@example.com"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.boss.post(f"/api/admin/users/{a}/email", json={"email": "x"}).status_code, 400)
        r = self.boss.post(f"/api/admin/users/{a}/email", json={"email": "Alice2@Example.com"})
        self.assertEqual(r.json()["email"], "alice2@example.com")
        r = self.boss.post(f"/api/admin/users/{a}/email", json={"email": None})
        self.assertIsNone(r.json()["email"])
        details = [e["details"] for e in self.audit(action="user.email")]
        self.assertEqual(details, [{"old": "a***@example.com", "new": None},
                                   {"old": "a***@example.com", "new": "a***@example.com"}])
        self.assertNotIn("alice2@example.com", json.dumps(self.audit()))
        # never public: leaderboard, other students' views
        self.boss.post(f"/api/admin/users/{a}/email", json={"email": "alice@example.com"})
        for c in (self.new_client(), alice, self.boss):
            self.assertNotIn("@", json.dumps(c.get("/api/leaderboard").json()))
        self.assertNotIn("@", json.dumps(alice.get("/api/submissions").json()))
        self.assertNotIn("bob@", json.dumps(alice.get("/api/me").json()))


class CliTest(AdminBase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"PR_DB": self.settings.db, "PR_ADMINS": "boss",
                                          "PR_HIDDEN_SEEDS": "777001,777002,777003"}), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = manage.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_commands(self):
        alice = self.user_client("alice")
        self.fake_full("alice", [1, 1, 1])
        code, out, _ = self.run_cli("list-users")
        self.assertEqual(code, 0)
        self.assertIn("alice alice@example.com", out)
        self.assertEqual(self.run_cli("disable", "alice", "--reason", "test")[0], 0)
        self.assertEqual(alice.get("/api/me").status_code, 401)
        self.assertEqual(self.run_cli("enable", "alice")[0], 0)
        code, _, err = self.run_cli("disable", "boss")
        self.assertEqual(code, 1)
        self.assertIn("last active admin", err)
        self.assertEqual(self.run_cli("set-email", "alice", "cli@example.com")[0], 0)
        self.assertEqual(self.run_cli("delete-user", "alice", "--confirm", "wrong")[0], 1)
        sid = self.conn.execute("SELECT id FROM submissions").fetchone()[0]
        self.assertEqual(self.run_cli("delete-submission", str(sid))[0], 0)
        self.assertEqual(self.run_cli("delete-user", "alice", "--confirm", "alice")[0], 0)
        self.assertEqual(self.run_cli("enable", "ghost")[0], 1)
        code, out, _ = self.run_cli("audit")
        self.assertIn("(cli) user.delete alice", out)
        actions = [e["action"] for e in self.audit(actor="(cli)")]
        self.assertEqual(actions, ["user.delete", "submission.delete", "user.email", "user.enable", "user.disable"])


OLD_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL, created TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created TEXT NOT NULL, expires TEXT NOT NULL);
CREATE TABLE submissions (id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('public', 'full')), source TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'error')),
    started TEXT, finished TEXT, error TEXT, raw_total INTEGER, cleared_total INTEGER, max_time REAL,
    tests_ac INTEGER, tests_total INTEGER, tests_done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE results (submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    seed INTEGER NOT NULL, verdict TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0, cleared INTEGER NOT NULL DEFAULT 0,
    time REAL, message TEXT, PRIMARY KEY (submission_id, seed));
"""


class MigrationTest(unittest.TestCase):
    def test_old_database_is_migrated_in_place(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="prmig-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = str(Path(tmp) / "old.db")
        old = sqlite3.connect(path)
        old.executescript(OLD_SCHEMA)
        pw = auth.hash_password("password123")
        old.execute("INSERT INTO users(username, pw_hash, created, is_admin) VALUES ('oldie', ?, ?, 0)",
                    (pw, db.now_iso()))
        old.execute("INSERT INTO users(username, pw_hash, created, is_admin) VALUES ('boss', ?, ?, 0)",
                    (pw, db.now_iso()))
        old.execute("INSERT INTO submissions(user_id, kind, source, created, status, raw_total, tests_ac, tests_total,"
                    " tests_done, finished) VALUES (1, 'full', 'print(1)', ?, 'done', 30, 3, 3, 3, ?)",
                    (db.now_iso(), db.now_iso()))
        for seed in (777001, 777002, 777003):
            old.execute("INSERT INTO results VALUES (1, ?, 'AC', 10, 0, 0.1, '')", (seed,))
        old.commit()
        old.close()

        settings = make_settings(tmp, PR_DB=path)
        db.init(path)
        db.init(path)  # idempotent
        conn = db.connect(path)
        self.addCleanup(conn.close)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertTrue({"last_login", "disabled_at", "disabled_reason", "email"} <= cols)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"audit", "settings", "meta"} <= tables)
        self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'users_email'").fetchone())
        row = conn.execute("SELECT username, email, disabled_at, last_login FROM users WHERE id = 1").fetchone()
        self.assertEqual(tuple(row), ("oldie", None, None, None))

        from fastapi.testclient import TestClient
        c = TestClient(create_app(settings))
        c.headers.update({"X-Requested-With": "fetch"})
        r = c.post("/api/login", json={"username": "oldie", "password": "password123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["email"])
        self.assertEqual(c.get("/api/leaderboard").json()["rows"][0]["relative_total"], 3_000_000)
        r = c.post("/api/account/email", json={"email": "oldie@example.com", "password": "password123"})
        self.assertEqual(r.status_code, 200)
        boss = TestClient(create_app(settings))
        boss.headers.update({"X-Requested-With": "fetch"})
        self.assertEqual(boss.post("/api/login", json={"username": "boss", "password": "password123"}).status_code, 200)
        users = {u["username"]: u for u in boss.get("/api/admin/users").json()["users"]}
        self.assertEqual(users["oldie"]["email"], "oldie@example.com")
        self.assertIsNone(users["boss"]["email"])  # several NULL emails are fine with the partial index
        self.assertEqual(users["oldie"]["rank"], 1)


if __name__ == "__main__":
    unittest.main()

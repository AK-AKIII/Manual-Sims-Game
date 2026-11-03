import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from prtest_helpers import (EMPTY_SOLVER, HIDDEN, RE_SOLVER, REPO, STARTER, TLE_SOLVER, WA_SOLVER,
                            PlatformTest)

from prplatform import db, judge, sandbox, worker
from poker_run import check

HIDDEN_SEEDS = [int(s) for s in HIDDEN.split(",")]


class SubmissionFlowTest(PlatformTest):
    def setUp(self):
        super().setUp()
        self.register("alice")

    def test_public_starter(self):
        sid = self.submit("public", STARTER)
        d = self.detail(sid)
        self.assertEqual(d["status"], "queued")
        self.assertEqual(d["position"], 1)
        self.assertEqual(d["tests_total"], 2)
        self.assertEqual(d["tests_done"], 0)
        self.assertEqual(self.judge_all(), [sid])
        d = self.detail(sid)
        self.assertEqual(d["status"], "done")
        self.assertIsNone(d["position"])
        self.assertEqual(d["source"], STARTER)
        self.assertEqual([r["seed"] for r in d["results"]], [5, 6])
        self.assertEqual(d["tests_ac"], 2)
        self.assertEqual(d["tests_done"], 2)
        self.assertIsNone(d["relative_total"])
        # The same scores the checker gives when the starter is run directly.
        for r in d["results"]:
            self.assertEqual(r["verdict"], "AC")
            inp = judge.input_text(r["seed"])
            out = subprocess.run([sys.executable, str(REPO / "poker_run/starters/starter.py")],
                                 input=inp, capture_output=True, text=True, timeout=30).stdout
            code, v = check(inp, out)
            self.assertEqual((code, r["score"], r["cleared"]), (0, v["score"], v["cleared"]))
            self.assertIn("score", r["message"])
            self.assertGreaterEqual(r["time"], 0)
        self.assertEqual(d["raw_total"], sum(r["score"] for r in d["results"]))
        lst = self.client.get("/api/submissions").json()
        self.assertEqual(len(lst), 1)
        self.assertNotIn("source", lst[0])
        self.assertEqual(lst[0]["tests_done"], 2)

    def test_full_starter_hides_seeds(self):
        sid = self.submit("full", STARTER)
        self.judge_all()
        r = self.client.get(f"/api/submissions/{sid}")
        d = r.json()
        self.assertEqual(d["status"], "done")
        self.assertEqual(d["tests_total"], 3)
        self.assertEqual(d["relative_total"], 3_000_000)  # the only submission is the best on every seed
        self.assertEqual([x["test"] for x in d["results"]], [1, 2, 3])
        for x in d["results"]:
            self.assertEqual(set(x), {"test", "verdict", "time"})
            self.assertEqual(x["verdict"], "AC")
        for seed in HIDDEN_SEEDS:
            self.assertNotIn(str(seed), r.text)
            self.assertNotIn(str(seed), self.client.get("/api/submissions").text)
            self.assertNotIn(str(seed), self.client.get("/api/leaderboard").text)
            self.assertNotIn(str(seed), self.client.get("/api/contest").text)
        # Admins see the seeds.
        boss = self.user_client("boss")
        d = self.detail(sid, client=boss)
        self.assertEqual([x["seed"] for x in d["results"]], HIDDEN_SEEDS)
        lb = self.client.get("/api/leaderboard").json()
        self.assertEqual(lb["rows"][0]["username"], "alice")
        self.assertEqual(lb["rows"][0]["relative_total"], 3_000_000)

    def test_tle_re_wa(self):
        cases = [(TLE_SOLVER, "TLE"), (RE_SOLVER, "RE"), (WA_SOLVER, "WA"), (EMPTY_SOLVER, "WA"),
                 ("import sys\nsys.stdin.read()\nprint('x' * 2_000_000)\n", "WA")]
        for source, verdict in cases:
            with self.subTest(verdict=verdict, source=source[:30]):
                sid = self.submit("public", source)
                self.judge_all()
                d = self.detail(sid)
                self.assertEqual(d["status"], "done", d)
                self.assertEqual([r["verdict"] for r in d["results"]], [verdict, verdict])
                self.assertEqual(d["tests_ac"], 0)
                self.assertEqual(d["raw_total"], 0)
                msg = d["results"][0]["message"]
                if verdict == "TLE":
                    self.assertIn("time limit", msg)
                    self.assertGreaterEqual(d["results"][0]["time"], 1.0)
                if source is RE_SOLVER:
                    self.assertIn("boom from the solver", msg)
                if source is WA_SOLVER:
                    self.assertIn("fly", msg)

    def test_hostile_solver_local(self):
        """In local mode the solver gets a scrubbed environment and a throwaway cwd; it
        can't see the configured seeds, and its writes vanish with the job dir."""
        db_path = self.settings.db
        source = f"""
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
print("ENV=" + os.environ.get("PR_HIDDEN_SEEDS", "none"), file=sys.stderr)
print("KEYS=" + ",".join(sorted(k for k in os.environ if k.startswith("PR_"))), file=sys.stderr)
print("LISTING=" + ",".join(sorted(os.listdir(os.path.join(here, "inputs")))), file=sys.stderr)
open(os.path.join(here, "planted.txt"), "w").write("x")
open("in_cwd.txt", "w").write("x")
try:
    open({str(Path(db_path).parent / 'planted_next_to_db.txt')!r}, "w").write("x")
    print("WROTE_OUTSIDE", file=sys.stderr)
except OSError as e:
    print("NO_WRITE", e, file=sys.stderr)
sys.exit(3)
"""
        sid = self.submit("public", source)
        self.judge_all()
        d = self.detail(sid)
        msg = d["results"][0]["message"]
        self.assertEqual(d["results"][0]["verdict"], "RE")
        self.assertIn("ENV=none", msg)
        self.assertIn("KEYS=\n", msg + "\n")
        self.assertNotIn("777001", msg)
        self.assertIn("LISTING=0001.txt,0002.txt", msg)  # inputs only, never seeds
        # The job dir (and anything planted in it) is gone.
        self.assertFalse(list(Path(tempfile.gettempdir()).glob("pr-job-*/planted.txt")))
        # Local mode is NOT a sandbox: writes elsewhere the user can write do succeed.
        # (That is exactly why PR_SANDBOX=local is development only.) Clean up.
        p = Path(db_path).parent / "planted_next_to_db.txt"
        if p.exists():
            self.assertIn("WROTE_OUTSIDE", msg)
            p.unlink()

    def test_progress_and_tests_done(self):
        sid = self.submit("full", STARTER)
        seen = []
        other = db.connect(self.settings.db)
        self.addCleanup(other.close)

        def on_progress(sub_id, done, total):
            row = other.execute("SELECT status, tests_done, tests_total FROM submissions WHERE id = ?",
                                (sub_id,)).fetchone()
            n = other.execute("SELECT COUNT(*) FROM results WHERE submission_id = ?", (sub_id,)).fetchone()[0]
            api = self.detail(sub_id)
            seen.append((done, total, row["status"], row["tests_done"], n, api["tests_done"], len(api["results"])))

        self.judge_all(on_progress=on_progress)
        self.assertEqual(seen, [(i, 3, "running", i, i, i, i) for i in (1, 2, 3)])
        d = self.detail(sid)
        self.assertEqual((d["tests_done"], d["tests_total"], d["status"]), (3, 3, "done"))

    def test_queue_position(self):
        bob = self.user_client("bob")
        a = self.submit("public", STARTER)
        b = self.submit("public", STARTER, client=bob)
        self.assertEqual(self.detail(a)["position"], 1)
        self.assertEqual(self.detail(b, client=bob)["position"], 2)
        self.assertEqual(bob.get("/api/submissions").json()[0]["position"], 2)
        self.assertEqual(bob.get(f"/api/submissions/{a}").status_code, 404)  # not his
        worker.run_once(self.conn, self.settings)
        self.assertEqual(self.detail(b, client=bob)["position"], 1)


class BrokenSandbox:
    def __init__(self, exc=None, records=None, rc=0, timed_out=False):
        self.exc, self.records, self.rc, self.timed_out = exc, records or [], rc, timed_out

    def run(self, job_dir, n, on_record):
        for r in self.records:
            on_record(r)
        if self.exc:
            raise self.exc
        return sandbox.RunResult(self.rc, self.timed_out, "docker: image not found", len(self.records))


class WorkerTest(PlatformTest):
    def setUp(self):
        super().setUp()
        self.register("alice")

    def status(self, sid):
        return self.conn.execute("SELECT status, error, tests_done FROM submissions WHERE id = ?", (sid,)).fetchone()

    def test_crash_marks_error(self):
        sid = self.submit("public", STARTER)
        worker.run_once(self.conn, self.settings, sandbox=BrokenSandbox(exc=RuntimeError("kaboom")))
        st = self.status(sid)
        self.assertEqual(st["status"], "error")
        self.assertIn("internal judge error", st["error"])
        self.assertEqual(self.detail(sid)["error"], st["error"])

    def test_sandbox_failure_marks_error(self):
        sid = self.submit("public", STARTER)
        worker.run_once(self.conn, self.settings, sandbox=BrokenSandbox(rc=125))
        st = self.status(sid)
        self.assertEqual(st["status"], "error")
        self.assertIn("image not found", st["error"])

    def test_protocol_violation(self):
        sid = self.submit("public", STARTER)
        rec = {"name": "0002", "status": "ok", "time": 0.1, "stdout": "0\n", "stderr_tail": ""}
        worker.run_once(self.conn, self.settings, sandbox=BrokenSandbox(records=[rec]))
        st = self.status(sid)
        self.assertEqual(st["status"], "error")
        self.assertIn("protocol", st["error"])

    def test_overall_timeout_and_early_stop(self):
        rec = {"name": "0001", "status": "ok", "time": 0.1, "stdout": "0\n", "stderr_tail": ""}
        sid = self.submit("public", STARTER)
        worker.run_once(self.conn, self.settings, sandbox=BrokenSandbox(records=[rec], timed_out=True, rc=-9))
        d = self.detail(sid)
        self.assertEqual(d["status"], "done")
        self.assertEqual([r["verdict"] for r in d["results"]], ["AC", "TLE"])
        sid = self.submit("public", STARTER)
        worker.run_once(self.conn, self.settings, sandbox=BrokenSandbox(records=[rec], rc=137))
        d = self.detail(sid)
        self.assertEqual([r["verdict"] for r in d["results"]], ["AC", "RE"])
        self.assertIn("out of memory", d["results"][1]["message"])

    def test_reset_running_on_start(self):
        sid = self.submit("public", STARTER)
        self.assertEqual(worker.claim(self.conn), sid)
        self.conn.execute("INSERT INTO results VALUES (?, 5, 'AC', 1, 0, 0.1, '')", (sid,))
        self.assertEqual(self.status(sid)["status"], "running")
        self.assertEqual(worker.reset_running(self.conn), 1)
        self.assertEqual(self.status(sid)["status"], "queued")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0], 0)
        self.judge_all()
        self.assertEqual(self.status(sid)["status"], "done")

    def test_claim_is_oldest_first(self):
        bob = self.user_client("bob")
        a = self.submit("public", STARTER)
        b = self.submit("public", STARTER, client=bob)
        self.assertEqual(worker.claim(self.conn), a)
        self.assertEqual(worker.claim(self.conn), b)
        self.assertIsNone(worker.claim(self.conn))


class RunnerTest(unittest.TestCase):
    """judge/runner.py on its own: JSON Lines, one per test, in sorted order."""

    def test_runner_jsonl(self):
        with tempfile.TemporaryDirectory() as job:
            os.mkdir(os.path.join(job, "inputs"))
            for name, text in (("0002", "b"), ("0001", "a"), ("0003", "loop")):
                Path(job, "inputs", name + ".txt").write_text(text)
            Path(job, "main.py").write_text(
                "import sys, os\nd = sys.stdin.read()\n"
                "if d == 'loop':\n    while True: pass\n"
                "if d == 'b':\n    sys.stderr.write('E' * 5000); sys.exit(4)\n"
                "print(d.upper(), os.getcwd() != os.path.dirname(os.path.abspath(__file__)))\n")
            out = subprocess.run([sys.executable, str(REPO / "platform/judge/runner.py"), job, "0.5"],
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(out.returncode, 0, out.stderr)
            lines = [json.loads(l) for l in out.stdout.splitlines()]
            self.assertEqual([l["name"] for l in lines], ["0001", "0002", "0003"])
            self.assertEqual([l["status"] for l in lines], ["ok", "re", "tle"])
            self.assertEqual(lines[0]["stdout"], "A True\n")
            self.assertEqual(len(lines[1]["stderr_tail"]), 2000)
            self.assertEqual(lines[1]["exit_code"], 4)
            self.assertGreaterEqual(lines[2]["time"], 0.5)
            for l in lines:
                self.assertTrue({"name", "status", "time", "stdout", "stderr_tail"} <= set(l))


if __name__ == "__main__":
    unittest.main()

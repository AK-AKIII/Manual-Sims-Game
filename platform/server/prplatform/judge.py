"""Judge one submission.

1. Write the source to <job>/main.py and one input per seed to
   <job>/inputs/0001.txt ... (generated with poker_run.instance from the seeds;
   the job dir never contains the seeds themselves).
2. Run the sandbox once for the whole submission. The runner prints one JSON
   line per finished test; each one is checked right away with
   poker_run.check (outside the sandbox), its result row is stored and the
   submission's tests_done is bumped, so the site can show live progress.
3. Store the totals and mark the submission done (or error if the sandbox
   itself failed).

poker_run import: the repo root must be importable. In Docker it is on
PYTHONPATH; for local runs, if `import poker_run` fails we add the repo root
(three levels above this package: platform/server/prplatform -> repo) to
sys.path, so `cd platform/server && python -m prplatform.worker` just works.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from . import db
from . import sandbox as sandbox_mod

REPO_ROOT = Path(__file__).resolve().parents[3]


def _import_poker_run():
    try:
        import poker_run  # noqa: F401
    except ImportError:
        if (REPO_ROOT / "poker_run" / "__init__.py").is_file():
            sys.path.append(str(REPO_ROOT))
        import poker_run  # noqa: F401
    from poker_run.checker import check
    from poker_run.instance import format_instance, generate
    return check, generate, format_instance


check, generate, format_instance = _import_poker_run()

MESSAGE_MAX = 2000
VERDICTS = ("AC", "WA", "TLE", "RE")


class JudgeError(Exception):
    """An internal problem; the submission is marked 'error' with this message."""


def input_text(seed):
    return format_instance(generate(seed))


def test_name(i):
    return f"{i + 1:04d}"


def prepare_job(root, source, seeds):
    """Make the job dir; returns (path, {test name: input text})."""
    job = Path(tempfile.mkdtemp(prefix="pr-job-", dir=root or None))
    inputs = job / "inputs"
    inputs.mkdir()
    texts = {}
    for i, seed in enumerate(seeds):
        name = test_name(i)
        texts[name] = input_text(seed)
        (inputs / f"{name}.txt").write_text(texts[name])
    (job / "main.py").write_text(source, encoding="utf-8")
    # The container runs as uid 65534: everything must be world-readable (mounted read-only).
    os.chmod(job, 0o755)
    os.chmod(inputs, 0o755)
    for p in [job / "main.py", *inputs.iterdir()]:
        os.chmod(p, 0o644)
    return job, texts


def _clip(text, n=MESSAGE_MAX):
    text = text or ""
    return text if len(text) <= n else "..." + text[-n:]


def evaluate(rec, inp, time_limit):
    """Runner record + input -> (verdict, score, cleared, time, message)."""
    status = rec.get("status")
    try:
        t = float(rec.get("time") or 0.0)
    except (TypeError, ValueError):
        t = 0.0
    stderr = rec.get("stderr_tail") if isinstance(rec.get("stderr_tail"), str) else ""
    if status == "tle":
        return "TLE", 0, 0, t, f"time limit exceeded ({time_limit:g} s)"
    if status == "re":
        code = rec.get("exit_code")
        head = f"runtime error (exit code {code})" if code is not None else "runtime error"
        return "RE", 0, 0, t, _clip(head + (":\n" + stderr if stderr.strip() else ""))
    if status != "ok":
        raise JudgeError(f"runner reported unknown status {status!r}")
    stdout = rec.get("stdout")
    if not isinstance(stdout, str):
        raise JudgeError("runner record has no stdout")
    if rec.get("stdout_truncated"):
        return "WA", 0, 0, t, "output is larger than 1 MB"
    try:
        code, v = check(inp, stdout)
    except Exception as e:  # the checker should never raise; treat as our bug
        raise JudgeError(f"checker crashed: {type(e).__name__}: {e}") from None
    if code == 0:
        return ("AC", int(v.get("score", 0)), int(v.get("cleared", 0)), t,
                f"score {v.get('score', 0)}, cleared {v.get('cleared', 0)}/{v.get('rounds', '?')} rounds")
    if code in (1, 2):
        return "WA", 0, 0, t, _clip(str(v.get("error", "wrong answer")))
    raise JudgeError(f"checker rejected the input (exit {code}): {v.get('error')}")


def judge(conn, settings, sub_id, sandbox=None, on_progress=None):
    """Judge a submission that is in status 'running'. Raises JudgeError /
    SandboxError on internal failures (the caller marks the submission error)."""
    sub = conn.execute("SELECT id, kind, source FROM submissions WHERE id = ?", (sub_id,)).fetchone()
    if sub is None:
        raise JudgeError("submission not found")
    seeds = settings.seeds_for(sub["kind"])
    names = [test_name(i) for i in range(len(seeds))]
    seed_of = dict(zip(names, seeds))
    sandbox = sandbox or sandbox_mod.get(settings)

    with db.transaction(conn, immediate=True):
        conn.execute("DELETE FROM results WHERE submission_id = ?", (sub_id,))
        conn.execute("UPDATE submissions SET tests_done = 0, tests_total = ? WHERE id = ?",
                     (len(seeds), sub_id))

    job, texts = prepare_job(settings.job_root, sub["source"], seeds)
    done = []

    def on_record(rec):
        i = len(done)
        if rec.get("name") != names[i]:
            raise sandbox_mod.SandboxError(
                f"runner protocol violation: expected test {names[i]}, got {rec.get('name')!r}")
        res = evaluate(rec, texts[names[i]], settings.time_limit)
        store(conn, sub_id, seed_of[names[i]], res, i + 1)
        done.append(res)
        if on_progress:
            on_progress(sub_id, len(done), len(seeds))

    try:
        run = sandbox.run(str(job), len(seeds), on_record)
    finally:
        shutil.rmtree(job, ignore_errors=True)

    if len(done) < len(seeds):
        if run.timed_out:
            res = ("TLE", 0, 0, None, "the judge's overall time limit ran out before this test")
        elif not done and run.returncode != 0:
            raise sandbox_mod.SandboxError(
                f"sandbox failed (exit {run.returncode}): {_clip(run.stderr, 500).strip()}")
        else:
            hint = " (out of memory?)" if run.returncode in (137, -9) else ""
            res = ("RE", 0, 0, None, f"the sandbox stopped before this test (exit {run.returncode}){hint}")
        for i in range(len(done), len(seeds)):
            store(conn, sub_id, seed_of[names[i]], res, i + 1)
            done.append(res)
            if on_progress:
                on_progress(sub_id, len(done), len(seeds))

    times = [r[3] for r in done if r[3] is not None]
    with db.transaction(conn, immediate=True):
        conn.execute(
            "UPDATE submissions SET status = 'done', finished = ?, error = NULL, raw_total = ?, "
            "cleared_total = ?, max_time = ?, tests_ac = ?, tests_total = ?, tests_done = ? "
            "WHERE id = ?",
            (db.now_iso(), sum(r[1] for r in done), sum(r[2] for r in done),
             round(max(times), 3) if times else None, sum(r[0] == "AC" for r in done),
             len(seeds), len(done), sub_id))


def store(conn, sub_id, seed, res, tests_done):
    verdict, score, cleared, t, message = res
    with db.transaction(conn, immediate=True):
        conn.execute("INSERT OR REPLACE INTO results(submission_id, seed, verdict, score, cleared, time, message) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (sub_id, seed, verdict, score, cleared, None if t is None else round(t, 3), message))
        conn.execute("UPDATE submissions SET tests_done = ? WHERE id = ?", (tests_done, sub_id))

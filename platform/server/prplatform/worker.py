"""Judge worker: `python -m prplatform.worker [--once] [--workers N]`.

Loop: atomically claim the oldest queued submission (queued -> running),
judge it in the sandbox, store results. An internal failure marks the
submission 'error' with a message. On start, submissions left 'running' by a
previous (crashed or stopped) worker go back to 'queued'.

Run ONE worker process per database (use --workers / PR_WORKERS for parallel
judging threads inside it): the start-up reset assumes no other worker is
judging at that moment.
"""

import argparse
import logging
import signal
import sys
import threading
import time
import traceback

from . import config, db, judge
from . import sandbox as sandbox_mod

log = logging.getLogger("prplatform.worker")


def reset_running(conn):
    with db.transaction(conn, immediate=True):
        n = conn.execute("UPDATE submissions SET status = 'queued', started = NULL, tests_done = 0 "
                         "WHERE status = 'running'").rowcount
        conn.execute("DELETE FROM results WHERE submission_id IN "
                     "(SELECT id FROM submissions WHERE status = 'queued')")
    return n


HEARTBEAT_KEY = "worker_heartbeat"
HEARTBEAT_EVERY = 5.0
_beat_lock = threading.Lock()
_last_beat = [0.0]


def heartbeat(conn, force=False):
    """Record that a worker is alive (meta.worker_heartbeat), at most every 5 s.
    Never raises: the heartbeat must not stop judging."""
    t = time.monotonic()
    with _beat_lock:
        if not force and t - _last_beat[0] < HEARTBEAT_EVERY:
            return
        _last_beat[0] = t
    try:
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (HEARTBEAT_KEY, db.now_iso()))
    except Exception:  # pragma: no cover - e.g. database busy for longer than busy_timeout
        log.warning("could not write the heartbeat:\n%s", traceback.format_exc())


def claim(conn):
    """Claim the oldest queued submission; returns its id or None."""
    with db.transaction(conn, immediate=True):
        row = conn.execute("SELECT id FROM submissions WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        conn.execute("UPDATE submissions SET status = 'running', started = ?, finished = NULL, error = NULL, "
                     "tests_done = 0 WHERE id = ? AND status = 'queued'", (db.now_iso(), row["id"]))
        return row["id"]


def mark_error(conn, sub_id, message):
    with db.transaction(conn, immediate=True):
        conn.execute("UPDATE submissions SET status = 'error', finished = ?, error = ? WHERE id = ?",
                     (db.now_iso(), message[:1000], sub_id))


def process(conn, settings, sub_id, sandbox=None, on_progress=None):
    try:
        judge.judge(conn, settings, sub_id, sandbox=sandbox, on_progress=on_progress)
        log.info("submission %s judged", sub_id)
    except (judge.JudgeError, sandbox_mod.SandboxError) as e:
        log.warning("submission %s: %s", sub_id, e)
        mark_error(conn, sub_id, f"judge error: {e}")
    except Exception as e:
        log.error("submission %s crashed the judge:\n%s", sub_id, traceback.format_exc())
        mark_error(conn, sub_id, f"internal judge error ({type(e).__name__}); an admin can rejudge it")


def run_once(conn, settings, sandbox=None, on_progress=None):
    """Judge one queued submission if there is one; returns its id or None."""
    heartbeat(conn)
    sub_id = claim(conn)
    if sub_id is not None:
        def progress(sid, done, total):
            heartbeat(conn)
            if on_progress:
                on_progress(sid, done, total)
        process(conn, settings, sub_id, sandbox=sandbox, on_progress=progress)
    return sub_id


def drain(conn, settings, sandbox=None, on_progress=None):
    """Judge until the queue is empty (used by tests and --once)."""
    ids = []
    while (sub_id := run_once(conn, settings, sandbox=sandbox, on_progress=on_progress)) is not None:
        ids.append(sub_id)
    return ids


def loop(settings, stop):
    conn = db.connect(settings.db)
    try:
        while not stop.is_set():
            try:
                if run_once(conn, settings) is None:
                    stop.wait(settings.poll_seconds)
            except Exception:
                log.error("worker loop error:\n%s", traceback.format_exc())
                stop.wait(5)
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m prplatform.worker")
    ap.add_argument("--once", action="store_true", help="judge everything queued, then exit")
    ap.add_argument("--workers", type=int, default=None, help="judging threads (default PR_WORKERS)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = config.load()
    if settings.sandbox == "local":
        log.warning("PR_SANDBOX=local: submissions run as this user with rlimits only. "
                    "This is NOT a security boundary; use it for development only.")
    db.init(settings.db)
    conn = db.connect(settings.db)
    n = reset_running(conn)
    if n:
        log.info("reset %d submission(s) stuck in 'running' back to 'queued'", n)
    if args.once:
        drain(conn, settings)
        return 0
    conn.close()
    stop = threading.Event()

    def on_signal(signum, frame):
        log.info("signal %d: stopping after the current submission", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    count = args.workers or settings.workers
    threads = [threading.Thread(target=loop, args=(settings, stop), name=f"judge-{i}", daemon=True)
               for i in range(count)]
    for t in threads:
        t.start()
    log.info("worker started: %d thread(s), sandbox=%s, db=%s", count, settings.sandbox, settings.db)
    while any(t.is_alive() for t in threads):
        for t in threads:
            t.join(timeout=0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())

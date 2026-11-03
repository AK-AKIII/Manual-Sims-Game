"""Poker Run judge runner (stdlib only). Runs inside the sandbox:

    python3 /runner.py /job 2.0

For each /job/inputs/*.txt in sorted order it runs `python3 /job/main.py` with
that file on stdin, in a fresh writable temp dir as cwd, with a per-test
wall-clock timeout. After each test it prints one JSON line (JSON Lines) and
flushes, so the worker can show progress:

    {"name": "0001", "status": "ok|tle|re", "time": 0.83, "stdout": "...", "stderr_tail": "..."}

stdout is capped at 1 MB (the rest is dropped, "stdout_truncated": true is
added); stderr keeps its last 2000 characters. Checking is not done here: the
worker checks stdout outside the sandbox.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

STDOUT_CAP = 1 << 20
STDERR_TAIL = 2000
FILE_LIMIT = 16 << 20   # bytes a solver may write to any one file (incl. its stdout)

_current = None


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _on_term(signum, frame):
    if _current is not None:
        _kill_group(_current)
    sys.exit(128 + signum)


def in_container():
    """True when this runner is the container's init (PID 1): then every other process
    in the PID namespace is something a solver started. Never true in the local sandbox,
    where sweeping would hit the worker's own user processes."""
    return os.getpid() == 1


def _reap():
    """As PID 1 we inherit orphans; reap them so zombies don't eat the pids limit."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _kill_others():
    """SIGKILL every process except this one. Repeat a few times, since a fork bomb
    can create new processes between passes (the pids limit bounds how many)."""
    me = os.getpid()
    for _ in range(20):
        others = [int(d) for d in os.listdir("/proc") if d.isdigit() and int(d) != me]
        if not others:
            return
        for pid in others:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        time.sleep(0.01)
        _reap()


def clear_dirs(roots, keep):
    """Delete everything under each root except the path `keep` (the runner's scratch)."""
    for root in roots:
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if os.path.abspath(path) == os.path.abspath(keep):
                continue
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.unlink(path)
            except OSError:
                pass


def sweep(scratch):
    """Between tests, inside the container: nothing a solver started or wrote may survive
    into the next test. A solver that escaped its process group (setsid + fork) could
    otherwise keep computing in the background, read the later inputs from /job, and
    stash answers in /tmp or /dev/shm, pooling the time limit across tests."""
    if not in_container():
        return
    _kill_others()
    _reap()
    clear_dirs(["/tmp", "/dev/shm"], keep=scratch)


def _child_limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_LIMIT, FILE_LIMIT))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


def _read_capped(f, cap):
    f.seek(0)
    data = f.read(cap + 1)
    return data[:cap], len(data) > cap


def run_one(main, path, time_limit, scratch):
    global _current
    cwd = tempfile.mkdtemp(prefix="run-", dir=scratch)
    out = tempfile.TemporaryFile(dir=scratch)
    err = tempfile.TemporaryFile(dir=scratch)
    try:
        with open(path, "rb") as stdin:
            t0 = time.monotonic()
            proc = subprocess.Popen([sys.executable, main], stdin=stdin, stdout=out, stderr=err,
                                    cwd=cwd, start_new_session=True, preexec_fn=_child_limits,
                                    close_fds=True)
            _current = proc
            try:
                proc.wait(timeout=time_limit)
                status = "ok" if proc.returncode == 0 else "re"
            except subprocess.TimeoutExpired:
                status = "tle"
            elapsed = time.monotonic() - t0
            # Kill the whole process group either way: no leftovers between tests.
            _kill_group(proc)
            proc.wait()
            _current = None
        stdout, truncated = _read_capped(out, STDOUT_CAP)
        err.seek(0, os.SEEK_END)
        size = err.tell()
        err.seek(max(0, size - 4 * STDERR_TAIL))
        stderr = err.read().decode("utf-8", "replace")[-STDERR_TAIL:]
        rec = {"name": os.path.splitext(os.path.basename(path))[0], "status": status,
               "time": round(elapsed, 3), "stdout": stdout.decode("utf-8", "replace"),
               "stderr_tail": stderr}
        if status == "re":
            rec["exit_code"] = proc.returncode
        if truncated:
            rec["stdout_truncated"] = True
        return rec
    finally:
        out.close()
        err.close()
        shutil.rmtree(cwd, ignore_errors=True)


def main(argv):
    if len(argv) != 3:
        print("usage: runner.py JOB_DIR TIME_LIMIT", file=sys.stderr)
        return 2
    job, time_limit = argv[1], float(argv[2])
    signal.signal(signal.SIGTERM, _on_term)
    main_py = os.path.join(job, "main.py")
    inputs_dir = os.path.join(job, "inputs")
    names = sorted(n for n in os.listdir(inputs_dir) if n.endswith(".txt"))
    scratch = tempfile.mkdtemp(prefix="judge-")
    try:
        for name in names:
            rec = run_one(main_py, os.path.join(inputs_dir, name), time_limit, scratch)
            sweep(scratch)
            sys.stdout.write(json.dumps(rec) + "\n")
            sys.stdout.flush()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

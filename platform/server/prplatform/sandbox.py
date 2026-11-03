"""Run judge/runner.py on a prepared job dir and stream its JSON Lines back.

Two backends (PR_SANDBOX):

* ``docker`` (production): one container per submission, the exact command
  from platform/API.md (plus ``--name`` so the worker can ``docker kill`` it
  on the overall timeout; killing the ``docker run`` client alone would leave
  the container running).

* ``local`` (DEVELOPMENT AND TESTS ONLY - NOT A SECURITY BOUNDARY): runs
  runner.py as a plain subprocess of the worker, as the worker's own user,
  with a scrubbed environment and ``resource`` rlimits (CPU seconds, address
  space, file size, core dumps, and on Linux processes). Submitted code can
  read and write anything that user can, talk to the network, read the
  database file, and leave background processes behind. Never use it with
  untrusted submissions.

Both stream: `run()` calls `on_record(dict)` for each JSON line as soon as the
runner prints it, and enforces the overall timeout
``tests * (time_limit + 1) + 30`` seconds, after which the run is killed.
"""

import json
import os
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

MAX_LINE = 8 << 20          # one JSON record: 1 MB stdout, JSON-escaped, plus stderr
STDERR_KEEP = 4000


class SandboxError(Exception):
    """The sandbox itself failed (not the submission)."""


@dataclass
class RunResult:
    returncode: int
    timed_out: bool
    stderr: str
    records: int


def overall_timeout(n_tests, time_limit):
    return n_tests * (time_limit + 1) + 30


def docker_command(settings, job_dir, name=None):
    """The `docker run` command from API.md. `name` (if given) is inserted as
    `--name NAME` right after `-i`."""
    mb = settings.memory_mb
    cmd = ["docker", "run", "--rm", "-i"]
    if name:
        cmd += ["--name", name]
    cmd += [
        "--network", "none", "--memory", f"{mb}m", "--memory-swap", f"{mb}m", "--cpus", "1",
        "--pids-limit", "64",
        "--read-only", "--tmpfs", "/tmp:rw,size=64m", "--user", "65534:65534", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "-v", f"{job_dir}:/job:ro", settings.judge_image,
        "python3", "/runner.py", "/job", format_seconds(settings.time_limit),
    ]
    return cmd


def format_seconds(x):
    return repr(float(x))


def _stream(proc, n_tests, deadline, on_record, kill):
    """Read JSON lines from proc.stdout until EOF or the deadline. Returns
    (timed_out, record count)."""
    lines = queue.Queue()

    def reader():
        try:
            while True:
                line = proc.stdout.readline(MAX_LINE + 1)
                if not line:
                    break
                lines.put(line)
                if len(line) > MAX_LINE:
                    break
        except Exception as e:  # pragma: no cover - pipe errors
            lines.put(e)
        finally:
            lines.put(None)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    count = 0
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            kill()
            return True, count
        try:
            item = lines.get(timeout=min(left, 0.5))
        except queue.Empty:
            continue
        if item is None:
            return False, count
        if isinstance(item, Exception):
            kill()
            raise SandboxError(f"reading runner output: {item}")
        if len(item) > MAX_LINE:
            kill()
            raise SandboxError("runner output line too long")
        if not item.strip():
            continue
        try:
            rec = json.loads(item)
        except ValueError:
            kill()
            raise SandboxError("runner printed something that is not JSON") from None
        if not isinstance(rec, dict):
            kill()
            raise SandboxError("runner printed a JSON value that is not an object")
        count += 1
        if count > n_tests:
            kill()
            raise SandboxError("runner printed more records than there are tests")
        on_record(rec)


def finally_close(proc):
    try:
        proc.stdout.close()
    except Exception:
        pass


def _drain_stderr(f):
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(max(0, size - STDERR_KEEP))
    return f.read().decode("utf-8", "replace")


class DockerSandbox:
    name = "docker"

    def __init__(self, settings):
        self.settings = settings

    def run(self, job_dir, n_tests, on_record):
        s = self.settings
        cname = f"pr-judge-{secrets.token_hex(6)}"
        cmd = docker_command(s, job_dir, name=cname)
        deadline = time.monotonic() + overall_timeout(n_tests, s.time_limit)
        with tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=err, start_new_session=True)
            except OSError as e:
                raise SandboxError(f"could not start docker: {e}") from None

            def kill():
                subprocess.run(["docker", "kill", cname], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30, check=False)
                try:
                    proc.kill()
                except OSError:
                    pass

            try:
                timed_out, count = _stream(proc, n_tests, deadline, on_record, kill)
            except BaseException:
                kill()
                proc.wait()
                finally_close(proc)
                raise
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill()
                proc.wait()
            finally_close(proc)
            return RunResult(proc.returncode, timed_out, _drain_stderr(err), count)


def _local_env():
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    if sys.platform == "darwin":  # some macOS Pythons need these to start
        for k in ("TMPDIR", "HOME"):
            if k in os.environ:
                env[k] = os.environ[k]
    return env


def _count_user_processes():
    try:
        uid = os.getuid()
        n = 0
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    if os.stat(f"/proc/{d}").st_uid == uid:
                        n += 1
                except OSError:
                    pass
        return n
    except OSError:
        return None


class LocalSandbox:
    """DEVELOPMENT ONLY. See the module docstring: this is not a security boundary."""

    name = "local"

    def __init__(self, settings):
        self.settings = settings

    def _limits(self, cpu_seconds):
        mem = self.settings.memory_mb * 1024 * 1024
        nproc = None
        if sys.platform.startswith("linux"):
            running = _count_user_processes()
            if running is not None:
                nproc = running + 64  # RLIMIT_NPROC counts all of this user's processes

        def apply():
            import resource
            for lim, val in ((resource.RLIMIT_CPU, cpu_seconds), (resource.RLIMIT_AS, mem),
                             (resource.RLIMIT_FSIZE, 64 << 20), (resource.RLIMIT_CORE, 0)):
                try:
                    soft, hard = resource.getrlimit(lim)
                    if hard != resource.RLIM_INFINITY:
                        val = min(val, hard)
                    resource.setrlimit(lim, (val, hard))
                except (ValueError, OSError):
                    pass  # e.g. RLIMIT_AS is not enforceable on macOS
            if nproc is not None:
                try:
                    resource.setrlimit(resource.RLIMIT_NPROC, (nproc, resource.getrlimit(resource.RLIMIT_NPROC)[1]))
                except (ValueError, OSError):
                    pass
        return apply

    def run(self, job_dir, n_tests, on_record):
        s = self.settings
        runner = s.runner_path()
        if not runner.is_file():
            raise SandboxError(f"runner not found at {runner}")
        total = overall_timeout(n_tests, s.time_limit)
        deadline = time.monotonic() + total
        scratch = tempfile.mkdtemp(prefix="pr-local-")
        env = _local_env()
        env["TMPDIR"] = scratch
        cmd = [sys.executable, "-I", str(runner), str(job_dir), format_seconds(s.time_limit)]
        with tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=err, cwd=scratch, env=env, start_new_session=True,
                                        preexec_fn=self._limits(int(total) + 1), close_fds=True)
            except OSError as e:
                shutil.rmtree(scratch, ignore_errors=True)
                raise SandboxError(f"could not start runner: {e}") from None

            def kill():
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    proc.kill()
                except OSError:
                    pass

            try:
                timed_out, count = _stream(proc, n_tests, deadline, on_record, kill)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    kill()
                    proc.wait()
            except BaseException:
                kill()
                proc.wait()
                finally_close(proc)
                raise
            finally:
                # Best effort: leftovers of the runner's own process group.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                shutil.rmtree(scratch, ignore_errors=True)
            finally_close(proc)
            return RunResult(proc.returncode, timed_out, _drain_stderr(err), count)


def get(settings):
    if settings.sandbox == "docker":
        return DockerSandbox(settings)
    return LocalSandbox(settings)

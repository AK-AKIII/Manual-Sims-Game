"""Settings from environment variables (see platform/API.md, "Configuration").

`load()` reads a mapping (os.environ by default) into an immutable Settings.
The api process keeps its Settings on `app.state.settings`; the worker loads
its own. Tests build Settings from a dict, so nothing reads os.environ late.
"""

import os
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULTS = {
    "PR_DB": "./data/platform.db",
    "PR_SECRET": "",
    "PR_INVITE": "",
    "PR_ADMINS": "",
    "PR_CONTEST_NAME": "Poker Run",
    "PR_START": "",
    "PR_END": "",
    "PR_FREEZE": "",
    "PR_HIDDEN_SEEDS": "1001-1050",
    "PR_PUBLIC_SEEDS": "1-10",
    "PR_TIME_LIMIT": "2.0",
    "PR_MEMORY_MB": "1024",
    "PR_MAX_SOURCE_KB": "256",
    "PR_FULL_PER_HOUR": "6",
    "PR_SANDBOX": "local",
    "PR_JUDGE_IMAGE": "poker-run-judge",
    "PR_SECURE_COOKIES": "0",
    # Not in API.md (extensions, documented in platform/server/README.md):
    "PR_WORKERS": "1",          # judge threads in one worker process
    "PR_RUNNER": "",            # path to judge/runner.py for the local sandbox
    "PR_POLL_SECONDS": "1.0",   # worker idle poll interval
    "PR_JOB_ROOT": "",          # where job temp dirs are made (docker: must be a host path)
}

MAX_SEEDS = 10_000


class ConfigError(ValueError):
    pass


def parse_seeds(text):
    """'81723,90211,1000-1047' -> [81723, 90211, 1000, ..., 1047] (order kept, no duplicates)."""
    out, seen = [], set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part[1:]:
                i = part.index("-", 1)
                lo, hi = int(part[:i]), int(part[i + 1:])
                if hi < lo:
                    raise ConfigError(f"bad seed range {part!r}")
                rng = range(lo, hi + 1)
            else:
                rng = [int(part)]
        except ValueError:
            raise ConfigError(f"bad seed {part!r}") from None
        for s in rng:
            if s < 0 or s > 0xFFFFFFFF:
                raise ConfigError(f"seed {s} out of range")
            if s not in seen:
                seen.add(s)
                out.append(s)
            if len(out) > MAX_SEEDS:
                raise ConfigError("too many seeds")
    return out


def parse_time(text, name):
    """ISO 8601 -> aware UTC datetime, or None when empty. Naive times are UTC."""
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ConfigError(f"{name}: not an ISO 8601 time: {text!r}") from None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


@dataclass(frozen=True)
class Settings:
    db: str
    secret: str
    invite: str
    admins: frozenset
    contest_name: str
    start: object
    end: object
    freeze: object
    hidden_seeds: tuple
    public_seeds: tuple
    time_limit: float
    memory_mb: int
    max_source_kb: int
    full_per_hour: int
    sandbox: str
    judge_image: str
    secure_cookies: bool
    workers: int = 1
    runner: str = ""
    poll_seconds: float = 1.0
    job_root: str = ""
    secret_generated: bool = field(default=False, compare=False)

    @property
    def max_source_bytes(self):
        return self.max_source_kb * 1024

    def seeds_for(self, kind):
        return list(self.public_seeds if kind == "public" else self.hidden_seeds)

    def is_open(self, now):
        if self.start and now < self.start:
            return False
        if self.end and now >= self.end:
            return False
        return True

    def frozen(self, now):
        return bool(self.freeze and now >= self.freeze)

    def runner_path(self):
        if self.runner:
            return Path(self.runner)
        return Path(__file__).resolve().parents[2] / "judge" / "runner.py"


def load(env=None):
    env = os.environ if env is None else env
    v = {k: str(env.get(k, d)) for k, d in DEFAULTS.items()}
    try:
        time_limit = float(v["PR_TIME_LIMIT"])
        memory_mb = int(v["PR_MEMORY_MB"])
        max_source_kb = int(v["PR_MAX_SOURCE_KB"])
        full_per_hour = int(v["PR_FULL_PER_HOUR"])
        workers = int(v["PR_WORKERS"])
        poll = float(v["PR_POLL_SECONDS"])
    except ValueError as e:
        raise ConfigError(f"bad number in configuration: {e}") from None
    if not (0 < time_limit <= 60):
        raise ConfigError("PR_TIME_LIMIT must be in (0, 60]")
    if not (64 <= memory_mb <= 65536):
        raise ConfigError("PR_MEMORY_MB must be in [64, 65536]")
    if not (1 <= max_source_kb <= 10240):
        raise ConfigError("PR_MAX_SOURCE_KB must be in [1, 10240]")
    if full_per_hour < 0 or not (1 <= workers <= 64) or poll <= 0:
        raise ConfigError("bad PR_FULL_PER_HOUR / PR_WORKERS / PR_POLL_SECONDS")
    sandbox = v["PR_SANDBOX"].strip().lower()
    if sandbox not in ("docker", "local"):
        raise ConfigError("PR_SANDBOX must be 'docker' or 'local'")
    hidden = parse_seeds(v["PR_HIDDEN_SEEDS"])
    public = parse_seeds(v["PR_PUBLIC_SEEDS"])
    if not hidden or not public:
        raise ConfigError("PR_HIDDEN_SEEDS and PR_PUBLIC_SEEDS must not be empty")
    secret = v["PR_SECRET"]
    generated = False
    if not secret:
        secret, generated = secrets.token_hex(32), True
        if sandbox == "docker" or v["PR_SECURE_COOKIES"] == "1":
            print("warning: PR_SECRET is not set; using a random one", file=sys.stderr)
    return Settings(
        db=v["PR_DB"],
        secret=secret,
        invite=v["PR_INVITE"],
        admins=frozenset(a.strip().lower() for a in v["PR_ADMINS"].split(",") if a.strip()),
        contest_name=v["PR_CONTEST_NAME"],
        start=parse_time(v["PR_START"], "PR_START"),
        end=parse_time(v["PR_END"], "PR_END"),
        freeze=parse_time(v["PR_FREEZE"], "PR_FREEZE"),
        hidden_seeds=tuple(hidden),
        public_seeds=tuple(public),
        time_limit=time_limit,
        memory_mb=memory_mb,
        max_source_kb=max_source_kb,
        full_per_hour=full_per_hour,
        sandbox=sandbox,
        judge_image=v["PR_JUDGE_IMAGE"],
        secure_cookies=v["PR_SECURE_COOKIES"] == "1",
        workers=workers,
        runner=v["PR_RUNNER"],
        poll_seconds=poll,
        job_root=v["PR_JOB_ROOT"],
        secret_generated=generated,
    )

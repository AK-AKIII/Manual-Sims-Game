"""SQLite storage (stdlib sqlite3). Schema from platform/API.md, created or
migrated on start; WAL mode so the api and the worker can share the file.

Timestamps are stored as fixed-width UTC ISO strings
('2026-11-03T12:00:00.000000Z'), so string order equals time order.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    created TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created TEXT NOT NULL,
    expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('public', 'full')),
    source TEXT NOT NULL,
    created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'error')),
    started TEXT,
    finished TEXT,
    error TEXT,
    raw_total INTEGER,
    cleared_total INTEGER,
    max_time REAL,
    tests_ac INTEGER,
    tests_total INTEGER,
    tests_done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS results (
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    seed INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    cleared INTEGER NOT NULL DEFAULT 0,
    time REAL,
    message TEXT,
    PRIMARY KEY (submission_id, seed)
);
CREATE INDEX IF NOT EXISTS submissions_user ON submissions(user_id, id);
CREATE INDEX IF NOT EXISTS submissions_status ON submissions(status, id);
CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires);
"""

# Columns added after the first version: (table, column, declaration).
MIGRATIONS = [
    ("submissions", "tests_done", "INTEGER NOT NULL DEFAULT 0"),
]


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now():
    return datetime.now(timezone.utc)


def now_iso():
    return iso(now())


def parse_iso(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def connect(path):
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI runs a request's sync dependencies and endpoint on
    # different threadpool threads. Each request still owns its connection and uses it
    # one step at a time, so this never shares a connection concurrently.
    conn = sqlite3.connect(path, timeout=30, isolation_level=None,  # autocommit; explicit BEGIN
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init(path):
    """Create or migrate the schema and switch to WAL. Safe to call repeatedly."""
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    finally:
        conn.close()


class transaction:
    """`with transaction(conn, immediate=True): ...` -> BEGIN [IMMEDIATE] / COMMIT / ROLLBACK."""

    def __init__(self, conn, immediate=False):
        self.conn, self.immediate = conn, immediate

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE" if self.immediate else "BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        return False

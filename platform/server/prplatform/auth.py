"""Passwords (hashlib.scrypt) and sessions (random cookie token, SHA-256 in the DB)."""

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import timedelta

from . import db

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
SCRYPT_DKLEN = 32
SESSION_DAYS = 14
COOKIE = "pr_session"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
PASSWORD_MIN, PASSWORD_MAX = 8, 256


def hash_password(password, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=SCRYPT_DKLEN,
                       maxmem=64 * 1024 * 1024)
    return f"scrypt${n}${r}${p}${salt.hex()}${h.hex()}"


def verify_password(password, stored):
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r),
                           p=int(p), dklen=len(expected), maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(h, expected)


# A hash to verify against when the username doesn't exist, so a login for an
# unknown user takes as long as one with a wrong password.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def dummy_verify(password):
    verify_password(password, _DUMMY_HASH)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    t = db.now()
    conn.execute("INSERT INTO sessions(token_hash, user_id, created, expires) VALUES (?, ?, ?, ?)",
                 (token_hash(token), user_id, db.iso(t), db.iso(t + timedelta(days=SESSION_DAYS))))
    # Opportunistic cleanup of expired sessions.
    conn.execute("DELETE FROM sessions WHERE expires < ?", (db.iso(t),))
    return token


def session_user(conn, token):
    if not token or len(token) > 200:
        return None
    return conn.execute(
        "SELECT u.id, u.username, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires > ?", (token_hash(token), db.now_iso())).fetchone()


def delete_session(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


def is_admin(settings, user):
    return bool(user and (user["is_admin"] or user["username"].lower() in settings.admins))


class FailureLimiter:
    """In-memory limit on failed logins per (client ip, username): `limit` per `window` s.
    Not in API.md; protects scrypt CPU and passwords from guessing."""

    def __init__(self, limit=10, window=900):
        self.limit, self.window = limit, window
        self._lock = threading.Lock()
        self._hits = {}

    def _prune(self, key, t):
        hits = [h for h in self._hits.get(key, []) if t - h < self.window]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def blocked(self, key):
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.limit

    def fail(self, key):
        with self._lock:
            t = time.monotonic()
            self._prune(key, t)
            self._hits.setdefault(key, []).append(t)
            if len(self._hits) > 100_000:  # bound memory
                self._hits.clear()

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)

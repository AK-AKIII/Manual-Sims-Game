"""Admin operations on users and submissions (platform/API.md, "Admin API"),
shared by the HTTP API and the `manage` CLI. Each state-changing function
expects to run inside a write transaction and writes its own audit row.

`actor` is {"id": user id or None, "username": text}; the CLI uses
{"id": None, "username": "(cli)"}.
"""

import json

from . import auth, db, scoring

CLI_ACTOR = {"id": None, "username": "(cli)"}
CANCELLED_DISABLED = "cancelled: account disabled"
CANCELLED_ADMIN = "cancelled by an admin"
# Submissions in status 'error' whose error starts with this were cancelled, not broken.
CANCELLED_PREFIX = "cancelled"

USER_COLS = "id, username, created, is_admin, last_login, disabled_at, disabled_reason, email"


class AdminError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def audit(conn, actor, action, target, details=None):
    conn.execute("INSERT INTO audit(created, actor, action, target, details) VALUES (?, ?, ?, ?, ?)",
                 (db.now_iso(), actor["username"], action, target, json.dumps(details or {}, sort_keys=True)))


def audit_rows(conn, actor=None, action=None, target=None, limit=200):
    sql, args = "SELECT id, created, actor, action, target, details FROM audit WHERE 1 = 1", []
    if actor:
        sql += " AND actor = ? COLLATE NOCASE"
        args.append(actor)
    if target:
        sql += " AND target = ? COLLATE NOCASE"
        args.append(target)
    if action:
        if action.endswith("."):
            sql += " AND substr(action, 1, ?) = ?"
            args += [len(action), action]
        else:
            sql += " AND action = ?"
            args.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 2000)))
    out = []
    for r in conn.execute(sql, args):
        try:
            details = json.loads(r["details"])
        except ValueError:
            details = {"raw": r["details"]}
        out.append({"id": r["id"], "created": r["created"], "actor": r["actor"], "action": r["action"],
                    "target": r["target"], "details": details})
    return out


# ---------------------------------------------------------------------------- users

def get_user(conn, uid):
    row = conn.execute(f"SELECT {USER_COLS} FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        raise AdminError(404, "user not found")
    return row


def find_user(conn, username):
    row = conn.execute(f"SELECT {USER_COLS} FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        raise AdminError(404, f"no user {username!r}")
    return row


def in_env_admins(settings, row):
    return row["username"].lower() in settings.admins


def effective_admin(settings, row):
    return bool(row["is_admin"]) or in_env_admins(settings, row)


def active_admin_count(conn, settings):
    rows = conn.execute("SELECT username, is_admin FROM users WHERE disabled_at IS NULL").fetchall()
    return sum(1 for r in rows if effective_admin(settings, r))


def _guard(conn, settings, actor, target, verb):
    """Refuse to take admin power away from yourself or from the last active admin."""
    if actor["id"] is not None and actor["id"] == target["id"]:
        raise AdminError(409, f"you can't {verb} your own account")
    if (effective_admin(settings, target) and target["disabled_at"] is None
            and active_admin_count(conn, settings) <= 1):
        raise AdminError(409, f"{target['username']} is the last active admin; make someone else an admin first")


def user_rows(conn, settings, uid=None):
    """User rows `U` (see API.md), by id; only `uid` when given."""
    sql = (f"SELECT u.id, u.username, u.email, u.created, u.is_admin, u.last_login, u.disabled_at, u.disabled_reason, "
           "COALESCE(SUM(s.kind = 'public'), 0) AS n_public, COALESCE(SUM(s.kind = 'full'), 0) AS n_full, "
           "MAX(s.created) AS last_sub FROM users u LEFT JOIN submissions s ON s.user_id = u.id")
    args = []
    if uid is not None:
        sql += " WHERE u.id = ?"
        args.append(uid)
    sql += " GROUP BY u.id ORDER BY u.id"
    rows = conn.execute(sql, args).fetchall()
    standing = {e["user_id"]: e for e in scoring.standings(conn, settings, include_disabled=True)}
    out = []
    for r in rows:
        e = standing.get(r["id"])
        out.append({
            "id": r["id"],
            "username": r["username"],
            "email": r["email"],
            "created": r["created"],
            "is_admin": effective_admin(settings, r),
            "admin_env": in_env_admins(settings, r),
            "status": "disabled" if r["disabled_at"] else "active",
            "disabled_at": r["disabled_at"],
            "disabled_reason": r["disabled_reason"],
            "last_login": r["last_login"],
            "public_submissions": r["n_public"],
            "full_submissions": r["n_full"],
            "best_relative_total": e["relative_total"] if e else None,
            "best_submission_id": e["best_id"] if e else None,
            "rank": e["rank"] if e else None,
            "last_submission": r["last_sub"],
        })
    return out


def summary(rows):
    return {
        "registered": len(rows),
        "active": sum(r["status"] == "active" for r in rows),
        "disabled": sum(r["status"] == "disabled" for r in rows),
        "admins": sum(bool(r["is_admin"]) for r in rows),
        "with_full": sum(r["best_relative_total"] is not None for r in rows),
    }


def disable(conn, settings, actor, uid, reason=None):
    """Returns (changed, cancelled submission ids)."""
    target = get_user(conn, uid)
    if target["disabled_at"]:
        return False, []
    _guard(conn, settings, actor, target, "disable")
    reason = (reason or "").strip()[:200] or None
    conn.execute("UPDATE users SET disabled_at = ?, disabled_reason = ? WHERE id = ?",
                 (db.now_iso(), reason, uid))
    sessions = conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,)).rowcount
    cancelled = [r[0] for r in conn.execute("SELECT id FROM submissions WHERE user_id = ? AND status = 'queued'",
                                            (uid,))]
    if cancelled:
        conn.execute("UPDATE submissions SET status = 'error', finished = ?, error = ? "
                     "WHERE user_id = ? AND status = 'queued'", (db.now_iso(), CANCELLED_DISABLED, uid))
    running = conn.execute("SELECT COUNT(*) FROM submissions WHERE user_id = ? AND status = 'running'",
                           (uid,)).fetchone()[0]
    audit(conn, actor, "user.disable", target["username"],
          {"reason": reason, "sessions_revoked": sessions, "cancelled": cancelled, "running_left": running})
    return True, cancelled


def enable(conn, settings, actor, uid):
    target = get_user(conn, uid)
    if not target["disabled_at"]:
        return False
    conn.execute("UPDATE users SET disabled_at = NULL, disabled_reason = NULL WHERE id = ?", (uid,))
    audit(conn, actor, "user.enable", target["username"], {"was_disabled_at": target["disabled_at"]})
    return True


def set_admin(conn, settings, actor, uid, flag):
    target = get_user(conn, uid)
    flag = bool(flag)
    if not flag:
        if in_env_admins(settings, target):
            raise AdminError(409, f"{target['username']} is listed in PR_ADMINS, so they stay an admin; "
                                  "edit PR_ADMINS and restart the api to remove them")
        if not target["is_admin"]:
            return False
        _guard(conn, settings, actor, target, "remove admin from")
    elif target["is_admin"]:
        return False
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(flag), uid))
    audit(conn, actor, "user.admin", target["username"], {"is_admin": flag})
    return True


def set_email(conn, actor, uid, email):
    """Admin edit of a user's email; None or "" clears it."""
    target = get_user(conn, uid)
    if email is None or (isinstance(email, str) and not email.strip()):
        new = None
    else:
        new = auth.normalise_email(email)
        if new is None:
            raise AdminError(400, "enter a valid email address")
        if conn.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (new, uid)).fetchone():
            raise AdminError(409, "email already registered")
    if new == target["email"]:
        return False
    conn.execute("UPDATE users SET email = ? WHERE id = ?", (new, uid))
    audit(conn, actor, "user.email", target["username"],
          {"old": auth.mask_email(target["email"]), "new": auth.mask_email(new)})
    return True


def check_password(password):
    if not isinstance(password, str) or not (auth.PASSWORD_MIN <= len(password) <= auth.PASSWORD_MAX):
        raise AdminError(400, f"password must be {auth.PASSWORD_MIN}-{auth.PASSWORD_MAX} characters")


def reset_password(conn, settings, actor, uid, password, keep_token_hash=None, pw_hash=None):
    """Sets the password and revokes the user's sessions (except `keep_token_hash`,
    the caller's own session when an admin resets their own password)."""
    target = get_user(conn, uid)
    check_password(password)
    conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (pw_hash or auth.hash_password(password), uid))
    if keep_token_hash and actor["id"] == uid:
        n = conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                         (uid, keep_token_hash)).rowcount
    else:
        n = conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,)).rowcount
    audit(conn, actor, "user.password", target["username"], {"sessions_revoked": n})
    return n


def delete_user(conn, settings, actor, uid, confirm):
    target = get_user(conn, uid)
    if not isinstance(confirm, str) or confirm.strip().lower() != target["username"].lower():
        raise AdminError(400, "type the username to confirm the deletion")
    _guard(conn, settings, actor, target, "delete")
    if conn.execute("SELECT 1 FROM submissions WHERE user_id = ? AND status = 'running' LIMIT 1",
                    (uid,)).fetchone():
        raise AdminError(409, "one of their submissions is being judged right now; disable the account, "
                              "wait for it to finish, then delete")
    counts = conn.execute("SELECT COUNT(*), COALESCE(SUM(kind = 'full'), 0) FROM submissions WHERE user_id = ?",
                          (uid,)).fetchone()
    # Explicit deletes (the schema's ON DELETE CASCADE would do the same).
    conn.execute("DELETE FROM results WHERE submission_id IN (SELECT id FROM submissions WHERE user_id = ?)", (uid,))
    conn.execute("DELETE FROM submissions WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    audit(conn, actor, "user.delete", target["username"],
          {"user_id": uid, "created": target["created"], "submissions": counts[0], "full_submissions": counts[1],
           "was_admin": effective_admin(settings, target)})
    return target["username"], counts[0]


# ---------------------------------------------------------------------------- submissions

def _get_sub(conn, sid):
    r = conn.execute("SELECT s.id, s.user_id, s.kind, s.status, s.error, s.raw_total, s.created, u.username "
                     "FROM submissions s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (sid,)).fetchone()
    if r is None:
        raise AdminError(404, "submission not found")
    return r


def delete_submission(conn, settings, actor, sid):
    r = _get_sub(conn, sid)
    if r["status"] == "running":
        raise AdminError(409, "the submission is being judged right now; wait for it to finish, then delete it")
    rel = None
    if r["kind"] == "full" and r["status"] == "done":
        rel = scoring.compute(conn, settings).totals.get(sid, 0)
    conn.execute("DELETE FROM results WHERE submission_id = ?", (sid,))
    conn.execute("DELETE FROM submissions WHERE id = ?", (sid,))
    audit(conn, actor, "submission.delete", r["username"],
          {"submission_id": sid, "kind": r["kind"], "status": r["status"], "created": r["created"],
           "raw_total": r["raw_total"], "relative_total": rel})


def cancel_submission(conn, actor, sid):
    r = _get_sub(conn, sid)
    if r["status"] != "queued":
        raise AdminError(409, f"only queued submissions can be cancelled (this one is {r['status']})")
    conn.execute("UPDATE submissions SET status = 'error', finished = ?, error = ? WHERE id = ? AND status = 'queued'",
                 (db.now_iso(), CANCELLED_ADMIN, sid))
    audit(conn, actor, "submission.cancel", r["username"], {"submission_id": sid, "kind": r["kind"]})


RESET_COLUMNS = ("status = 'queued', started = NULL, finished = NULL, error = NULL, raw_total = NULL, "
                 "cleared_total = NULL, max_time = NULL, tests_ac = NULL, tests_done = 0")


def rejudge(conn, actor, sid):
    r = _get_sub(conn, sid)
    if r["status"] == "running":
        raise AdminError(409, "the submission is being judged right now")
    conn.execute("DELETE FROM results WHERE submission_id = ?", (sid,))
    conn.execute(f"UPDATE submissions SET {RESET_COLUMNS} WHERE id = ?", (sid,))
    audit(conn, actor, "submission.rejudge", r["username"],
          {"submission_id": sid, "kind": r["kind"], "previous_status": r["status"]})


def rejudge_bulk(conn, actor, which):
    if which == "errored":
        cond = "s.status = 'error'"
    elif which == "full":
        cond = "s.kind = 'full' AND s.status IN ('done', 'error')"
    else:
        raise AdminError(400, "which must be 'errored' or 'full'")
    ids = [r[0] for r in conn.execute(
        f"SELECT s.id FROM submissions s JOIN users u ON u.id = s.user_id WHERE {cond} "
        "AND u.disabled_at IS NULL AND NOT (s.status = 'error' AND COALESCE(s.error, '') LIKE ?) ORDER BY s.id",
        (CANCELLED_PREFIX + "%",))]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM results WHERE submission_id IN ({marks})", chunk)
        conn.execute(f"UPDATE submissions SET {RESET_COLUMNS} WHERE id IN ({marks})", chunk)
    audit(conn, actor, "submission.rejudge_bulk", None,
          {"which": which, "count": len(ids), "ids": ids[:200], "ids_truncated": len(ids) > 200})
    return ids

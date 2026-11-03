"""Relative scoring and the leaderboard (platform/API.md, "Scoring").

relative(test) = round(1_000_000 * raw / best), best = highest raw score on that
seed over all finished full submissions (0 if best is 0). A submission's
relative total is the sum over the hidden seeds; it is computed when read.

Freeze: non-admins see everything computed only from full submissions created
before PR_FREEZE. For a non-admin's own submission list during the freeze, the
bests come from pre-freeze submissions plus that user's own submissions, so
the user sees how their new submissions compare without learning anything
about other people's post-freeze progress.
"""

import threading
from collections import OrderedDict

from . import db


def relative(raw, best):
    return round(1_000_000 * raw / best) if best > 0 else 0


class Board:
    """Bests per hidden seed and relative totals per finished full submission."""

    def __init__(self, best, totals):
        self.best = best        # seed -> best raw
        self.totals = totals    # submission id -> relative total


_cache_lock = threading.Lock()
_cache = OrderedDict()
_CACHE_SIZE = 64


def _signature(conn):
    r = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(finished), '') "
                     "FROM submissions WHERE kind = 'full' AND status = 'done'").fetchone()
    return tuple(r)


def compute(conn, settings, cutoff=None, include_user=None):
    """Board over finished full submissions; with `cutoff` (datetime) only those
    created before it, plus all of `include_user`'s own submissions."""
    hidden = tuple(settings.hidden_seeds)
    key = (settings.db, hidden, cutoff and db.iso(cutoff), include_user)
    sig = _signature(conn)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == sig:
            _cache.move_to_end(key)
            return hit[1]
    sql = ("SELECT r.submission_id, r.seed, r.score FROM results r "
           "JOIN submissions s ON s.id = r.submission_id "
           "WHERE s.kind = 'full' AND s.status = 'done'")
    args = []
    if cutoff is not None:
        sql += " AND (s.created < ? OR s.user_id = ?)"
        args += [db.iso(cutoff), include_user if include_user is not None else -1]
    hidden_set = set(hidden)
    rows = [r for r in conn.execute(sql, args) if r["seed"] in hidden_set]
    best = {s: 0 for s in hidden}
    for r in rows:
        if r["score"] > best[r["seed"]]:
            best[r["seed"]] = r["score"]
    totals = {}
    for r in rows:
        totals[r["submission_id"]] = totals.get(r["submission_id"], 0) + relative(r["score"], best[r["seed"]])
    # Done submissions without any rows (shouldn't happen) still get a total of 0.
    board = Board(best, totals)
    with _cache_lock:
        _cache[key] = (sig, board)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return board


def board_for_viewer(conn, settings, user_id, admin, now):
    """The Board a viewer's own submission list is scored against."""
    if admin or not settings.frozen(now):
        return compute(conn, settings)
    return compute(conn, settings, cutoff=settings.freeze, include_user=user_id)


def leaderboard(conn, settings, admin, now):
    frozen = settings.frozen(now) and not admin
    cutoff = settings.freeze if frozen else None
    board = compute(conn, settings, cutoff=cutoff, include_user=None)
    sql = ("SELECT s.id, s.user_id, u.username, s.created, s.raw_total, s.tests_ac "
           "FROM submissions s JOIN users u ON u.id = s.user_id "
           "WHERE s.kind = 'full' AND s.status = 'done'")
    args = []
    if cutoff is not None:
        sql += " AND s.created < ?"
        args.append(db.iso(cutoff))
    sql += " ORDER BY s.id"
    per_user = {}
    for s in conn.execute(sql, args):
        rel = board.totals.get(s["id"], 0)
        u = per_user.get(s["user_id"])
        if u is None:
            per_user[s["user_id"]] = u = {"best": None, "rel": -1, "count": 0}
        u["count"] += 1
        # Strictly greater: on equal totals the earlier submission (lower id) stays.
        if rel > u["rel"]:
            u["best"], u["rel"] = s, rel
    entries = sorted(per_user.values(), key=lambda u: (-u["rel"], u["best"]["created"], u["best"]["id"]))
    rows = []
    for i, u in enumerate(entries, 1):
        s = u["best"]
        rows.append({
            "rank": i,
            "username": s["username"],
            "relative_total": u["rel"],
            "raw_total": s["raw_total"],
            "tests_ac": s["tests_ac"],
            "submitted": s["created"],
            "submissions": u["count"],
        })
    return {"frozen": frozen, "rows": rows}

"""Relative scoring and the leaderboard (platform/API.md, "Scoring").

relative(test) = round(1_000_000 * raw / best), best = highest raw score on that
seed over all finished full submissions (0 if best is 0). A submission's
relative total is the sum over the hidden seeds; it is computed when read and
is what ranking uses (an exact integer, so ties are exact).

The score people see is the normalised `score` = 100 * mean over the hidden
tests of raw / best, with 2 decimals (0.00 to 100.00). It is derived from the
relative total (relative_total / (10_000 * hidden tests)), so it is a monotonic
rescaling of it: ordering by score and by relative total always agree. It
matches the exact mean to within 0.00005 (the per-test rounding).

Freeze: non-admins see everything computed only from full submissions created
before PR_FREEZE. For a non-admin's own submission list during the freeze, the
bests come from pre-freeze submissions plus that user's own submissions, so
the user sees how their new submissions compare without learning anything
about other people's post-freeze progress.

Disabled users: their submissions never set the per-seed bests and they are
not ranked. Admins see them after the ranked rows (rank None, disabled True),
scored against the active users' bests.
"""

import threading
from collections import OrderedDict

from . import db


def relative(raw, best):
    return round(1_000_000 * raw / best) if best > 0 else 0


def normalised(rel, n_tests):
    """Contest score 0..100 (2 decimals) from a relative total over n_tests hidden tests."""
    if rel is None:
        return None
    if not n_tests:
        return 0.0
    return round(rel / (10_000 * n_tests), 2)


def score_of(rel, settings):
    return normalised(rel, len(settings.hidden_seeds))


def score_text(score):
    """A score as CSV text with exactly 2 decimals ("48.20"); "" for None."""
    return "" if score is None else f"{score:.2f}"


def relative_pct(raw, best):
    """One test's raw / best as a percentage of the best (2 decimals); 0 when best is 0."""
    return round(100 * raw / best, 2) if best and best > 0 and raw is not None else 0.0


class Board:
    """Bests per hidden seed and relative totals per finished full submission."""

    def __init__(self, best, totals):
        self.best = best        # seed -> best raw
        self.totals = totals    # submission id -> relative total


_cache_lock = threading.Lock()
_cache = OrderedDict()
_CACHE_SIZE = 64


def _signature(conn):
    r = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(finished), ''), "
                     "COALESCE(SUM(id), 0) FROM submissions WHERE kind = 'full' AND status = 'done'").fetchone()
    disabled = conn.execute("SELECT group_concat(id) FROM (SELECT id FROM users "
                            "WHERE disabled_at IS NOT NULL ORDER BY id)").fetchone()[0]
    return tuple(r) + (disabled or "",)


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
           "JOIN submissions s ON s.id = r.submission_id JOIN users u ON u.id = s.user_id "
           "WHERE s.kind = 'full' AND s.status = 'done'")
    args = []
    # Disabled users don't set the bests (their own totals are scored against the others').
    rows_sql = sql
    sql += " AND u.disabled_at IS NULL"
    if cutoff is not None:
        sql += " AND (s.created < ? OR s.user_id = ?)"
        args += [db.iso(cutoff), include_user if include_user is not None else -1]
    hidden_set = set(hidden)
    rows = [r for r in conn.execute(sql, args) if r["seed"] in hidden_set]
    best = {s: 0 for s in hidden}
    for r in rows:
        if r["score"] > best[r["seed"]]:
            best[r["seed"]] = r["score"]
    disabled_rows = [r for r in conn.execute(rows_sql + " AND u.disabled_at IS NOT NULL")
                     if r["seed"] in hidden_set]
    totals = {}
    for r in rows + disabled_rows:
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


def standings(conn, settings, cutoff=None, include_disabled=False):
    """Each user's best finished full submission, ranked: a list of dicts with
    user_id, username, disabled, rank (None for disabled users, listed last),
    relative_total, score (0..100), raw_total, tests_ac, best_id, submitted, submissions."""
    board = compute(conn, settings, cutoff=cutoff, include_user=None)
    sql = ("SELECT s.id, s.user_id, u.username, u.disabled_at, s.created, s.raw_total, s.tests_ac "
           "FROM submissions s JOIN users u ON u.id = s.user_id "
           "WHERE s.kind = 'full' AND s.status = 'done'")
    args = []
    if not include_disabled:
        sql += " AND u.disabled_at IS NULL"
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
    key = lambda u: (-u["rel"], u["best"]["created"], u["best"]["id"])  # noqa: E731
    active = sorted((u for u in per_user.values() if u["best"]["disabled_at"] is None), key=key)
    disabled = sorted((u for u in per_user.values() if u["best"]["disabled_at"] is not None), key=key)
    out = []
    for i, u in enumerate(active + disabled, 1):
        s = u["best"]
        dis = s["disabled_at"] is not None
        out.append({
            "user_id": s["user_id"],
            "username": s["username"],
            "disabled": dis,
            "rank": None if dis else i,
            "relative_total": u["rel"],
            "score": score_of(u["rel"], settings),
            "raw_total": s["raw_total"],
            "tests_ac": s["tests_ac"],
            "best_id": s["id"],
            "submitted": s["created"],
            "submissions": u["count"],
        })
    return out


def leaderboard(conn, settings, admin, now):
    frozen = settings.frozen(now) and not admin
    cutoff = settings.freeze if frozen else None
    rows = []
    for e in standings(conn, settings, cutoff=cutoff, include_disabled=admin):
        row = {k: e[k] for k in ("rank", "username", "score", "relative_total", "raw_total", "tests_ac", "submitted",
                                 "submissions")}
        if e["disabled"]:
            row["disabled"] = True
        rows.append(row)
    return {"frozen": frozen, "rows": rows}

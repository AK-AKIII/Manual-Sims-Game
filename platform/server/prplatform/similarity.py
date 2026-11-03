"""Similarity check between students' latest full submissions (plagiarism helper,
platform/API.md "Similarity").

Explainable on purpose: tokens from Python's `tokenize` with comments, blank
lines, indentation and whitespace dropped and string literals replaced by STR;
the set of token 5-grams minus every 5-gram of the official starter.py; Jaccard
similarity |A & B| / |A | B| of two sets. Grams are hashed to ints and sets
are intersected pairwise (pairs whose sizes alone rule out the threshold are
skipped); a time budget stops early and reports `truncated`.
"""

import io
import re
import threading
import time
import tokenize
from pathlib import Path

N = 5
MIN_GRAMS = 20
MAX_STUDENTS = 600
MIN_SIMILARITY = 0.2
TIME_BUDGET = 15.0
METHOD = ("Jaccard similarity of token 5-grams (Python tokenize; comments, whitespace and indentation "
          "ignored, string literals normalised to STR), after removing every 5-gram that appears in "
          "the official starter.py. Each student's latest full submission is compared.")

_SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
         tokenize.ENCODING, tokenize.ENDMARKER}
_FALLBACK = re.compile(r"""[A-Za-z_]\w*|\d[\w.]*|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|\S""")


def tokens(source):
    out = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(source).readline):
            if t.type in _SKIP:
                continue
            out.append("STR" if t.type == tokenize.STRING else t.string)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # Not valid Python: a plain regex tokeniser, comments stripped first.
        text = re.sub(r"#[^\n]*", "", source)
        out = []
        for m in _FALLBACK.finditer(text):
            tok = m.group(0)
            out.append("STR" if tok[0] in "'\"" else tok)
    return out


def grams(source):
    toks = tokens(source)
    return {hash("\x00".join(toks[i:i + N])) for i in range(len(toks) - N + 1)}


_starter_cache = {}


def starter_path():
    # Through the package, so it works both in the repo and in the Docker image (/app/poker_run).
    from . import judge  # noqa: F401  (makes `poker_run` importable, see judge._import_poker_run)
    import poker_run
    return Path(poker_run.__file__).resolve().parent / "starters" / "starter.py"


def starter_grams():
    path = starter_path()
    key = str(path)
    if key not in _starter_cache:
        try:
            _starter_cache[key] = grams(path.read_text(encoding="utf-8"))
        except OSError:
            _starter_cache[key] = set()
    return _starter_cache[key]


def latest_full(conn):
    """[(username, submission id, source)] for each user's latest full submission, newest first."""
    return conn.execute(
        "SELECT u.username, s.id, s.source FROM submissions s JOIN users u ON u.id = s.user_id "
        "WHERE s.id IN (SELECT MAX(id) FROM submissions WHERE kind = 'full' GROUP BY user_id) "
        "ORDER BY s.id DESC").fetchall()


_result_lock = threading.Lock()
_result_cache = {}


def check(conn, limit=50, scope=""):
    """The API response for the current latest full submissions. The full pair list is
    cached per set of (username, submission id), so repeated runs are instant until
    someone submits again (sources never change under an id)."""
    rows = latest_full(conn)
    key = (scope,) + tuple((r["username"], r["id"]) for r in rows)
    with _result_lock:
        hit = _result_cache.get(key)
    if hit is None:
        hit = compare([(r["username"], r["id"], r["source"]) for r in rows], limit=None)
        with _result_lock:
            _result_cache.clear()  # only the latest state is worth keeping
            _result_cache[key] = hit
    return dict(hit, pairs=hit["pairs"][:limit])


def compare(entries, limit=50, starter=None, max_students=MAX_STUDENTS, budget=TIME_BUDGET):
    """entries: [(username, submission id, source)]. Returns the API response
    (`limit` None = every pair above the threshold)."""
    t0 = time.monotonic()
    starter = starter_grams() if starter is None else starter
    truncated = len(entries) > max_students
    entries = entries[:max_students]
    sets, people, skipped = [], [], 0
    for username, sid, source in entries:
        g = grams(source) - starter
        if len(g) < MIN_GRAMS:
            skipped += 1
            continue
        sets.append(g)
        people.append({"username": username, "submission_id": sid})
    # Plain pairwise set intersections (C-speed), skipping pairs whose sizes alone rule out
    # MIN_SIMILARITY (Jaccard <= min/max size). 600 students = 180k pairs, a few seconds at most.
    order = sorted(range(len(sets)), key=lambda i: len(sets[i]))
    pairs = []
    for x, i in enumerate(order):
        si, ni = sets[i], len(sets[i])
        for j in order[x + 1:]:
            nj = len(sets[j])
            if ni < MIN_SIMILARITY * nj:
                break  # sizes only grow from here
            inter = len(si & sets[j])
            sim = inter / (ni + nj - inter)
            if sim >= MIN_SIMILARITY:
                a, b = (i, j) if i < j else (j, i)
                pairs.append({"a": people[a], "b": people[b], "similarity": round(sim, 4), "shared": inter,
                              "a_size": len(sets[a]), "b_size": len(sets[b])})
        if time.monotonic() - t0 > budget:
            truncated = True
            break
    pairs.sort(key=lambda p: (-p["similarity"], p["a"]["username"].lower(), p["b"]["username"].lower()))
    return {"pairs": pairs if limit is None else pairs[:limit], "compared": len(sets), "skipped": skipped, "truncated": truncated,
            "method": METHOD}

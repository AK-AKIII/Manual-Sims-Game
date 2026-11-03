"""The HTTP API (platform/API.md, "HTTP API"). Run with `uvicorn prplatform.app:app`.

This process never runs submitted code: it only stores submissions; the
worker (prplatform.worker) judges them in the sandbox.
"""

import threading
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, config, db, scoring

STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RegisterBody(Body):
    username: str = ""
    password: str = ""
    invite: str = ""


class LoginBody(Body):
    username: str = ""
    password: str = ""


class SubmitBody(Body):
    kind: str = ""
    source: str = ""


def create_app(settings=None):
    settings = settings or config.load()
    init_lock = threading.Lock()
    initialized = []
    limiter = auth.FailureLimiter()

    def ensure_db():
        if not initialized:
            with init_lock:
                if not initialized:
                    db.init(settings.db)
                    initialized.append(True)

    @asynccontextmanager
    async def lifespan(app):
        ensure_db()
        yield

    app = FastAPI(title="Poker Run contest API", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.settings = settings
    app.state.login_limiter = limiter

    # ---- errors: always {"error": "..."} -----------------------------------------------

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        msg = exc.detail if isinstance(exc.detail, str) else "error"
        if exc.status_code == 404:
            msg = "not found"
        return JSONResponse({"error": msg}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errs = exc.errors()
        if errs:
            e = errs[0]
            where = ".".join(str(x) for x in e.get("loc", []) if x != "body")
            msg = f"invalid request: {where + ': ' if where else ''}{e.get('msg', 'invalid')}"
        else:
            msg = "invalid request"
        return JSONResponse({"error": msg}, status_code=400)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):  # pragma: no cover - last resort
        return JSONResponse({"error": "internal server error"}, status_code=500)

    # ---- CSRF guard, body size cap, headers --------------------------------------------

    max_body = settings.max_source_bytes * 6 + 16 * 1024  # JSON escaping can inflate the source

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method in STATE_CHANGING:
            if request.headers.get("x-requested-with") != "fetch":
                return JSONResponse({"error": "missing X-Requested-With: fetch header"}, status_code=403)
            cl = request.headers.get("content-length")
            if cl is not None:
                try:
                    too_big = int(cl) > max_body
                except ValueError:
                    return JSONResponse({"error": "bad Content-Length"}, status_code=400)
                if too_big:
                    return JSONResponse({"error": "request too large"}, status_code=413)
            elif request.headers.get("transfer-encoding"):
                return JSONResponse({"error": "Content-Length required"}, status_code=411)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # ---- dependencies ---------------------------------------------------------------

    def get_conn():
        ensure_db()
        conn = db.connect(settings.db)
        try:
            yield conn
        finally:
            conn.close()

    def optional_user(request: Request, conn=Depends(get_conn)):
        return auth.session_user(conn, request.cookies.get(auth.COOKIE))

    def require_user(user=Depends(optional_user)):
        if user is None:
            raise ApiError(401, "not logged in")
        return user

    def require_admin(user=Depends(require_user)):
        if not auth.is_admin(settings, user):
            raise ApiError(403, "admins only")
        return user

    def set_session_cookie(response, token):
        response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, path="/",
                            httponly=True, samesite="lax", secure=settings.secure_cookies)

    def me_json(user):
        return {"username": user["username"], "is_admin": auth.is_admin(settings, user)}

    def iso_or_none(t):
        return db.iso(t) if t else None

    # ---- contest ------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/contest")
    def contest():
        t = db.now()
        return {
            "name": settings.contest_name,
            "start": iso_or_none(settings.start),
            "end": iso_or_none(settings.end),
            "freeze": iso_or_none(settings.freeze),
            "now": db.iso(t),
            "open": settings.is_open(t),
            "public_seeds": list(settings.public_seeds),
            "hidden_count": len(settings.hidden_seeds),
            "time_limit": settings.time_limit,
            "full_per_hour": settings.full_per_hour,
            "invite_required": bool(settings.invite),
        }

    # ---- accounts -----------------------------------------------------------------

    @app.post("/api/register", status_code=201)
    def register(body: RegisterBody, response: Response, conn=Depends(get_conn)):
        username = body.username.strip()
        if not auth.USERNAME_RE.match(username):
            raise ApiError(400, "username must be 3-20 characters: letters, digits, _ or -")
        if not (auth.PASSWORD_MIN <= len(body.password) <= auth.PASSWORD_MAX):
            raise ApiError(400, f"password must be {auth.PASSWORD_MIN}-{auth.PASSWORD_MAX} characters")
        if settings.invite and not _consteq(body.invite.strip(), settings.invite):
            raise ApiError(403, "wrong invite code")
        pw_hash = auth.hash_password(body.password)
        admin = username.lower() in settings.admins
        with db.transaction(conn, immediate=True):
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise ApiError(409, "that username is taken")
            uid = conn.execute("INSERT INTO users(username, pw_hash, created, is_admin) VALUES (?, ?, ?, ?)",
                               (username, pw_hash, db.now_iso(), int(admin))).lastrowid
            token = auth.create_session(conn, uid)
        set_session_cookie(response, token)
        return {"username": username, "is_admin": admin}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response, conn=Depends(get_conn)):
        username = body.username.strip()
        key = (request.client.host if request.client else "?", username.lower())
        if limiter.blocked(key):
            raise ApiError(429, "too many failed logins; try again in a few minutes")
        user = None
        if 0 < len(username) <= 20 and 0 < len(body.password) <= auth.PASSWORD_MAX:
            user = conn.execute("SELECT id, username, pw_hash, is_admin FROM users WHERE username = ?",
                                (username,)).fetchone()
        if user is None:
            auth.dummy_verify(body.password[:auth.PASSWORD_MAX])
            ok = False
        else:
            ok = auth.verify_password(body.password, user["pw_hash"])
        if not ok:
            limiter.fail(key)
            raise ApiError(401, "wrong username or password")
        limiter.reset(key)
        with db.transaction(conn, immediate=True):
            token = auth.create_session(conn, user["id"])
        set_session_cookie(response, token)
        return me_json(user)

    @app.post("/api/logout", status_code=204)
    def logout(request: Request, conn=Depends(get_conn)):
        auth.delete_session(conn, request.cookies.get(auth.COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(auth.COOKIE, path="/", httponly=True, samesite="lax",
                               secure=settings.secure_cookies)
        return response

    @app.get("/api/me")
    def me(user=Depends(require_user)):
        return me_json(user)

    # ---- submissions ------------------------------------------------------------------

    @app.post("/api/submissions", status_code=202)
    def submit(body: SubmitBody, user=Depends(require_user), conn=Depends(get_conn)):
        if body.kind not in ("public", "full"):
            raise ApiError(400, "kind must be 'public' or 'full'")
        source = body.source
        if len(source.encode("utf-8", "surrogatepass")) > settings.max_source_bytes:
            raise ApiError(413, f"source is larger than {settings.max_source_kb} KB")
        if not source.strip():
            raise ApiError(400, "source is empty")
        if "\x00" in source:
            raise ApiError(400, "source contains a NUL byte")
        try:
            source.encode("utf-8")
        except UnicodeEncodeError:
            raise ApiError(400, "source is not valid UTF-8 text") from None
        admin = auth.is_admin(settings, user)
        t = db.now()
        if body.kind == "full" and not admin and not settings.is_open(t):
            raise ApiError(403, "the contest is not open for full submissions right now")
        with db.transaction(conn, immediate=True):
            busy = conn.execute("SELECT 1 FROM submissions WHERE user_id = ? AND status IN ('queued', 'running') "
                                "LIMIT 1", (user["id"],)).fetchone()
            if busy:
                raise ApiError(429, "you already have a submission waiting to be judged")
            if body.kind == "full":
                n = conn.execute("SELECT COUNT(*) FROM submissions WHERE user_id = ? AND kind = 'full' "
                                 "AND status != 'error' AND created > ?",
                                 (user["id"], db.iso(t - timedelta(hours=1)))).fetchone()[0]
                if n >= settings.full_per_hour:
                    raise ApiError(429, f"at most {settings.full_per_hour} full submissions per hour")
            sid = conn.execute(
                "INSERT INTO submissions(user_id, kind, source, created, status, tests_total, tests_done) "
                "VALUES (?, ?, ?, ?, 'queued', ?, 0)",
                (user["id"], body.kind, source, db.iso(t), len(settings.seeds_for(body.kind)))).lastrowid
        return {"id": sid}

    def queue_position(conn, sub_id):
        return conn.execute("SELECT COUNT(*) FROM submissions WHERE status = 'queued' AND id <= ?",
                            (sub_id,)).fetchone()[0]

    def sub_json(conn, r, board):
        rel = None
        if r["kind"] == "full" and r["status"] == "done":
            rel = board.totals.get(r["id"], 0)
        return {
            "id": r["id"],
            "kind": r["kind"],
            "status": r["status"],
            "created": r["created"],
            "started": r["started"],
            "finished": r["finished"],
            "raw_total": r["raw_total"],
            "relative_total": rel,
            "tests_ac": r["tests_ac"],
            "tests_done": r["tests_done"],
            "tests_total": r["tests_total"],
            "max_time": r["max_time"],
            "error": r["error"],
            "position": queue_position(conn, r["id"]) if r["status"] == "queued" else None,
        }

    SUB_COLS = ("s.id, s.user_id, s.kind, s.status, s.created, s.started, s.finished, s.raw_total, "
                "s.tests_ac, s.tests_done, s.tests_total, s.max_time, s.error")

    @app.get("/api/submissions")
    def my_submissions(user=Depends(require_user), conn=Depends(get_conn)):
        board = scoring.board_for_viewer(conn, settings, user["id"], auth.is_admin(settings, user), db.now())
        rows = conn.execute(f"SELECT {SUB_COLS} FROM submissions s WHERE s.user_id = ? ORDER BY s.id DESC LIMIT 500",
                            (user["id"],)).fetchall()
        return [sub_json(conn, r, board) for r in rows]

    @app.get("/api/submissions/{sub_id}")
    def submission(sub_id: int, user=Depends(require_user), conn=Depends(get_conn)):
        admin = auth.is_admin(settings, user)
        r = conn.execute(f"SELECT {SUB_COLS}, s.source FROM submissions s WHERE s.id = ?", (sub_id,)).fetchone()
        if r is None or (r["user_id"] != user["id"] and not admin):
            raise ApiError(404, "submission not found")
        board = scoring.board_for_viewer(conn, settings, user["id"], admin, db.now())
        out = sub_json(conn, r, board)
        out["source"] = r["source"]
        res = conn.execute("SELECT seed, verdict, score, cleared, time, message FROM results "
                           "WHERE submission_id = ?", (sub_id,)).fetchall()
        order = {s: i for i, s in enumerate(settings.seeds_for(r["kind"]))}
        res = sorted(res, key=lambda x: (order.get(x["seed"], len(order)), x["seed"]))
        if r["kind"] == "public":
            out["results"] = [{"seed": x["seed"], "verdict": x["verdict"], "score": x["score"],
                               "cleared": x["cleared"], "time": x["time"], "message": x["message"]}
                              for x in res]
        else:
            results = []
            for i, x in enumerate(res, 1):
                item = {"test": i, "verdict": x["verdict"], "time": x["time"]}
                if admin:  # admins know the hidden seeds anyway
                    item.update(seed=x["seed"], score=x["score"], cleared=x["cleared"], message=x["message"])
                results.append(item)
            out["results"] = results
        return out

    # ---- leaderboard ------------------------------------------------------------------

    @app.get("/api/leaderboard")
    def leaderboard(user=Depends(optional_user), conn=Depends(get_conn)):
        return scoring.leaderboard(conn, settings, auth.is_admin(settings, user), db.now())

    # ---- admin ------------------------------------------------------------------------

    @app.get("/api/admin/submissions")
    def admin_submissions(limit: int = 1000, admin=Depends(require_admin), conn=Depends(get_conn)):
        limit = max(1, min(limit, 10000))
        board = scoring.compute(conn, settings)
        rows = conn.execute(f"SELECT {SUB_COLS}, u.username FROM submissions s JOIN users u ON u.id = s.user_id "
                            "ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            item = sub_json(conn, r, board)
            item["username"] = r["username"]
            out.append(item)
        return out

    @app.post("/api/admin/rejudge/{sub_id}")
    def rejudge(sub_id: int, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            r = conn.execute("SELECT status FROM submissions WHERE id = ?", (sub_id,)).fetchone()
            if r is None:
                raise ApiError(404, "submission not found")
            if r["status"] == "running":
                raise ApiError(409, "the submission is being judged right now")
            conn.execute("DELETE FROM results WHERE submission_id = ?", (sub_id,))
            conn.execute("UPDATE submissions SET status = 'queued', started = NULL, finished = NULL, error = NULL, "
                         "raw_total = NULL, cleared_total = NULL, max_time = NULL, tests_ac = NULL, "
                         "tests_done = 0 WHERE id = ?", (sub_id,))
        return {"id": sub_id, "status": "queued"}

    return app


def _consteq(a, b):
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


app = create_app()

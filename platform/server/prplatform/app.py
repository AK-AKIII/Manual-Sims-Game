"""The HTTP API (platform/API.md, "HTTP API"). Run with `uvicorn prplatform.app:app`.

This process never runs submitted code: it only stores submissions; the
worker (prplatform.worker) judges them in the sandbox.
"""

import csv
import io
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import admin as admin_ops
from . import auth, config, db, overrides, scoring, similarity

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
    email: str = ""


class LoginBody(Body):
    username: str = ""  # a username or an email
    login: str = ""     # alias for `username`
    email: str = ""     # alias for `username`
    password: str = ""


class EmailBody(Body):
    email: str = ""
    password: str = ""


class AdminEmailBody(Body):
    email: Optional[str] = None


class SubmitBody(Body):
    kind: str = ""
    source: str = ""


class DisableBody(Body):
    reason: Optional[str] = None


class PasswordBody(Body):
    password: str = ""


class AdminFlagBody(Body):
    is_admin: StrictBool


class DeleteUserBody(Body):
    confirm: str = ""


class BulkBody(Body):
    which: str = ""


class SettingsBody(Body):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    set_values: dict[str, Any] = Field(default_factory=dict, alias="set")
    clear: list[str] = Field(default_factory=list)


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

    @app.exception_handler(admin_ops.AdminError)
    async def admin_error(request, exc):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(overrides.SettingsError)
    async def settings_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

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

    def get_cfg(conn=Depends(get_conn)):
        """The env settings with the admin-panel overrides applied (read per request)."""
        return overrides.effective(conn, settings)

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
        return {"username": user["username"], "is_admin": auth.is_admin(settings, user), "email": user["email"]}

    def iso_or_none(t):
        return db.iso(t) if t else None

    # ---- contest ------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True}

    def contest_json(cfg):
        t = db.now()
        return {
            "name": cfg.contest_name,
            "start": iso_or_none(cfg.start),
            "end": iso_or_none(cfg.end),
            "freeze": iso_or_none(cfg.freeze),
            "now": db.iso(t),
            "open": cfg.is_open(t),
            "public_seeds": list(cfg.public_seeds),
            "hidden_count": len(cfg.hidden_seeds),
            "time_limit": cfg.time_limit,
            "full_per_hour": cfg.full_per_hour,
            "invite_required": bool(cfg.invite),
            "max_source_kb": cfg.max_source_kb,
            "announcement": cfg.announcement if (cfg.announcement_on and cfg.announcement) else None,
        }

    @app.get("/api/contest")
    def contest(cfg=Depends(get_cfg)):
        return contest_json(cfg)

    # ---- accounts -----------------------------------------------------------------

    @app.post("/api/register", status_code=201)
    def register(body: RegisterBody, response: Response, conn=Depends(get_conn), cfg=Depends(get_cfg)):
        username = body.username.strip()
        if not auth.USERNAME_RE.match(username):
            raise ApiError(400, "username must be 3-20 characters: letters, digits, _ or -")
        if not (auth.PASSWORD_MIN <= len(body.password) <= auth.PASSWORD_MAX):
            raise ApiError(400, f"password must be {auth.PASSWORD_MIN}-{auth.PASSWORD_MAX} characters")
        if cfg.invite and not _consteq(body.invite.strip(), cfg.invite):
            raise ApiError(403, "wrong invite code")
        email = auth.normalise_email(body.email)
        if email is None:
            raise ApiError(400, "enter a valid email address")
        pw_hash = auth.hash_password(body.password)
        admin = username.lower() in settings.admins
        with db.transaction(conn, immediate=True):
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise ApiError(409, "that username is taken")
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise ApiError(409, "email already registered")
            t = db.now_iso()
            uid = conn.execute("INSERT INTO users(username, pw_hash, created, is_admin, last_login, email) "
                               "VALUES (?, ?, ?, ?, ?, ?)", (username, pw_hash, t, int(admin), t, email)).lastrowid
            token = auth.create_session(conn, uid)
        set_session_cookie(response, token)
        return {"username": username, "is_admin": admin, "email": email}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response, conn=Depends(get_conn)):
        ident = (body.username or body.login or body.email).strip()
        key = (request.client.host if request.client else "?", ident.lower())
        if limiter.blocked(key):
            raise ApiError(429, "too many failed logins; try again in a few minutes")
        user = None
        cols = "id, username, pw_hash, is_admin, disabled_at, email"
        if 0 < len(body.password) <= auth.PASSWORD_MAX:
            if "@" in ident:
                email = auth.normalise_email(ident)
                if email:
                    user = conn.execute(f"SELECT {cols} FROM users WHERE email = ?", (email,)).fetchone()
            elif 0 < len(ident) <= 20:
                user = conn.execute(f"SELECT {cols} FROM users WHERE username = ?", (ident,)).fetchone()
        if user is None:
            auth.dummy_verify(body.password[:auth.PASSWORD_MAX])
            ok = False
        else:
            ok = auth.verify_password(body.password, user["pw_hash"])
        if not ok:
            limiter.fail(key)
            raise ApiError(401, "wrong username or password")
        limiter.reset(key)
        if user["disabled_at"]:
            raise ApiError(403, "this account is disabled; contact the organisers")
        with db.transaction(conn, immediate=True):
            token = auth.create_session(conn, user["id"])
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (db.now_iso(), user["id"]))
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

    @app.post("/api/account/email")
    def account_email(body: EmailBody, user=Depends(require_user), conn=Depends(get_conn)):
        email = auth.normalise_email(body.email)
        if email is None:
            raise ApiError(400, "enter a valid email address")
        row = conn.execute("SELECT pw_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not (0 < len(body.password) <= auth.PASSWORD_MAX) or not auth.verify_password(body.password, row["pw_hash"]):
            raise ApiError(401, "wrong password")
        with db.transaction(conn, immediate=True):
            if conn.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user["id"])).fetchone():
                raise ApiError(409, "email already registered")
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user["id"]))
        return {"username": user["username"], "is_admin": auth.is_admin(settings, user), "email": email}

    # ---- submissions ------------------------------------------------------------------

    @app.post("/api/submissions", status_code=202)
    def submit(body: SubmitBody, user=Depends(require_user), conn=Depends(get_conn), cfg=Depends(get_cfg)):
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
        if body.kind == "full" and not admin and not cfg.is_open(t):
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
                if n >= cfg.full_per_hour:
                    raise ApiError(429, f"at most {cfg.full_per_hour} full submissions per hour")
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
    def my_submissions(user=Depends(require_user), conn=Depends(get_conn), cfg=Depends(get_cfg)):
        board = scoring.board_for_viewer(conn, cfg, user["id"], auth.is_admin(settings, user), db.now())
        rows = conn.execute(f"SELECT {SUB_COLS} FROM submissions s WHERE s.user_id = ? ORDER BY s.id DESC LIMIT 500",
                            (user["id"],)).fetchall()
        return [sub_json(conn, r, board) for r in rows]

    @app.get("/api/submissions/{sub_id}")
    def submission(sub_id: int, user=Depends(require_user), conn=Depends(get_conn), cfg=Depends(get_cfg)):
        admin = auth.is_admin(settings, user)
        r = conn.execute(f"SELECT {SUB_COLS}, s.source FROM submissions s WHERE s.id = ?", (sub_id,)).fetchone()
        if r is None or (r["user_id"] != user["id"] and not admin):
            raise ApiError(404, "submission not found")
        board = scoring.board_for_viewer(conn, cfg, user["id"], admin, db.now())
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
    def leaderboard(user=Depends(optional_user), conn=Depends(get_conn), cfg=Depends(get_cfg)):
        return scoring.leaderboard(conn, cfg, auth.is_admin(settings, user), db.now())

    # ---- admin: see admin_routes() below ------------------------------------------

    admin_routes(app, settings, get_conn, get_cfg, require_admin, sub_json, SUB_COLS, contest_json)

    return app



# ---------------------------------------------------------------------------- admin API

CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(v):
    """Formula-injection guard: text that a spreadsheet would run as a formula gets a leading '."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str) and v.startswith(CSV_DANGEROUS):
        return "'" + v
    return v


def csv_response(header, rows, filename):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([csv_cell(x) for x in r])
    return Response(buf.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def stamp():
    return db.now().strftime("%Y%m%d-%H%M")


def admin_routes(app, settings, get_conn, get_cfg, require_admin, sub_json, SUB_COLS, contest_json):
    def actor_of(user):
        return {"id": user["id"], "username": user["username"]}

    def one_user(conn, uid):
        rows = admin_ops.user_rows(conn, settings, uid)
        if not rows:
            raise ApiError(404, "user not found")
        return rows[0]

    def admin_sub_rows(conn, rows, board):
        out = []
        for r in rows:
            item = sub_json(conn, r, board)
            item["username"] = r["username"]
            item["user_id"] = r["user_id"]
            item["user_disabled"] = bool(r["disabled_at"])
            out.append(item)
        return out

    ADMIN_SUB_SELECT = (f"SELECT {SUB_COLS}, u.username, u.disabled_at FROM submissions s "
                        "JOIN users u ON u.id = s.user_id")

    def parse_query_time(text, name):
        try:
            return db.iso(config.parse_time(text, name))
        except config.ConfigError:
            raise ApiError(400, f"{name} must be an ISO 8601 time") from None

    # ---- overview ---------------------------------------------------------------

    @app.get("/api/admin/overview")
    def overview(admin=Depends(require_admin), conn=Depends(get_conn), cfg=Depends(get_cfg)):
        t = db.now()
        day_ago = db.iso(t - timedelta(hours=24))
        users = conn.execute("SELECT username, is_admin, disabled_at FROM users").fetchall()
        active_24h = conn.execute(
            "SELECT COUNT(*) FROM users u WHERE u.last_login > ? OR EXISTS "
            "(SELECT 1 FROM submissions s WHERE s.user_id = u.id AND s.created > ?)", (day_ago, day_ago)).fetchone()[0]
        sc = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(created > ?), 0) AS n24, COALESCE(SUM(kind = 'full'), 0) AS full, "
            "COALESCE(SUM(kind = 'public'), 0) AS public, COALESCE(SUM(status = 'queued'), 0) AS queued, "
            "COALESCE(SUM(status = 'running'), 0) AS running, COALESCE(SUM(status = 'error'), 0) AS errors "
            "FROM submissions", (day_ago,)).fetchone()
        running = [{"id": r["id"], "username": r["username"], "kind": r["kind"], "tests_done": r["tests_done"],
                    "tests_total": r["tests_total"], "started": r["started"]}
                   for r in conn.execute("SELECT s.id, u.username, s.kind, s.tests_done, s.tests_total, s.started "
                                         "FROM submissions s JOIN users u ON u.id = s.user_id "
                                         "WHERE s.status = 'running' ORDER BY s.id")]
        hb = conn.execute("SELECT value FROM meta WHERE key = 'worker_heartbeat'").fetchone()
        last_seen = hb["value"] if hb else None
        alive = False
        if last_seen:
            try:
                alive = (t - db.parse_iso(last_seen)).total_seconds() <= 30
            except ValueError:
                alive = False
        verdicts = {v: 0 for v in ("AC", "WA", "TLE", "RE")}
        for r in conn.execute("SELECT verdict, COUNT(*) AS n FROM results GROUP BY verdict"):
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + r["n"]
        first_day = (t - timedelta(days=13)).date()
        since = first_day.isoformat()
        days = {(first_day + timedelta(days=i)).isoformat(): {"registrations": 0, "submissions": 0, "full": 0}
                for i in range(14)}
        for r in conn.execute("SELECT substr(created, 1, 10) AS d, COUNT(*) AS n FROM users WHERE created >= ? "
                              "GROUP BY d", (since,)):
            if r["d"] in days:
                days[r["d"]]["registrations"] = r["n"]
        for r in conn.execute("SELECT substr(created, 1, 10) AS d, COUNT(*) AS n, COALESCE(SUM(kind = 'full'), 0) AS f "
                              "FROM submissions WHERE created >= ? GROUP BY d", (since,)):
            if r["d"] in days:
                days[r["d"]].update(submissions=r["n"], full=r["f"])
        top = [r for r in scoring.leaderboard(conn, cfg, True, t)["rows"] if r.get("rank")][:5]
        return {
            "counts": {
                "registered": len(users),
                "active": sum(1 for u in users if not u["disabled_at"]),
                "disabled": sum(1 for u in users if u["disabled_at"]),
                "admins": sum(1 for u in users if admin_ops.effective_admin(settings, u)),
                "active_24h": active_24h,
                "submissions": sc["n"], "submissions_24h": sc["n24"], "full": sc["full"], "public": sc["public"],
                "queued": sc["queued"], "running": sc["running"], "errors": sc["errors"],
            },
            "judge": {"queue": sc["queued"], "running": running, "worker_last_seen": last_seen,
                      "worker_alive": alive},
            "verdicts": verdicts,
            "daily": [dict(date=d, **v) for d, v in days.items()],
            "top": top,
            "contest": contest_json(cfg),
        }

    # ---- users ------------------------------------------------------------------

    @app.get("/api/admin/users")
    def admin_users(admin=Depends(require_admin), conn=Depends(get_conn), cfg=Depends(get_cfg)):
        rows = admin_ops.user_rows(conn, settings)
        return {"users": rows, "summary": admin_ops.summary(rows)}

    USER_CSV = ("id", "username", "email", "created", "status", "is_admin", "last_login", "public_submissions",
                "full_submissions", "best_relative_total", "rank", "best_submission_id", "last_submission",
                "disabled_at", "disabled_reason")

    @app.get("/api/admin/users.csv")
    def admin_users_csv(admin=Depends(require_admin), conn=Depends(get_conn)):
        rows = admin_ops.user_rows(conn, settings)
        return csv_response(USER_CSV, [[r[k] for k in USER_CSV] for r in rows], f"poker-run-students-{stamp()}.csv")

    @app.get("/api/admin/users/{uid}")
    def admin_user(uid: int, admin=Depends(require_admin), conn=Depends(get_conn)):
        out = one_user(conn, uid)
        board = scoring.compute(conn, settings)
        rows = conn.execute(f"{ADMIN_SUB_SELECT} WHERE s.user_id = ? ORDER BY s.id DESC LIMIT 1000", (uid,)).fetchall()
        out["submissions"] = admin_sub_rows(conn, rows, board)
        return out

    @app.post("/api/admin/users/{uid}/disable")
    def admin_disable(uid: int, body: DisableBody = DisableBody(), admin=Depends(require_admin),
                      conn=Depends(get_conn)):
        if body.reason is not None and len(body.reason) > 200:
            raise ApiError(400, "reason must be at most 200 characters")
        with db.transaction(conn, immediate=True):
            admin_ops.disable(conn, settings, actor_of(admin), uid, body.reason)
        return one_user(conn, uid)

    @app.post("/api/admin/users/{uid}/enable")
    def admin_enable(uid: int, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            admin_ops.enable(conn, settings, actor_of(admin), uid)
        return one_user(conn, uid)

    @app.post("/api/admin/users/{uid}/password")
    def admin_password(uid: int, body: PasswordBody, request: Request, admin=Depends(require_admin),
                       conn=Depends(get_conn)):
        admin_ops.get_user(conn, uid)
        admin_ops.check_password(body.password)
        pw_hash = auth.hash_password(body.password)  # slow: outside the write lock
        token = request.cookies.get(auth.COOKIE)
        with db.transaction(conn, immediate=True):
            n = admin_ops.reset_password(conn, settings, actor_of(admin), uid, body.password,
                                         keep_token_hash=auth.token_hash(token) if token else None, pw_hash=pw_hash)
        return {"ok": True, "sessions_revoked": n}

    @app.post("/api/admin/users/{uid}/admin")
    def admin_flag(uid: int, body: AdminFlagBody, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            admin_ops.set_admin(conn, settings, actor_of(admin), uid, body.is_admin)
        return one_user(conn, uid)

    @app.post("/api/admin/users/{uid}/email")
    def admin_email(uid: int, body: AdminEmailBody, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            admin_ops.set_email(conn, actor_of(admin), uid, body.email)
        return one_user(conn, uid)

    @app.delete("/api/admin/users/{uid}")
    def admin_delete_user(uid: int, body: DeleteUserBody = DeleteUserBody(), admin=Depends(require_admin),
                          conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            name, n = admin_ops.delete_user(conn, settings, actor_of(admin), uid, body.confirm)
        return {"deleted": name, "submissions": n}

    # ---- submissions --------------------------------------------------------------

    @app.get("/api/admin/submissions")
    def admin_submissions(limit: int = 1000, user: str = "", kind: str = "", status: str = "", verdict: str = "",
                          since: str = "", until: str = "", admin=Depends(require_admin), conn=Depends(get_conn)):
        limit = max(1, min(limit, 10000))
        where, args = [], []
        if user.strip():
            where.append("u.username = ?")
            args.append(user.strip())
        if kind:
            if kind not in ("public", "full"):
                raise ApiError(400, "kind must be 'public' or 'full'")
            where.append("s.kind = ?")
            args.append(kind)
        if status:
            if status not in ("queued", "running", "done", "error"):
                raise ApiError(400, "status must be queued, running, done or error")
            where.append("s.status = ?")
            args.append(status)
        if verdict:
            if verdict not in ("AC", "WA", "TLE", "RE"):
                raise ApiError(400, "verdict must be AC, WA, TLE or RE")
            where.append("EXISTS (SELECT 1 FROM results r WHERE r.submission_id = s.id AND r.verdict = ?)")
            args.append(verdict)
        if since:
            where.append("s.created >= ?")
            args.append(parse_query_time(since, "since"))
        if until:
            where.append("s.created < ?")
            args.append(parse_query_time(until, "until"))
        sql = ADMIN_SUB_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY s.id DESC LIMIT ?"
        rows = conn.execute(sql, args + [limit]).fetchall()
        return admin_sub_rows(conn, rows, scoring.compute(conn, settings))

    @app.delete("/api/admin/submissions/{sub_id}")
    def admin_delete_submission(sub_id: int, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            admin_ops.delete_submission(conn, settings, actor_of(admin), sub_id)
        return {"deleted": sub_id}

    @app.post("/api/admin/submissions/{sub_id}/cancel")
    def admin_cancel(sub_id: int, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            admin_ops.cancel_submission(conn, actor_of(admin), sub_id)
        rows = conn.execute(f"{ADMIN_SUB_SELECT} WHERE s.id = ?", (sub_id,)).fetchall()
        return admin_sub_rows(conn, rows, scoring.compute(conn, settings))[0]

    @app.post("/api/admin/rejudge/{sub_id}")
    def rejudge(sub_id: int, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            admin_ops.rejudge(conn, actor_of(admin), sub_id)
        return {"id": sub_id, "status": "queued"}

    @app.post("/api/admin/rejudge-bulk")
    def rejudge_bulk(body: BulkBody, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            ids = admin_ops.rejudge_bulk(conn, actor_of(admin), body.which)
        return {"count": len(ids), "ids": ids}

    @app.get("/api/admin/submissions.zip")
    def admin_zip(pick: str = "best", admin=Depends(require_admin), conn=Depends(get_conn)):
        if pick not in ("best", "latest"):
            raise ApiError(400, "pick must be 'best' or 'latest'")
        board = scoring.compute(conn, settings)
        if pick == "best":
            ids = [e["best_id"] for e in scoring.standings(conn, settings, include_disabled=True)]
        else:
            ids = [r[0] for r in conn.execute("SELECT MAX(id) FROM submissions WHERE kind = 'full' GROUP BY user_id")]
        rows = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows += conn.execute(f"SELECT s.id, s.source, s.created, s.status, s.raw_total, u.username, u.disabled_at "
                                 f"FROM submissions s JOIN users u ON u.id = s.user_id "
                                 f"WHERE s.id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        rows.sort(key=lambda r: r["username"].lower())
        buf = io.BytesIO()
        manifest = io.StringIO()
        w = csv.writer(manifest, lineterminator="\r\n")
        w.writerow(["username", "account", "file", "submission_id", "created", "status", "relative_total", "raw_total"])
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for r in rows:
                fname = f"{r['username']}.py"
                z.writestr(fname, r["source"])
                rel = board.totals.get(r["id"], 0) if r["status"] == "done" else None
                w.writerow([csv_cell(x) for x in (r["username"], "disabled" if r["disabled_at"] else "active", fname,
                                                  r["id"], r["created"], r["status"], rel, r["raw_total"])])
            z.writestr("manifest.csv", manifest.getvalue())
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="poker-run-{pick}-{stamp()}.zip"'})

    # ---- leaderboard ----------------------------------------------------------------

    @app.get("/api/admin/leaderboard.csv")
    def admin_leaderboard_csv(view: str = "live", admin=Depends(require_admin), conn=Depends(get_conn),
                              cfg=Depends(get_cfg)):
        if view not in ("live", "public"):
            raise ApiError(400, "view must be 'live' or 'public'")
        cutoff = cfg.freeze if (view == "public" and cfg.frozen(db.now())) else None
        rows = scoring.standings(conn, cfg, cutoff=cutoff, include_disabled=False)
        return csv_response(
            ("rank", "username", "relative_total", "raw_total", "tests_ac", "submissions", "best_submission_id",
             "best_submitted"),
            [[e["rank"], e["username"], e["relative_total"], e["raw_total"], e["tests_ac"], e["submissions"],
              e["best_id"], e["submitted"]] for e in rows],
            f"poker-run-standings-{view}-{stamp()}.csv")

    # ---- similarity -------------------------------------------------------------------

    @app.get("/api/admin/similarity")
    def admin_similarity(limit: int = 50, admin=Depends(require_admin), conn=Depends(get_conn)):
        limit = max(1, min(limit, 500))
        return similarity.check(conn, limit=limit, scope=settings.db)

    # ---- settings ---------------------------------------------------------------------

    @app.get("/api/admin/settings")
    def admin_settings(admin=Depends(require_admin), conn=Depends(get_conn)):
        return overrides.describe(conn, settings)

    @app.post("/api/admin/settings")
    def admin_settings_update(body: SettingsBody, admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            details = overrides.update(conn, settings, admin["username"], body.set_values, body.clear)
            if details:
                admin_ops.audit(conn, actor_of(admin), "settings.update", None, details)
        return overrides.describe(conn, settings)

    @app.post("/api/admin/settings/invite/regenerate")
    def admin_invite_regenerate(admin=Depends(require_admin), conn=Depends(get_conn)):
        with db.transaction(conn, immediate=True):
            details = overrides.update(conn, settings, admin["username"], {"invite": overrides.new_invite()})
            admin_ops.audit(conn, actor_of(admin), "settings.invite", None, details.get("invite") and
                            {"invite": details["invite"]} or {})
        return overrides.describe(conn, settings)

    # ---- audit ---------------------------------------------------------------------

    @app.get("/api/admin/audit")
    def admin_audit(actor: str = "", action: str = "", target: str = "", limit: int = 200,
                    admin=Depends(require_admin), conn=Depends(get_conn)):
        return admin_ops.audit_rows(conn, actor.strip() or None, action.strip() or None, target.strip() or None, limit)

def _consteq(a, b):
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


app = create_app()

# Poker Run contest platform: design and HTTP contract

The contest site for Poker Run, modelled on Harvest Craft's platform: students
register with an invite code, upload a Python solver, a judge runs it in a
sandbox on hidden seeds, and a leaderboard ranks them by relative score.

## Layout

```
platform/
  API.md                 this file: the contract between server, site and deploy
  server/prplatform/     FastAPI app, SQLite database, judge worker, sandbox runners
  server/tests/          tests (run with the local sandbox, no Docker needed)
  judge/                 the judge container: Dockerfile and runner.py (runs inside it)
  site/                  static pages: landing, game, problem, contest pages (vanilla JS)
  deploy/                docker-compose.yml, Caddyfile, Dockerfiles, backup, RUNBOOK.md
integrations/nextjs/     drop-in page for the owner's Next.js site
```

## Processes

- **api**: `uvicorn prplatform.app:app`. Serves `/api/*` only. Caddy serves the static site and proxies `/api/*`.
- **worker**: `python -m prplatform.worker`. Takes queued submissions from the database one at a time (configurable count of workers), runs them in the sandbox, checks each output with `poker_run.check`, stores results.
- **caddy**: HTTPS for `$DOMAIN` (automatic Let's Encrypt), static files from `platform/site/` (plus `poker_run/web/` at `/play/`, downloads at `/downloads/`), reverse proxy `/api/*` to api.

## Configuration (environment variables)

| Variable | Meaning | Default |
| --- | --- | --- |
| `PR_DB` | SQLite file path | `./data/platform.db` |
| `PR_SECRET` | reserved; session tokens are 256-bit random values stored as SHA-256, so nothing is signed with it today | unset |
| `PR_INVITE` | invite code needed to register (empty = open registration) | empty |
| `PR_ADMINS` | comma-separated usernames that are admins | empty |
| `PR_CONTEST_NAME` | shown on the site | `Poker Run` |
| `PR_START`, `PR_END` | ISO 8601 UTC times; submissions only between them (admins exempt) | unset = always open |
| `PR_FREEZE` | ISO time after which the public leaderboard stops updating (admins see live) | unset |
| `PR_HIDDEN_SEEDS` | comma/range list of secret seeds for full submissions, e.g. `81723,90211,1000-1047` | `1001-1050` (dev only) |
| `PR_PUBLIC_SEEDS` | seeds for public (feedback) runs | `1-10` |
| `PR_TIME_LIMIT` | seconds per test | `2.0` |
| `PR_MEMORY_MB` | memory per submission | `1024` |
| `PR_MAX_SOURCE_KB` | max upload size | `256` |
| `PR_FULL_PER_HOUR` | full submissions allowed per user per rolling hour | `6` |
| `PR_SANDBOX` | `docker` (production) or `local` (development only: NOT a security boundary) | `local` |
| `PR_JUDGE_IMAGE` | docker image for the sandbox | `poker-run-judge` |
| `PR_SECURE_COOKIES` | `1` to mark cookies Secure (behind HTTPS) | `0` |
| `PR_WORKERS` | judge threads inside the one worker process (run one worker process per database) | `1` |
| `PR_JOB_ROOT` | where job dirs are created; with the docker sandbox it must be a host path the Docker daemon can see | system temp |
| `PR_POLL_SECONDS`, `PR_RUNNER` | worker poll interval; path to runner.py for the local sandbox | |

## Sandbox

One container per submission, not per test. The worker writes the source and all inputs into a temp dir, then runs:

```
docker run --rm -i --name pr-judge-<random> --network none --memory {MB}m --memory-swap {MB}m --cpus 1 --pids-limit 64
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges
  -v {dir}:/job:ro {image} python3 /runner.py /job {time_limit}
```

`judge/runner.py` (inside the container) runs `python3 /job/main.py < /job/inputs/NNNN.txt` for each input in order with a per-test wall-clock timeout, and prints one JSON line per finished test as it goes (JSON Lines, flushed):

```json
{"name": "0001", "status": "ok|tle|re", "time": 0.83, "stdout": "...", "stderr_tail": "...", "exit_code": 1, "stdout_truncated": true}
```

(`exit_code` only for `re`, `stdout_truncated` only when the 1 MB cap was hit; truncated output is judged WA.) The worker reads the lines as they arrive, checks each test, stores its result and updates `submissions.tests_done`, so the site can show live progress. Lines must come in test order and at most one per test, or the submission ends with status `error`. The worker enforces an overall timeout of `tests × (time_limit + 1) + 30` s and `docker kill`s the named container after it; tests still missing then are TLE, or RE if the sandbox stopped early. The local sandbox runs the same runner.py as a subprocess with `resource` limits (CPU, address space, file size, processes) for development and tests.

The worker checks every test's stdout with `poker_run.check` outside the sandbox. Per-test verdict: `AC` (valid output), `WA` (checker exit 1 or 2: illegal or unparsable), `TLE`, `RE`. A non-AC test scores 0.

## Scoring

- Raw score of a test: the checker's `score` (0 unless AC).
- Relative score of a test: `round(1_000_000 * raw / best)`, where `best` is the highest raw score on that seed over all finished full submissions (0 if best is 0).
- A submission's relative total: sum over hidden seeds. It changes as others improve, so it is computed when read.
- Leaderboard: each user's best full submission by relative total. Ties: earlier submission first.
- During a freeze, non-admins see the leaderboard computed only from submissions created before `PR_FREEZE`.

## Database (SQLite)

- `users(id, username UNIQUE, pw_hash, created, is_admin)`; password hashed with `hashlib.scrypt` (n=2**14, r=8, p=1, 16-byte salt), stored as `scrypt$n$r$p$salt_hex$hash_hex`.
- `sessions(token_hash PRIMARY KEY, user_id, created, expires)`; the cookie holds a random token, the DB stores its SHA-256.
- `submissions(id, user_id, kind 'public'|'full', source, created, status 'queued'|'running'|'done'|'error', started, finished, error, raw_total, cleared_total, max_time, tests_ac, tests_total, tests_done)`; `tests_total` is set when the submission is queued.
- `results(submission_id, seed, verdict, score, cleared, time, message)`; PRIMARY KEY (submission_id, seed).

## HTTP API

All JSON. Errors: `{"error": "human readable message"}` with a 4xx/5xx status. Session: HttpOnly cookie `pr_session`, SameSite=Lax, 14 days. State-changing requests must send header `X-Requested-With: fetch` (CSRF guard; 403 otherwise).

| Method, path | Body | Response |
| --- | --- | --- |
| `GET /api/health` | | `{"ok": true}` |
| `GET /api/contest` | | `{name, start, end, freeze, now, open: bool, public_seeds: [..], hidden_count, time_limit, full_per_hour, invite_required: bool}` |
| `POST /api/register` | `{username, password, invite}` | 201 `{username, is_admin}` + cookie. username 3–20 chars `[A-Za-z0-9_-]`, password 8+ chars. 409 if taken, 403 bad invite |
| `POST /api/login` | `{username, password}` | 200 `{username, is_admin}` + cookie; 401 on bad credentials |
| `POST /api/logout` | | 204, clears cookie |
| `GET /api/me` | | `{username, is_admin}` or 401 |
| `POST /api/submissions` | `{kind: "public"|"full", source: "<python code>"}` | 202 `{id}`. 401 not logged in; 403 contest closed (full only; public always allowed while logged in); 413 too big; 429 if the user already has a queued/running submission, or exceeded `PR_FULL_PER_HOUR` |
| `GET /api/submissions` | | `[{id, kind, status, created, started, finished, raw_total, relative_total, tests_ac, tests_done, tests_total, position, max_time, error}]` newest first, own only (`relative_total` null for public; `position` is the place in the queue, null unless queued) |
| `GET /api/submissions/{id}` | | the row above plus `source` and `results`. For **public** submissions results are per seed `{seed, verdict, score, cleared, time, message}`; for **full** submissions results are anonymised `{test: 1..N, verdict, time}` (no seeds, no scores) so hidden seeds stay hidden. Own submissions only, admins any. `position` = place in queue while queued |
| `GET /api/leaderboard` | | `{frozen: bool, rows: [{rank, username, relative_total, raw_total, tests_ac, submitted, submissions}]}` |
| `GET /api/admin/submissions` | | admin only: all submissions with username |
| `POST /api/admin/rejudge/{id}` | | admin only: back to queued, results cleared. 200 `{id, status}`; 409 while running |

Other rules as built: usernames are unique and log in case-insensitively; 10 failed logins per 15 minutes per (IP, username) gives 429 (uvicorn runs with `--proxy-headers` behind Caddy so it sees real IPs); validation errors are 400; someone else's submission is 404; the hourly limit ignores submissions that ended in `error`; one queued-or-running submission at a time applies to everyone including admins (admins are only exempt from the contest window); admins see seeds and scores in full-run details; during a freeze a student's own list is scored against pre-freeze submissions plus their own.

## Site pages (platform/site/)

- `/` landing: name, one-paragraph pitch, the explainer video (`/media/poker_run_explainer.mp4`), buttons: Play in the browser (`/play/`), Read the problem (`/problem/`), Enter the contest (`/contest/`).
- `/problem/` the statement (rendered from `poker_run/problem/statement.md` at build time) and downloads: starter.py, public inputs zip, statement.md.
- `/play/` the browser game (copied from `poker_run/web/`).
- `/contest/` contest hub: countdown/status from `/api/contest`; login/register forms when logged out; when logged in: submit form (file upload or paste, kind public/full), my submissions table with live polling, submission detail view.
- `/contest/leaderboard/` the leaderboard.

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
- `best` of a hidden seed: the highest raw score on that seed over all finished full submissions (of active users).
- **Contest score** (`score`, the number the site shows): `100 × mean over the hidden tests of (raw / best)`, a test whose best is 0 contributing 0. A float from 0.00 to 100.00 with 2 decimals (`100.0` = the best on every test, `50.0` = half the best on every test). It changes as others improve, so it is computed when read.
- Internally (and in responses, for compatibility) the exact integer `relative_total` = sum over hidden tests of `round(1_000_000 * raw / best)` is kept; `score = round(relative_total / (10_000 × hidden tests), 2)`, a monotonic rescaling, so it equals the exact mean to within 0.00005 and never disagrees with the ranking. The site no longer displays `relative_total`.
- Leaderboard: each user's best full submission by relative total (equivalently by score). Ties: earlier submission first.
- During a freeze, non-admins see the leaderboard computed only from submissions created before `PR_FREEZE`.

## Database (SQLite)

- `users(id, username UNIQUE, pw_hash, created, is_admin)`; password hashed with `hashlib.scrypt` (n=2**14, r=8, p=1, 16-byte salt), stored as `scrypt$n$r$p$salt_hex$hash_hex`.
- `sessions(token_hash PRIMARY KEY, user_id, created, expires)`; the cookie holds a random token, the DB stores its SHA-256.
- `submissions(id, user_id, kind 'public'|'full', source, created, status 'queued'|'running'|'done'|'error', started, finished, error, raw_total, cleared_total, max_time, tests_ac, tests_total, tests_done)`; `tests_total` is set when the submission is queued.
- `results(submission_id, seed, verdict, score, cleared, time, message)`; PRIMARY KEY (submission_id, seed).
- Added later (additive migrations run on every start: `ALTER TABLE ... ADD COLUMN` for missing columns, `CREATE TABLE IF NOT EXISTS` for new tables; nothing is rewritten or dropped):
  - `users.last_login` (ISO, set on register and login; NULL until the user next signs in), `users.disabled_at` (ISO; NULL = active), `users.disabled_reason`.
  - `users.email` (lowercase, trimmed; NULL for accounts made before emails existed) with `UNIQUE INDEX users_email ON users(email) WHERE email IS NOT NULL`. Emails are only returned to their owner (`/api/me`) and to admins; never on the leaderboard or in other students' responses. Nothing sends email yet.
  - `audit(id, created, actor, action, target, details)`: one row per admin action. `actor`/`target` are username text (no foreign key), so entries outlive deleted users. `details` is a JSON object.
  - `settings(key PRIMARY KEY, value, updated, updated_by)`: contest settings overridden from the admin panel; `value` is JSON. A missing row means "use the environment variable".
  - `meta(key PRIMARY KEY, value)`: small runtime values; the worker writes `worker_heartbeat` (ISO time) at most every 5 s while it polls or judges.

## HTTP API

All JSON. Errors: `{"error": "human readable message"}` with a 4xx/5xx status. Session: HttpOnly cookie `pr_session`, SameSite=Lax, 14 days. State-changing requests must send header `X-Requested-With: fetch` (CSRF guard; 403 otherwise).

| Method, path | Body | Response |
| --- | --- | --- |
| `GET /api/health` | | `{"ok": true}` |
| `GET /api/contest` | | `{name, start, end, freeze, now, open: bool, public_seeds: [..], hidden_count, time_limit, full_per_hour, invite_required: bool, max_source_kb, announcement: string or null}` (values include admin-panel overrides; `announcement` is null unless an admin turned the banner on with non-empty text) |
| `POST /api/register` | `{username, password, email, invite}` | 201 `{username, is_admin, email}` + cookie. username 3–20 chars `[A-Za-z0-9_-]`, password 8+ chars, email required (one `@`, a dot in the domain, no spaces, ≤ 254 chars; stored lowercase). 409 "that username is taken" / "email already registered" (case-insensitive), 403 bad invite (checked before the email) |
| `POST /api/login` | `{username, password}` | `username` may be a username or an email (the key `login` is accepted too). 200 `{username, is_admin, email}` + cookie; 401 on bad credentials; 403 for a disabled account (only after a correct password). The failure limit is keyed on the lowercased identifier |
| `POST /api/logout` | | 204, clears cookie |
| `GET /api/me` | | `{username, is_admin, email}` (email may be null) or 401 |
| `POST /api/account/email` | `{email, password}` | the signed-in user adds or changes their email; current password required (401 "wrong password"); 400 invalid, 409 taken. 200 `{username, is_admin, email}` |
| `POST /api/submissions` | `{kind: "public"|"full", source: "<python code>"}` | 202 `{id}`. 401 not logged in; 403 contest closed (full only; public always allowed while logged in); 413 too big; 429 if the user already has a queued/running submission, or exceeded `PR_FULL_PER_HOUR` |
| `GET /api/submissions` | | `[{id, kind, status, created, started, finished, raw_total, score, relative_total, tests_ac, tests_done, tests_total, position, max_time, error}]` newest first, own only (`score` 0–100 and `relative_total`, see Scoring; both null for public and for unfinished runs; `position` is the place in the queue, null unless queued) |
| `GET /api/submissions/{id}` | | the row above plus `source` and `results`. For **public** submissions results are per seed `{seed, verdict, score, cleared, time, message}`; for **full** submissions results are anonymised `{test: 1..N, verdict, time}` (no seeds, no scores) so hidden seeds stay hidden; admins also get `seed, score, cleared, message` and, once the submission is done, `best` (the best raw on that seed) and `relative_pct` (`100 × score / best`, 2 decimals; 0 when best is 0). Own submissions only, admins any. `position` = place in queue while queued |
| `GET /api/leaderboard` | | `{frozen: bool, rows: [{rank, username, score, relative_total, raw_total, tests_ac, submitted, submissions}]}` (`score` 0–100, 2 decimals) |
| `GET /api/admin/submissions` | | admin only: all submissions with username (see Admin API for filters) |
| `POST /api/admin/rejudge/{id}` | | admin only: back to queued, results cleared, on the same tests as its kind (public = public seeds, full = hidden). 200 `{id, status}`; 409 while running. To run a public submission on the hidden tests use `POST /api/admin/submissions/{id}/run-full` |

## Admin API

Every `/api/admin/*` endpoint: 401 when signed out, 403 for non-admins, CSRF header on state-changing methods (403 without), 404 `{"error": ...}` for an unknown user or submission id, 400 for invalid input, 409 when a safety rule refuses. Every state-changing admin action writes an `audit` row. The site's admin panel is `/admin/`.

**User row** (`U`): `{id, username, email, created, is_admin, admin_env: bool (listed in PR_ADMINS), status: "active"|"disabled", disabled_at, disabled_reason, last_login, public_submissions, full_submissions, best_score (0–100; null without a finished full run), best_relative_total (null likewise), best_submission_id, rank (null when unranked or disabled; same ranks as the live leaderboard), last_submission (ISO or null)}`.

**Admin submission row** (`S`): the `GET /api/submissions` row plus `username, user_id, user_disabled: bool, copied_from` (the public submission id a full row was made from by run-full, else null).

| Method, path | Body / query | Response |
| --- | --- | --- |
| `GET /api/admin/overview` | | `{counts: {registered, active, disabled, admins, active_24h, submissions, submissions_24h, full, public, queued, running, errors}, judge: {queue, running: [{id, username, kind, tests_done, tests_total, started}], worker_last_seen (ISO or null), worker_alive: bool (heartbeat within 30 s)}, verdicts: {AC, WA, TLE, RE} (per-test counts over all judged tests), daily: [{date: "YYYY-MM-DD", registrations, submissions, full}] (last 14 UTC days, oldest first), top: [first 5 live leaderboard rows], contest: (same as GET /api/contest)}`. `active_24h` = users who signed in or submitted in the last 24 h |
| `GET /api/admin/users` | | `{users: [U...] (by id), summary: {registered, active, disabled, admins, with_full}}` |
| `GET /api/admin/users.csv` | | CSV attachment of the same rows. Text cells starting with `= + - @`, tab or CR are prefixed with `'` (formula-injection guard; usernames may start with `-`) |
| `GET /api/admin/users/{id}` | | `U` plus `submissions: [S...]` newest first |
| `POST /api/admin/users/{id}/disable` | `{reason?}` (≤ 200 chars) | `U`. See rules below. Disabling a disabled user is a no-op 200 (no audit row) |
| `POST /api/admin/users/{id}/enable` | | `U` |
| `POST /api/admin/users/{id}/password` | `{password}` (8-256 chars, like registration) | `{ok: true, sessions_revoked}`. Revokes all the user's sessions (an admin resetting their own password keeps the current session) |
| `POST /api/admin/users/{id}/admin` | `{is_admin: bool}` | `U` |
| `POST /api/admin/users/{id}/email` | `{email}` (null or "" clears it) | `U`; 400 invalid, 409 taken. Audit details mask both values (`a***@example.com`) |
| `DELETE /api/admin/users/{id}` | `{confirm: "<username>"}` (must equal the username, case-insensitive) | `{deleted: username, submissions: n}`. 400 when `confirm` doesn't match; 409 while one of their submissions is running (disable first, wait, retry) |
| `GET /api/admin/submissions` | `?user=&kind=public\|full&status=queued\|running\|done\|error&verdict=AC\|WA\|TLE\|RE&since=&until=&limit=1000` | `[S...]` newest first. `user` is an exact username (case-insensitive); `verdict` keeps submissions with at least one test of that verdict; `since`/`until` are ISO times on `created` |
| `DELETE /api/admin/submissions/{id}` | | `{deleted: id}`; removes the source and results. 409 while it is running (cancel isn't possible mid-run; wait for it to finish). The audit row keeps username, id, kind, status, raw total, relative total and score |
| `POST /api/admin/submissions/{id}/cancel` | | `S` with status `error` and error `cancelled by an admin`; 409 unless queued |
| `POST /api/admin/rejudge-bulk` | `{which: "errored"\|"full"}` | `{count, ids}`. `errored`: every submission in status `error` except cancelled ones; `full`: every full submission that is `done` or `error` (not cancelled). Submissions of disabled users are skipped; running/queued ones are left alone |
| `POST /api/admin/submissions/{id}/run-full` | | 200 `{id, from, status: "queued"}`: a NEW full submission (hidden tests) for the same user with the same source, `copied_from` = `{id}`, `created` copied from the original (so it ranks and counts across a freeze as if submitted then). The original is untouched. Not limited by `PR_FULL_PER_HOUR`, the one-waiting-per-user rule or the contest window; it counts on the leaderboard as the user's. 400 if `{id}` is already full (use rejudge); 409 if the user is disabled. Repeating it on the same public submission makes another copy. Audit `submission.run_full` (target username, `{from_id, new_id}`) |
| `POST /api/admin/run-full-bulk` | `{which: "all_public"\|"latest_public"}`, `?dry_run=1` | `{count, ids, skipped}`: queues a full copy (as run-full) of every public submission of an active user (`all_public`) or of each active user's latest public submission (`latest_public`), skipping public submissions that already have a full copy (`skipped` counts those). `dry_run=1` writes nothing and returns `{count, ids: [], skipped, dry_run: true, from_ids}` (`count` = how many would be created). Audit `submission.run_full_bulk` (`{which, count, skipped, pairs: [[from, new]...]}`) |
| `GET /api/admin/submissions.zip` | `?pick=best\|latest` | ZIP attachment: per student one `<username>.py`: their best finished full submission by score (`best`) or their latest full submission (`latest`), plus `manifest.csv` (username, account active/disabled, file, submission_id, created, status, score, relative_total, raw_total) |
| `GET /api/admin/leaderboard.csv` | `?view=live\|public` | CSV attachment: `rank, username, score, relative_total, raw_total, tests_ac, submissions, best_submission_id, best_submitted` (`score` 0–100 is the contest score; `relative_total` stays as a secondary column for exact tie-checking). `public` is what students see (frozen during a freeze) |
| `GET /api/admin/similarity` | `?limit=50` (1-500) | `{pairs: [{a: {username, submission_id}, b: {...}, similarity (0-1), shared, a_size, b_size}], compared, skipped, truncated: bool, method}` most similar first; see Similarity below |
| `GET /api/admin/settings` | | `{editable: {KEY: {value, env, overridden: bool}}, readonly: {hidden_count, public_seeds, time_limit, memory_mb, max_source_kb, sandbox, workers}}`; keys below |
| `POST /api/admin/settings` | `{set: {KEY: value}, clear: [KEY]}` | same as GET. Validated together; nothing is saved on a 400 |
| `POST /api/admin/settings/invite/regenerate` | | same as GET; sets the invite override to a fresh random code (`xxxx-xxxx-xxxx`) |
| `GET /api/admin/audit` | `?actor=&action=&target=&limit=200` (≤ 2000) | `[{id, created, actor, action, target, details}]` newest first; `actor`, `target` exact (case-insensitive), `action` exact or a prefix ending in `.` (e.g. `user.`) |

Editable settings (env default in brackets): `name` [PR_CONTEST_NAME] 1-80 chars; `start`, `end`, `freeze` [PR_START/PR_END/PR_FREEZE] ISO 8601 or null for "unset" (stored UTC; `start < end`, and `freeze` must be at or after `start` and before `end` when those are set); `invite` [PR_INVITE] "" = open registration, else 4-64 printable characters; `full_per_hour` [PR_FULL_PER_HOUR] integer 0-1000; `announcement` [""] ≤ 500 chars; `announcement_on` [false] bool. Hidden/public seeds, time limit, memory and source size stay env-only (changing them would invalidate scores). The api reads overrides on every request; the worker doesn't use any of them. Audit `details` for `settings.update` is `{KEY: [old, new]}`, with invite codes masked (`ab…yz`, or `(open)`).

Audit actions: `user.disable`, `user.enable`, `user.delete`, `user.password`, `user.admin`, `user.email`, `submission.delete`, `submission.cancel`, `submission.rejudge`, `submission.rejudge_bulk`, `submission.run_full`, `submission.run_full_bulk`, `settings.update`, `settings.invite`. The CLI writes them with actor `(cli)`.

### Rules for user actions

- **Disable** (the reversible "remove"): sets `disabled_at`; in the same transaction deletes all the user's sessions and cancels their queued submissions (status `error`, error `cancelled: account disabled`). A submission already running is left to finish (the sandbox can't be stopped cleanly from the api); its result is stored but doesn't count while the user is disabled. A disabled user can't sign in (403 "this account is disabled" after a correct password; 401 for a wrong one) and any leftover session is rejected.
- **Scoring**: disabled users' submissions are excluded everywhere scores are computed: they don't set the per-seed bests (so a cheater can't depress others' relative scores) and don't appear on the public leaderboard. Admins see them on `GET /api/leaderboard` after the ranked rows with `rank: null, disabled: true`, scored against the active users' bests (so they can exceed 1,000,000 per test). Enabling restores everything, because scores are computed when read.
- **Delete** (permanent): removes the user, their sessions, submissions and results; scores recompute on the next read. Audit rows stay.
- **Safety**: an admin can't disable, delete or remove admin from themselves (409). The last active admin (counting PR_ADMINS users who are registered and active) can't be disabled, deleted or de-admined (409). A user listed in PR_ADMINS is an admin whatever the DB flag says, so removing their admin returns 409 "listed in PR_ADMINS; edit PR_ADMINS and restart the api". Disabling or deleting a PR_ADMINS user is allowed (a deleted one would be an admin again on re-registration).

### Similarity

For each active or disabled student with a full submission, take their latest full submission. Tokenise it with Python's `tokenize` (comments, blank lines, indentation and whitespace dropped; string literals become `STR`; identifiers, keywords, numbers and operators kept), form token 5-grams, and remove every 5-gram that also occurs in the official `starter.py`. Similarity of a pair = Jaccard |A∩B| / |A∪B| of their 5-gram sets. The result is cached until the set of latest full submissions changes; a first run over several hundred long solvers takes a few seconds (15 s budget, after which `truncated` is true). Sources that don't tokenise fall back to a regex tokeniser; a student with fewer than 20 remaining 5-grams is counted in `skipped`. At most 600 students (the most recent submitters) are compared; `truncated` says when more exist. Pairs below 0.2 are not returned.

Other rules as built: usernames are unique and log in case-insensitively; 10 failed logins per 15 minutes per (IP, username) gives 429 (uvicorn runs with `--proxy-headers` behind Caddy so it sees real IPs); validation errors are 400; someone else's submission is 404; the hourly limit ignores submissions that ended in `error`; one queued-or-running submission at a time applies to everyone including admins (admins are only exempt from the contest window); admins see seeds and scores in full-run details; during a freeze a student's own list is scored against pre-freeze submissions plus their own.

## Site pages (platform/site/)

- `/` landing: name, one-paragraph pitch, the explainer video (`/media/poker_run_explainer.mp4`), buttons: Play in the browser (`/play/`), Read the problem (`/problem/`), Enter the contest (`/contest/`).
- `/problem/` the statement (rendered from `poker_run/problem/statement.md` at build time) and downloads: starter.py, public inputs zip, statement.md.
- `/play/` the browser game (copied from `poker_run/web/`).
- `/contest/` contest hub: countdown/status from `/api/contest`; login/register forms when logged out; when logged in: submit form (file upload or paste, kind public/full), my submissions table with live polling, submission detail view.
- `/contest/leaderboard/` the leaderboard.
- `/admin/` the admin panel (admins only; others see an "admins only" screen): Overview, Students, Submissions, Settings, Leaderboard, Similarity, Activity. The header shows an Admin link only to admins.
- Every page that loads `site.js` shows the announcement banner from `/api/contest` when it is set.

## Online multiplayer

Rooms where 2-4 signed-in players (plus optional server-side bots) play the
same deal from their own devices. Code: `server/prplatform/rooms.py` (an
APIRouter included by `app.py`), game rules: `poker_run/multiplayer.py` (the
Python port of `poker_run/web/multiplayer.js`) and `poker_run/bot.py`. Player
rules and UI: `poker_run/web/MULTIPLAYER.md`. Online matches never touch the
contest tables or the leaderboard.

**Authority.** The server keeps each room's move log and rebuilds the match
from it (cached in memory per room version). Every move goes through
`Match.apply` for the player's seat: whose turn it is (draft, rounds), the shop
visit order and the shared-shop rule (draft), and the solo rules of that
player's own game, so each move list stays a valid output for the seed. Each
write is one `BEGIN IMMEDIATE` transaction that bumps the room's `version`, so
simultaneous moves are applied one after the other.

**Live updates: long polling.** `GET /api/rooms/{code}?since=V&wait=S` answers
at once if the room's version is not `V`, otherwise waits up to `S` seconds
(max 25) for a change. The wait is async (no thread is held: an in-process
asyncio event per room, plus a re-read every 5 s), so many idle clients cost
almost nothing. It is a plain JSON response, so it passes through Caddy's
`reverse_proxy` and `encode` unchanged (no Caddyfile change; Caddy has no
proxy read timeout by default and `idle 2m` is longer than a poll). The game
polls with `wait=20` while a room is open. All of this, presence and the rate
limits live in the one api process: **run a single uvicorn process** (as
deployed).

**Presence.** Each poll marks the player as seen (in memory, never written).
Online = seen in the last 30 s. A player who is away (not seen for 30 s) and
has kept the game waiting for 90 s (their turn in draft/rounds; no move in
race) is `removable` by any other seated player.

**Expiry.** Lobby idle 30 min: deleted. Game with no move for 2 h: ended and
saved with `ended: "expired"` (players score what they have). Finished rooms:
kept 30 min, then deleted (the saved match stays). Checked when a room is
read and by a sweep at most once a minute.

**Limits** (per user, in memory): 10 new rooms per 10 min; 3 open rooms
(lobby or playing) at once; 300 moves per minute; 120 other room actions per
minute; 500 open rooms on the server (503 above). A move is at most 200
characters and must look like `word [arg ...]` (≤ 5 args of `[A-Za-z0-9_]`).

### Tables (additive; `CREATE TABLE IF NOT EXISTS`)

- `mp_rooms(id, code UNIQUE, host_id, mode 'race'|'draft'|'rounds', rules, seed, max_players, status 'lobby'|'playing'|'finished', version, created, active, started, finished, match_id, rematch_code)`
- `mp_seats(room_id, seat, user_id NULL for bots, bot 'easy'|'normal'|'hard', ready, status 'in'|'left'|'removed', joined)`; PRIMARY KEY (room_id, seat). Seats are renumbered 0..n-1 in join order at start.
- `mp_kicked(room_id, user_id)`: kicked users can't rejoin that room.
- `mp_moves(room_id, n, seat, move, created)`; PRIMARY KEY (room_id, n). Every move in order, bots' included; `!leave` marks a player who left or was removed.
- `mp_matches(id, code, mode, rules, seed, players, started, finished, ended 'done'|'expired')` and `mp_match_players(match_id, seat, user_id, name, bot, total, cleared, place, left_early, moves)`: saved results; `moves` is the move list joined with newlines.

### Endpoints

All need a signed-in user (401 otherwise); POSTs need `X-Requested-With: fetch`. Room codes are 5 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (case-insensitive in URLs); a bad or unknown code is 404 `room not found (it may have expired)`.

| Method, path | Body | Response |
| --- | --- | --- |
| `POST /api/rooms` | `{mode, rules, seed: int or null (random), max_players: 2-4, bots: ["easy"|"normal"|"hard", ...]}` | 201 room view; you are the host in seat 0. 400 bad settings; 429 rate or open-room limit; 503 server full |
| `GET /api/rooms` | | your open rooms `[{code, mode, rules, status, players, max_players, you, created}]` |
| `GET /api/rooms/{code}?since=V&wait=S` | | room view (see below). Non-members get the view with `you: null` (to decide to join). 403 if kicked |
| `POST /api/rooms/{code}/join` | | room view. Already seated: resumes your seat. 409 full or already started; 403 kicked |
| `POST /api/rooms/{code}/ready` | `{ready: bool}` | room view (lobby only) |
| `POST /api/rooms/{code}/settings` | any of `{mode, rules, seed, random_seed: bool, max_players, bots}` | host, lobby only. Changing mode, rules or seed resets the others' ready. 409 if the seats don't fit |
| `POST /api/rooms/{code}/kick` | `{seat}` | host, lobby only. Removes a player (who can't rejoin) or a bot |
| `POST /api/rooms/{code}/start` | | host; needs 2+ seats and every other person ready (409 otherwise). Bots make their opening moves |
| `POST /api/rooms/{code}/move` | `{move: "play 3 17 22"}` | room view after the move (and any bot moves it triggers). 400 illegal move or bad syntax (`error` says why); 409 not your turn / game not running / you left |
| `POST /api/rooms/{code}/leave` | | lobby: frees the seat (host passes on; an empty room is deleted, answer `{deleted: true}`). Game: your run stops and scores what it has |
| `POST /api/rooms/{code}/remove` | `{seat}` | any seated player; the target must be `removable` (away and idle, see above), else 409. Their run stops |
| `POST /api/rooms/{code}/rematch` | `{same_seed: bool}` | finished rooms: `{code}` of a new room with the same settings and bots (you host it); later calls return the same code, which also appears as `rematch` in the old room's view |
| `GET /api/matches` | | `{record: {played, wins}, matches: [match]}` newest first (50); match = `{id, code, mode, rules, seed, started, finished, ended, you: seat, players: [{seat, name, bot, total, cleared, place, left, you}]}` |
| `GET /api/matches/{id}` | | one match with each player's `moves` list; participants only (404 otherwise) |

**Room view.** `{code, mode, rules, seed, max_players, status, version, host: seat, you: seat|null, turn: {seat, stage: "play"|"shop", round}|null, shop_order: [seats]|null (draft shop), owners: {modifier: seat} (draft), created, started, finished, ended, rematch, match_id, expires_in, server_time, players: [...]}`. In the lobby a player is `{seat, name, bot, host, ready, status, you, online}`. In a game: `{seat, name, bot, host, status 'in'|'left'|'removed', you, online, idle, removable, total, cleared, round, phase, over, moves_made, mods, rounds: [{score, cleared, total, plays: [{hand, points}], discards, buys, sells, shop_moves}], hidden, place}` plus `moves` (the full move list) for yourself, and for everyone once the game is finished. In a race, another player's `rounds` (and the `total`, `cleared` and `mods` derived from them) only include rounds you have finished yourself (`hidden: true` while some are held back).

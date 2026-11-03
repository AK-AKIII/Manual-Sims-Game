# Poker Run platform backend

FastAPI api (`prplatform.app:app`) and judge worker (`python -m prplatform.worker`),
sharing one SQLite database (WAL). The contract is [../API.md](../API.md).

```
prplatform/
  app.py       HTTP API (/api/*). Never runs submitted code.
  config.py    PR_* environment variables
  db.py        schema, migration on start, WAL
  auth.py      scrypt passwords, session tokens (cookie = random token, DB = its SHA-256)
  scoring.py   relative scores, leaderboard, freeze
  judge.py     judge one submission (job dir, sandbox run, poker_run.check per test)
  sandbox.py   docker backend (production) and local backend (development only)
  worker.py    claim queued submissions, judge, store; resets 'running' on start
  manage.py    small admin CLI
../judge/      runner.py (runs inside the sandbox) and its Dockerfile
```

## Run locally (local sandbox)

`poker_run` must be importable: put the repo root on `PYTHONPATH` (Docker does
this by copying it in). If you forget, `prplatform.judge` falls back to
appending the repo root (three levels above `prplatform/`) to `sys.path`.

```sh
cd platform/server
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export PYTHONPATH="$PWD:$PWD/../.."
export PR_DB=./data/platform.db PR_ADMINS=me PR_INVITE=sesame PR_SANDBOX=local

.venv/bin/uvicorn prplatform.app:app --port 8000 &     # the api
.venv/bin/python -m prplatform.worker &                # the judge
```

Then, for example:

```sh
H='X-Requested-With: fetch'; CT='Content-Type: application/json'
curl -c jar -H "$H" -H "$CT" -d '{"username":"me","password":"password123","invite":"sesame"}' localhost:8000/api/register
python3 -c "import json;print(json.dumps({'kind':'public','source':open('../../poker_run/starters/starter.py').read()}))" > sub.json
curl -b jar -H "$H" -H "$CT" --data-binary @sub.json localhost:8000/api/submissions
curl -b jar localhost:8000/api/submissions          # poll: status, tests_done/tests_total, position
curl localhost:8000/api/leaderboard
```

The site (platform/site/) is served by Caddy in production; locally you can
point any static server at it and proxy `/api/*` to uvicorn.

**PR_SANDBOX=local is NOT a security boundary.** It runs submissions as your
user with rlimits only. Use it for development and tests, never with code
from people you don't trust. Production uses `PR_SANDBOX=docker` with the
image from `../judge/Dockerfile` (`docker build -t poker-run-judge platform/judge`).

## Configuration

All variables of API.md (`PR_DB`, `PR_SECRET`, `PR_INVITE`, `PR_ADMINS`,
`PR_CONTEST_NAME`, `PR_START`, `PR_END`, `PR_FREEZE`, `PR_HIDDEN_SEEDS`,
`PR_PUBLIC_SEEDS`, `PR_TIME_LIMIT`, `PR_MEMORY_MB`, `PR_MAX_SOURCE_KB`,
`PR_FULL_PER_HOUR`, `PR_SANDBOX`, `PR_JUDGE_IMAGE`, `PR_SECURE_COOKIES`), with
the documented defaults, plus these extensions:

| Variable | Meaning | Default |
| --- | --- | --- |
| `PR_WORKERS` | judging threads inside the one worker process | `1` |
| `PR_JOB_ROOT` | where job temp dirs are created. With docker, the path must be the same on the host (the worker passes it to `docker run -v`); when the worker itself runs in a container with the Docker socket, bind-mount a host dir at the same path | system temp |
| `PR_RUNNER` | path to runner.py for the local sandbox | `../judge/runner.py` |
| `PR_POLL_SECONDS` | worker idle poll interval | `1.0` |

Times are ISO 8601 (`2026-12-01T18:00:00Z`; naive times are UTC). Seeds are
comma/range lists (`81723,90211,1000-1047`). Run **one** worker process per
database (use `PR_WORKERS` for parallelism); on start it puts submissions
stuck in `running` back to `queued`.

Behind Caddy, run uvicorn with `--proxy-headers --forwarded-allow-ips=<caddy ip>`
so the failed-login limiter sees real client addresses.

## Admins

Either list usernames in `PR_ADMINS` (restart the api and worker), or flag a
registered user in the DB:

```sh
python -m prplatform.manage set-admin alice          # --off to remove
python -m prplatform.manage list-users
python -m prplatform.manage set-password alice       # prompts; logs out their sessions
```

Admins may submit full runs outside the contest window, see the live
leaderboard during a freeze, read any submission (full results include seeds
and scores for them), list all submissions (`GET /api/admin/submissions`) and
rejudge (`POST /api/admin/rejudge/{id}`).

## Tests

```sh
cd platform/server
PYTHONPATH="$PWD:$PWD/../.." python -m unittest discover -s tests -v
```

They use temp databases, the local sandbox, 3 hidden and 2 public seeds and a
1 s time limit (about 7 s total). No Docker needed: the docker backend is
tested on the exact command it builds.

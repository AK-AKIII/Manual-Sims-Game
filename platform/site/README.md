# Poker Run contest site (static)

Vanilla HTML/CSS/JS, no frameworks, no external scripts or fonts. The API
contract is [../API.md](../API.md).

## Build

```sh
python3 platform/site/build.py                 # -> platform/site/dist/
python3 platform/site/build.py --out /srv/site # anywhere else
```

Stdlib only (it imports `poker_run` from the repository to generate the public
inputs). The output is rebuilt from scratch on each run and is byte-identical
across runs. What it assembles:

| URL | Source |
| --- | --- |
| `/` | `pages/index.html` (landing, live demo, scroll story, opening intro) |
| `/problem/` | `pages/problem/index.html` + `poker_run/problem/statement.md` and `generator.md`, rendered by build.py's own Markdown renderer |
| `/modes/` | `pages/modes/index.html` + `poker_run/web/MULTIPLAYER.md` and `poker_run/problem/balanced.md` (a `<!-- site:skip -->` line before a heading leaves that section out) |
| `/contest/` | `pages/contest/index.html` (sign in / register, submit, my submissions, account email, admin "all submissions") |
| `/contest/leaderboard/` | `pages/contest/leaderboard/index.html` |
| `/admin/` | `pages/admin/index.html` + `assets/admin.js` (admin panel; signed-out and non-admin visitors get an "Admins only" screen) |
| `/play/` | `poker_run/web/index.html` + every script it loads (`engine.js`, `bot.js`, `multiplayer.js`; the title becomes a link home) |
| `/assets/` | `assets/` (references get `?v=<content hash>` for cache busting) |
| `/media/` | `video/poker_run_explainer.mp4` (+ `.webm` if present) |
| `/downloads/` | `starter.py`, `statement.md`, `poker_run_public.zip` (inputs for seeds 1-10 from `generate`/`format_instance`, plus the baseline outputs and verdicts from `tests/fixtures/poker_run/public/`) |
| `/favicon.ico` | wraps `assets/favicon.png` |

Pages use build-time partials: `<!--#head-->`, `<!--#nav:KEY-->` and `<!--#footer-->`.
`KEY` is a `NAV_ITEMS` key (marked `aria-current`); any other key, like the admin page's `admin`, marks
nothing. The header's Admin link isn't a static nav item: `site.js` adds it for signed-in admins only.
The landing page has a small inline `<script>` in `<head>` (the intro cover guard).
If you deploy a CSP, allow it or move it to a file.

## Preview with the mock API

```sh
python3 platform/site/build.py
python3 platform/site/mock_server.py        # http://127.0.0.1:8765/
```

`mock_server.py` (stdlib `http.server`) serves `dist/` and implements every
endpoint of API.md in memory, including `tests_done`/`tests_total`/`position`
progress. Nothing is persisted.

- Accounts (sign in with the username or the email): `admin` / `adminpass` (admin, treated as listed in
  `PR_ADMINS`, so "Remove admin" on it gives the 409), `helper` / `helperpass` (admin via the database flag),
  `demo` / `demopass` (has history, `demo@example.com`), plus 14 fake students (password `xxxxxxxxxxxx`).
  Most accounts have `<name>@example.com`; `helper`, `kicker` and `low_road` have no email (old accounts).
  `lastcall` copied `momentum` and `hal9000` copied `ace_high`, so the similarity check finds pairs;
  `greedy_gus` has an errored run.
- A new submission is queued for about 2 s, then runs one test at a time (`tests_done` climbs) and finishes.
  Submissions are judged one at a time, in enqueue order.
- The source picks the verdicts: `raise` gives some RE, `sleep` some TLE, `WRONG` some WA, no `print`/`stdout`
  gives all WA, `JUDGE_ERROR` ends with status `error`.
- `rivalrook` gains score every 20 s, so the leaderboard's rank arrows have something to show.
- The whole Admin API of API.md is implemented in memory: overview, users (list, CSV, detail, disable,
  enable, password, admin, email, delete), submissions (filters, delete, cancel, rejudge, bulk rejudge, ZIP),
  `leaderboard.csv`, similarity (Python `tokenize` 5-grams minus `starter.py`, Jaccard), settings (get, set,
  clear, regenerate invite; overrides feed `/api/contest`, registration, the hourly limit and the freeze)
  and the audit log with its filters. The safety rules (self-actions, last active admin, `PR_ADMINS`) and
  the disable rules (sessions dropped, queued runs cancelled, off the public leaderboard; admins see them
  after the ranked rows with `rank: null`) are enforced. The per-test bests are a fixed fake field, so
  disabling someone doesn't move anyone else's relative score in the mock. `POST /api/account/email` too.
- Rules enforced: CSRF header on POSTs and DELETEs (403), 429 while you have a queued/running submission or after 6 full
  runs per hour, 413 over 256 KB, 403 for full submissions when the contest is closed.
- Options: `--invite CODE`, `--frozen`, `--closed`, `--upcoming`, `--no-deadline`, `--speed X`,
  `--latency S`, `--stale-worker` (the overview shows a dead worker), `--port N`, `--dir PATH`, `--quiet`.

## Files

- `assets/site.css`: the whole design system (felt/cream/gold, one dark theme, `color-scheme: dark`).
- `assets/api.js`: API client (`credentials: "same-origin"`, `X-Requested-With: fetch` on every POST and DELETE, `{error}` messages).
  `API.contest()` shares one request for 3 s (site.js and the page scripts both read it at load); `API.contest(true)` forces a fresh one.
  Admin calls are `API.admin*` (one per Admin API endpoint).
- `assets/site.js`: formatting, toasts, count-ups, button ripple and ticks, the signed-in chip (plus the Admin link
  for admins), the announcement banner (from `/api/contest`'s `announcement`; a dismissal is remembered per text in
  `localStorage["pr-announce-dismissed"]`), `UI.dialog` (native `<dialog>` confirm/form dialogs: focus kept inside,
  Escape closes, focus returns, errors inline, optional "type the name to confirm"), the contest clock, the problem-page scrollspy.
- `assets/admin.js`: the admin panel. Seven hash-addressed sections (`#overview`, `#students`, `#submissions`,
  `#settings`, `#leaderboard`, `#similarity`, `#activity`) as ARIA tabs: a sidebar on desktop, a scrollable strip
  under 900 px; arrow keys, Home and End move between them. Only the visible section polls (overview every 5 s,
  submissions every 3 s while something is queued or running, leaderboard every 10 s), never while the
  browser tab is hidden. Students open in a `<dialog>` drawer with the account actions; overview charts are inline SVG.
- `assets/motion.js`: scroll progress line, compacting header, scroll reveals, card tilt, smooth anchors.
- `assets/sound.js`: quiet WebAudio tones, header toggle (remembered in localStorage); silent until a user gesture.
- `assets/code.js`: Python highlighter, line-numbered code view, highlighted editor.
- `assets/landing.js`, `contest.js`, `leaderboard.js`: page logic. `assets/intro.js`: the opening intro.

## Motion and accessibility notes

- Content is visible without JS. Reveals only apply to elements below the fold at load.
- `prefers-reduced-motion`: no intro, no reveals, parallax or tilt. Feedback stays as fades.
- The intro plays once per browser session (`sessionStorage["pr-intro"]`). Any click, key, wheel or touch skips it,
  and so does the Skip button. `/?intro=1` (the footer's "Replay intro") forces it.
  `/?intro=1&introAt=1.6` renders and holds one moment, for review screenshots.
- No `alert()`/`confirm()`. Errors show inline (and as toasts). Every control has a label and a gold focus ring.
  Tabs support the arrow keys. The demo has keys 1-8, P, D and Esc.

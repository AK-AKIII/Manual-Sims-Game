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
| `/contest/` | `pages/contest/index.html` (sign in / register, submit, my submissions, admin) |
| `/contest/leaderboard/` | `pages/contest/leaderboard/index.html` |
| `/play/` | `poker_run/web/index.html` + every script it loads (`engine.js`, `bot.js`, `multiplayer.js`; the title becomes a link home) |
| `/assets/` | `assets/` (references get `?v=<content hash>` for cache busting) |
| `/media/` | `video/poker_run_explainer.mp4` (+ `.webm` if present) |
| `/downloads/` | `starter.py`, `statement.md`, `poker_run_public.zip` (inputs for seeds 1-10 from `generate`/`format_instance`, plus the baseline outputs and verdicts from `tests/fixtures/poker_run/public/`) |
| `/favicon.ico` | wraps `assets/favicon.png` |

Pages use build-time partials: `<!--#head-->`, `<!--#nav:KEY-->` and `<!--#footer-->`.
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

- Accounts: `admin` / `adminpass` (admin), `demo` / `demopass` (has history), plus 14 fake leaderboard players.
- A new submission is queued for about 2 s, then runs one test at a time (`tests_done` climbs) and finishes.
  Submissions are judged one at a time, in enqueue order.
- The source picks the verdicts: `raise` gives some RE, `sleep` some TLE, `WRONG` some WA, no `print`/`stdout`
  gives all WA, `JUDGE_ERROR` ends with status `error`.
- `rivalrook` gains score every 20 s, so the leaderboard's rank arrows have something to show.
- Rules enforced: CSRF header on POSTs (403), 429 while you have a queued/running submission or after 6 full
  runs per hour, 413 over 256 KB, 403 for full submissions when the contest is closed.
- Options: `--invite CODE`, `--frozen`, `--closed`, `--upcoming`, `--no-deadline`, `--speed X`,
  `--latency S`, `--port N`, `--dir PATH`, `--quiet`.

## Files

- `assets/site.css`: the whole design system (felt/cream/gold, one dark theme, `color-scheme: dark`).
- `assets/api.js`: API client (`credentials: "same-origin"`, `X-Requested-With: fetch` on every POST, `{error}` messages).
- `assets/site.js`: formatting, toasts, count-ups, button ripple and ticks, the signed-in chip, the contest clock, the problem-page scrollspy.
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

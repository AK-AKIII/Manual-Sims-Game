# Poker Run: multiplayer modes

The browser game (`index.html`) has a **Mode** switch in its top bar: **Solo**
(the default, the contest's game), **vs Bot** and **Online**. Solo and vs Bot
run entirely in the page (no server, no network; the browser remembers your
game). Online play happens in **rooms** on the contest website's server: each
player on their own device, signed in to their own account.

Every match is set up with **one seed and one ruleset** (Classic or Balanced),
shared by all players: the same draw orders and the same shop queues. Each
player has their own independent run (a `PokerRun.Game`), so each player's
move list is a valid output file for that seed, and the results screen shows
them all with their checker verdicts. Highest total wins; on equal totals,
more rounds cleared; otherwise it's a shared place.

(Earlier versions also had same-device modes, Pass & play and a local Draft.
They are gone: old links to them open the Online screen, a draft link with
Draft preselected.)

## vs Bot

You against a computer player on the same deal. The bot plays its whole run on
its own at the start (it never sees your moves); its rounds are revealed one at
a time: when you finish round r (your last play of the round), the bot's round
r appears in the **Race** panel with its score, its plays, what it bought and
its moves. The panel also compares your totals round for round. When your run
ends, the rest of the bot's run is revealed and the results screen shows the
winner, both scores, both move lists, **Rematch** (same seed) and **New seed**.

Bot levels (`bot.js`, and the same moves from `poker_run/bot.py` on the server):

| Level | What it does | Mean score, seeds 1-20 (classic / balanced) |
| --- | --- | ---: |
| Easy | plays its best hand every time, never discards, buys at most one modifier per shop, never levels | 2,346 / 2,351 |
| Normal | an exact port of `poker_run/baseline.py`: the same moves, move for move | 9,069 / 10,702 |
| Hard | the baseline, plus a look-ahead when it discards (the draw order is known, so it tries ~10 discards and keeps the one that leaves the best play) and a modifier wish list in the shop | 19,496 / 24,145 |

On seeds 21-120: Easy 2,738 / 3,044, Normal 8,435 / 10,426, Hard 21,474 /
21,704. The example solvers in `poker_run/solvers/` score 80,000-95,000.

## Online rooms

Pick **Online**, sign in (the game sends you to the contest page and back),
then **create a room** or **join one with its code**. A room has a mode
(Race, Draft or Rounds), a ruleset, a seed (random unless you pick one) and 2
to 4 seats; empty seats can hold bots (Easy, Normal or Hard), which play on
the server. The room code is 5 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`
(no 0/O or 1/I), and the share link is `/play/#room=CODE`.

**Lobby.** Everyone appears under their username with a colour, a ready tick
and online/away. The host can change the mode, rules, seat count and seed
(that asks everyone to confirm "ready" again), add or remove bots, and kick a
player (who can't come back to that room). The host starts the game once
everyone else is ready. Anyone can leave; if the host leaves, the next player
becomes host; an empty lobby is deleted.

**Reconnecting.** The room belongs to your account, not the tab: reload, or
open the link on another device and sign in, and you are back in your seat.
If the connection drops, a "Connection lost, retrying" banner shows and the
page keeps trying (1 s, 2 s, ... up to every 10 s).

**What you see.** Your own game in full (hand, draw pile, shop, deck). For the
others: their scores, the hand types of their plays, what they bought and sold,
round results, and whether they are online. Their move lists appear at the end.
Toasts tell you when someone clears a round, buys a modifier, leaves or seems
away, and a soft chime says when it's your turn.

**Away players.** A player is *away* when their page hasn't checked in for
30 seconds. If an away player keeps the game waiting for 90 seconds (in Draft
and Rounds: it's their turn; in Race: they made no move), every other player
gets a **Remove** button. A removed player, like one who leaves, stops: their
run scores what it has, and the game goes on without them.

**Idle rooms.** A lobby with no activity for 30 minutes is deleted. A game
with no move for 2 hours ends: it is saved like any finished game ("ended
after a long pause"), everyone scoring what they have. A finished room stays
open for 30 minutes (results, rematch) and is then deleted; the match itself
stays in your history.

**Results.** When every run is over (or left), the server saves the match to
every player's account: the players, final scores, rounds cleared, places,
each player's move list, the mode, rules, seed and times. **Your matches** on
the Online screen lists them with your record (played, won), the details and a
**Replay** button that opens your run in the Replay tab. **Rematch** makes a
new room with the same settings and bots (a new seed, or the same one); the
other players get a button to join it. Online matches never count for the
contest leaderboard.

### Race

Everyone plays the whole run at the same time, each on their own (like vs Bot,
with people). You see the others' progress live (which round they are on);
each of their rounds, with its score, plays and purchases, is revealed when
you have finished the same round yourself. The game ends when every run is
over.

### Draft: the shared shop

Turns go round by round, and the players share one shop. The exact rules:

1. **Two stages per round.** *Play*: every player still in the run plays round
   r, in seat order. *Shop*: then the shop after round r is visited by one
   player at a time. A player's turn ends with the round's last play (play
   stage) or with "Done shopping" (`next`, shop stage).
2. **Visit order: lowest total first.** The players who cleared round r visit
   the shop in increasing order of their total after round r. Ties go in seat
   order starting from seat r mod N (seat 1 first after round 1, seat 2 first
   after round 2, ...), so nobody is always first on a tie. Lowest first is a
   catch-up rule: whoever is behind gets the first pick, which keeps the match
   close and makes being ahead cost something.
3. **One pool of modifiers.** A modifier that another player owns cannot be
   bought. It shows as "Drafted by <name>" in every shop where it is offered:
   for the players after the buyer in that round, and for everyone in later
   rounds (later rounds have their own queues; the name is blocked wherever it
   appears in them).
4. **Queue, window and reroll are as in solo, per player.** Each player has
   their own position in the queue: the window shows 3 slots, a reroll ($2,
   +$1 each time) moves *your* window to the next 3 slots, and a slot you bought
   from stays empty. A drafted offer keeps its place in your window (it is not
   replaced by the next one), so a later pick may see fewer than 3 offers and
   has to decide whether a reroll is worth it.
5. **Selling returns a modifier to the pool.** From that moment anyone may buy
   it again where it is offered. A player who goes out, leaves or is removed
   keeps their modifiers (they stay drafted).
6. Levels, deck edits (remove, paint, promote), +discards and +hand size are
   private. Bots take part: they buy greedily from what is left.

Because the draft only *forbids* some purchases and never changes the window,
every player's moves are legal in a solo game too: each move list is a valid
output for the seed, checked by `PokerRun.check` and by
`python3 -m poker_run check`.

### Rounds

Turns round by round without a shared shop: player 1 plays round 1 and the
shop after it, then player 2 plays round 1 and its shop, and so on; then round
2. A player who goes out (or leaves) is skipped.

<!-- site:skip (the website's /modes/ page leaves out this repository-only section) -->
## Code

* `bot.js` (`PokerBot`): `bestPlay(game)`, `roundMoves(game, level, hands)`,
  `shopMoves(game, level, hands, avail)`, `solve(inst, rules, level)`.
  `poker_run/bot.py` is its Python port (the server's bots).
* `multiplayer.js` (`PokerMulti.Match`): a pure layer over several `Game`s, no
  DOM. `new Match(inst, { mode: "race" | "rounds" | "draft", rules, players:
  [{ name, bot }] })`; `turn`, `apply(move)`, `tryMove`, `undo`, `shopOrder(r)`,
  `ownerOf(name, seat)`, `shopSlots(seat)`, `standings()`, `output(seat)`,
  `toJSON()` (and `new Match(inst, json)` replays it). The page uses its `race`
  mode for vs Bot.
* `poker_run/multiplayer.py` (`Match`): the Python port, the online rooms'
  authority. It adds `leave(seat)` (logged as the pseudo-move `!leave`) for
  players who leave or are removed.
* The server side is `platform/server/prplatform/rooms.py`; its HTTP API is in
  `platform/API.md`, "Online multiplayer". The page talks to it with same-origin
  `fetch` (long polls for updates), so online play needs the website; the game
  opened on its own (`file://`) says so.
* `index.html` loads the scripts with plain `<script src>` tags after `engine.js`;
  `platform/site/build.py` copies every script the page loads to `/play/`.
* Tests: `tests/test_poker_run_multiplayer.py` runs `multiplayer_driver.js` and
  `draft_parity_driver.js` in Node and checks, with the Python simulator and
  checker: every bot move list is valid, the Normal bot equals the baseline,
  the Python bots equal the JS bots, every draft follows the rules above, and
  `multiplayer.py` agrees with `multiplayer.js` on random move sequences (legal
  and illegal attempts: wrong seat, drafted buys, sells, rerolls).
  `platform/server/tests/test_rooms.py` covers the rooms.

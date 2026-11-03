# Poker Run: multiplayer modes

The browser game (`index.html`) has a **Mode** switch in its top bar: **Solo**
(the default, the contest's game), **vs Bot**, **Pass & play** and
**Draft**. Everything runs in the page: no server, no network. The chosen mode
is remembered by the browser (`localStorage`), and so is each mode's match:
reloading the page puts you back where you were, on the pass screen if that is
where you were.

Every match is set up with **one seed and one ruleset** (Classic or Balanced,
from the Rules switch), shared by all players: the same draw orders and the
same shop queues. Each player has their own independent run (a
`PokerRun.Game`), so each player's move list is a valid output file for that
seed, and the results screen shows them all with their checker verdicts.
Highest total wins; on equal totals, more rounds cleared; otherwise it's a
shared place.

## vs Bot

You against a computer player on the same deal. The bot plays its whole run on
its own at the start (it never sees your moves); its rounds are revealed one at
a time: when you finish round r (your last play of the round), the bot's round
r appears in the **Race** panel with its score, its plays, what it bought and
its moves. The panel also compares your totals round for round. When your run
ends, the rest of the bot's run is revealed and the results screen shows the
winner, both scores, both move lists, **Rematch** (same seed) and **New seed**.

Bot levels (`bot.js`):

| Level | What it does | Mean score, seeds 1-20 (classic / balanced) |
| --- | --- | ---: |
| Easy | plays its best hand every time, never discards, buys at most one modifier per shop, never levels | 2,346 / 2,351 |
| Normal | an exact port of `poker_run/baseline.py`: the same moves, move for move | 9,069 / 10,702 |
| Hard | the baseline, plus a look-ahead when it discards (the draw order is known, so it tries ~10 discards and keeps the one that leaves the best play) and a modifier wish list in the shop | 19,496 / 24,145 |

On seeds 21-120: Easy 2,738 / 3,044, Normal 8,435 / 10,426, Hard 21,474 /
21,704. The example solvers in `poker_run/solvers/` score 80,000-95,000.

## Pass & play (2-4 players)

Hot-seat on one device. Names and colours are set up first (defaults Player 1
to Player 4); a seat can also be a bot. Turns go **round by round**: player 1
plays round 1 and then the shop after it, then player 2 plays round 1 and its
shop, and so on, then round 2. Between turns a **pass screen** hides the hand
("Pass to Ben"), says what the others did since your last turn and shows the
standings (every player's score in every round). You can undo moves of your own
turn, not earlier ones. A player who goes out is skipped.

## Draft (2-4 players, humans and bots): the shared shop

Like Pass & play, but the players share one shop. The exact rules:

1. **Two stages per round.** *Play*: every player still in the run plays round
   r, in seat order (with pass screens). *Shop*: then the shop after round r is
   visited by one player at a time. A player's turn ends with the round's last
   play (play stage) or with "Done shopping" (`next`, shop stage).
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
   it again where it is offered. A player who goes out keeps their modifiers
   (they stay drafted).
6. Levels, deck edits (remove, paint, promote), +discards and +hand size are
   private. Bots take part: they buy greedily from what is left.

Because the draft only *forbids* some purchases and never changes the window,
every player's moves are legal in a solo game too: each move list is a valid
output for the seed, checked by `PokerRun.check` and by
`python3 -m poker_run check`.

<!-- site:skip (the website's /modes/ page leaves out this repository-only section) -->
## Code

* `bot.js` (`PokerBot`): `bestPlay(game)`, `roundMoves(game, level, hands)`,
  `shopMoves(game, level, hands, avail)`, `solve(inst, rules, level)`.
* `multiplayer.js` (`PokerMulti.Match`): a pure layer over several `Game`s, no
  DOM. `new Match(inst, { mode: "race" | "rounds" | "draft", rules, players:
  [{ name, bot }] })`; `turn`, `apply(move)`, `tryMove`, `undo`, `shopOrder(r)`,
  `ownerOf(name, seat)`, `shopSlots(seat)`, `standings()`, `output(seat)`,
  `toJSON()` (and `new Match(inst, json)` replays it). vs Bot is the `race`
  mode, Pass & play `rounds`, Draft `draft`.
* `index.html` loads both with plain `<script src>` tags after `engine.js`;
  `platform/site/build.py` copies every script the page loads to `/play/`.
* Tests: `tests/test_poker_run_multiplayer.py` runs
  `multiplayer_driver.js` in Node and checks, with the Python simulator and
  checker: every bot move list is valid, the Normal bot equals the baseline,
  pass-and-play games are independent, and every draft follows the rules
  above (re-derived from the move history in Python).

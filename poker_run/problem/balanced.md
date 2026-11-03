# Poker Run: the balanced ruleset

An optional variant of the rules in [statement.md](statement.md). **The contest
is judged with the classic rules**; balanced is an extra choice for playing
and experimenting. Inputs are the same files for both rulesets (same
generator, same targets); only the scoring and shop rules change.

## Every difference from classic

| | Classic | Balanced | Why |
| --- | --- | --- | --- |
| Full House | 45 base × 4 mult, +25 / +2 per level | 35 × 4, +20 / +2 | Full House made 63-69% of the example solvers' points: with known draws and discards it is available almost every round, and it got tricolor and twin_engine for free. |
| Flush | 35 × 4, +25 / +2 | 40 × 5, +30 / +2 | A flush can never use tricolor or twin_engine; now it is a real alternative (and painting cards, which is cheaper, feeds it). |
| Three of a Kind | 25 × 3, +20 / +2 | 30 × 3, +25 / +2 | Makes the 3-card hand worth playing, with short_hand. |
| tricolor | $6, x2 mult when the scored cards have **3 or more** suits | $7, x2 mult when they have **exactly 3** suits | A three of a kind always has 3 suits, so tricolor doubled every Full House: it was bought in ~80% of runs and ~40% of all points depended on it. Now a Full House qualifies only when its pair's suits are among its three of a kind's (about half the time); four suits never do. Choosing the cards matters. |
| short_hand | $7, x2 mult when you play 3 or fewer cards | $7, **x3** | Never bought: x2 did not close the gap between 3-card and 5-card hands. |
| collector | $6, +10 base per Straight / Straight Flush played, for good | **$5, +20** | Never bought. +20 makes a straight build worth it (forcing the tuned solver to buy it: +4% mean score; +25 was +27%, too strong). |
| momentum | $6, +2 mult per play in a row of the same hand type | **$5, +3** | Never bought. |
| thin_deck | $6, +1 mult per card your deck is below 52 | $6, **+2** | Never bought; together with $2 removals a thin deck is a real build. |
| ace_dividend | $4, +$2 per scored ace | **$3, +$3** | Never bought. |
| spare_hands | $4, +$2 per unused discard when you clear a round | **$3, +$3** | Never bought; now a real trade-off between discarding and money. |
| savings | $5, interest cap +$5 (only pays if you hold $30+) | **$4**, interest **$1 per $3 held** (not per $5), cap +$5 | Holding $15 already pays the full $5, $30 pays $10: a banking strategy. |
| remove / paint / promote | $3 a card | **$2** a card | Deck editing was rarely used (under one edit per run). |
| `end` | allowed in any round once the target is met | **not allowed in the last round** | Money is worthless after the last round, so ending it early can only throw points away; the last round is always played out. |

Everything else is unchanged: the other hand types, the other modifiers
(rose_tint, coal_seam, low_road, high_court, twin_engine, full_spread,
last_call, mirror, odd_job, even_split), level prices, reroll, +discard and
+hand size prices, slots, the shop window, round rewards and interest
without savings.

## Using it

```
python3 -m poker_run check in.txt out.txt --rules balanced
python3 -m poker_run batch --seeds 1-20 --solver "python3 my_solver.py" --rules balanced
python3 -m poker_run versus --solvers "python3 a.py" "python3 b.py" --rules balanced
```

`check`, `batch` and `versus` take `--rules classic|balanced` (default
classic). `batch` and `versus` also pass it to the solver in the environment
variable `POKER_RULES`; the baseline and the example solvers in
`solvers/` read it (`tuned.py` then loads `tuned_weights_balanced.json`). A
verdict scored with balanced rules has `"rules": "balanced"`.

In code: `Game(inst, rules="balanced")` (Python, `poker_run.sim`) or
`new PokerRun.Game(inst, "balanced")` / `new PokerRun.Game(inst, { rules:
"balanced" })` (JavaScript, `web/engine.js`); `rules.get_ruleset(name)` /
`PokerRun.getRuleset(name)` return the tables, and `game.rules` is the
ruleset a game plays by. The browser game has a **Rules: Classic /
Balanced** switch in the top bar (remembered by the browser, and in the
page's URL as `&rules=balanced`).

## Measured effect

`python3 -m poker_run.tools.balance_stats` runs solvers on many seeds and
reports where the points come from. On the held-out seeds 2001-2100 (100 runs
per cell; "beam (no prior)" is beam_search without the shop's built-in bonus
for tricolor and last_call):

| Solver | Mean score | Rounds cleared | Won | Full House share of points |
| --- | ---: | ---: | ---: | ---: |
| baseline | 7,799 → 9,396 | 3.02 → 3.24 | 0 → 0 | 26% → 15% |
| tuned | 86,244 → 69,519 | 6.73 → 6.48 | 2 → 0 | 69% → 43% |
| beam_search | 94,803 → 82,823 | 7.01 → 6.83 | 14 → 7 | 65% → 36% |
| monte_carlo | 94,065 → 80,690 | 7.06 → 6.79 | 22 → 1 | 63% → 35% |
| beam (no prior) | 90,555 → 81,460 | 6.96 → 6.89 | 12 → 9 | 65% → 36% |

* Hand types (beam_search): Full House 65% → 36%, Straight 21% → 34%,
  Flush 5% → 14%, Two Pair 6% → 9%, Three of a Kind 0% → 4%.
* Modifiers: 17 of the 18 are bought by tuned, beam_search or monte_carlo
  on some seeds (classic: 14 on these seeds). savings is the exception: these
  solvers never hold money long enough for it to pay. tricolor's share of
  all points drops from ~40% to ~32%.
* Deck edits per run: beam_search 0.62 → 1.29, monte_carlo 0.73 → 1.59,
  tuned 0 → 2.67.
* Slightly harder: about 0.2 fewer rounds cleared and 10-20% lower mean
  scores for the search solvers (they were built and tuned for classic).

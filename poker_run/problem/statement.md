# Poker Run

You play a run of **R rounds** of poker hands with a 52-card deck. Each round has a score target; miss one and the run ends. Between rounds you visit a shop to buy modifiers, level up hand types and edit your deck. You know everything in advance: the input lists the draw order of every round and every shop's offers. Output the moves that score the most points.

## Cards

Cards have ids `0`–`51`. Card `i` starts with rank `i mod 13` (`0`=2, `1`=3, …, `8`=10, `9`=J, `10`=Q, `11`=K, `12`=A) and suit `i div 13` (`0`=S, `1`=H, `2`=D, `3`=C). So `0` is 2♠, `12` is A♠, `13` is 2♥ and `51` is A♣. The shop can change a card's rank or suit; its id never changes.

A card's **chips** are its face value for 2–10, 10 for J, Q and K, and 11 for an ace.

## A round

At the start of round `r` your draw pile is that round's draw order from the input, keeping only the cards still in your deck. You draw from the front until you hold **H** cards (your hand size). You have **P** plays and **D** discards.

- **play** 1 to 5 cards from your hand. They score (below), leave your hand, and you draw back up to your hand size.
- **discard** 1 to 5 cards from your hand. They leave your hand and you draw back up. Uses one discard.
- **end** the round early. Allowed only once the round's points have reached its target.

The round ends when you use your last play, when your hand is empty, or when you `end` it. If the round's points are below its target **T_r**, the run is over. Otherwise you have cleared the round and earn money:

- $4, plus
- $1 for each unused play, plus
- interest: $1 for every $5 you held when the round ended, at most $5.

After the last round there is no shop and the run is over.

## Scoring a play

The cards you play make the strongest hand type they can, in this order from weakest to strongest:

| Code | Hand | Base | Mult | Per level |
| --- | --- | ---: | ---: | --- |
| HC | High Card | 5 | 1 | +10 base, +1 mult |
| PR | Pair | 10 | 2 | +15, +1 |
| TP | Two Pair | 20 | 2 | +20, +1 |
| TK | Three of a Kind | 25 | 3 | +20, +2 |
| ST | Straight | 30 | 4 | +30, +2 |
| FL | Flush | 35 | 4 | +25, +2 |
| FH | Full House | 45 | 4 | +25, +2 |
| FK | Four of a Kind | 60 | 6 | +30, +3 |
| SF | Straight Flush | 80 | 8 | +40, +3 |
| FV | Five of a Kind | 100 | 10 | +35, +3 |
| HF | Flush House | 120 | 11 | +40, +3 |
| F5 | Flush Five | 150 | 12 | +50, +3 |

Straights and flushes need all 5 cards. A-2-3-4-5 is a straight; there is no wrap-around (Q-K-A-2-3 is not). FV, HF and F5 need duplicate cards, which the shop's `paint` and `promote` can make.

The **scored cards** are the cards that make the hand: all five for ST, FL, FH, SF, FV, HF and F5; the cards whose rank appears 2 or more times for PR, TP, TK and FK; the highest card for HC. Other cards you play are kickers: they leave your hand but don't score.

```
base  = hand base + (level - 1) * base per level + chips of the scored cards
mult  = hand mult + (level - 1) * mult per level
then each modifier you own adds to base or mult, or multiplies the result
points = base * mult * (product of all multipliers)
```

All arithmetic is on integers.

## Modifiers

You own at most **M** modifiers, never two of the same one.

| Name | Price | Effect |
| --- | ---: | --- |
| rose_tint | 5 | +3 mult for each scored heart or diamond |
| coal_seam | 5 | +3 mult for each scored spade or club |
| low_road | 4 | +12 base for each scored 2, 3, 4, 5 or 6 |
| high_court | 5 | +3 mult for each scored J, Q or K |
| ace_dividend | 4 | +$2 for each scored ace |
| twin_engine | 6 | +4 mult for each rank that appears 2+ times among the scored cards |
| short_hand | 7 | ×2 when you play 3 or fewer cards (kickers count) |
| full_spread | 5 | +40 base when you play exactly 5 cards |
| last_call | 7 | ×3 on the last play of a round (when you have 1 play left before playing) |
| momentum | 6 | +2 mult for each play in a row before this one with the same hand type, counted since you bought it |
| collector | 6 | +10 base for good each time you play ST or SF (counted before scoring that play); adds its total to every play |
| thin_deck | 6 | +1 mult for each card your deck is below 52 |
| mirror | 6 | scored cards add their chips a second time |
| tricolor | 6 | ×2 when the scored cards have 3 or more different suits |
| savings | 5 | interest cap +$5 (so $10) |
| spare_hands | 4 | +$2 for each unused discard when you clear a round |
| odd_job | 5 | +2 mult for each scored A, 3, 5, 7 or 9 |
| even_split | 5 | +15 base for each scored 2, 4, 6, 8 or 10 |

## The shop

After clearing round `r` (except the last) you are in the shop. Its offers come from that shop's queue in the input: you see 3 at a time, starting with the first three. A bought offer leaves the window. `reroll` moves the window to the next three in the queue.

| Move | Effect | Price |
| --- | --- | --- |
| `buy NAME` | buy a modifier in the window | its price |
| `sell NAME` | sell a modifier you own | half its price, rounded down |
| `level CODE` | that hand type gains a level | 2 + 2 × current level |
| `remove i` | remove card `i` from your deck (deck stays at least 20 cards) | 3 |
| `paint i S` | card `i` becomes suit `S` (S, H, D or C) | 3 |
| `promote i` | card `i`'s rank goes up by one (not an ace) | 3 |
| `discards` | +1 discard every round from now on | 8 × (times bought + 1) |
| `handsize` | +1 hand size from now on (at most 10) | 10 × (times bought + 1) |
| `reroll` | show the next three offers | 2, +1 for each reroll in this shop |
| `next` | leave the shop and start the next round | free |

You start the run with $4. Money never goes below zero: a move you can't afford is illegal.

## Input

```
R H P D M Q
T_1 T_2 ... T_R
O_1            (52 card ids: the draw order of round 1, first drawn first)
...
O_R
S_1            (Q modifier names: the offer queue of the shop after round 1)
...
S_{R-1}
```

Every test uses `R=8, H=8, P=4, D=3, M=5, Q=12` and targets `300 600 1200 2400 4800 9600 19200 38400`. Only the draw orders and offer queues change from test to test.

## Output

```
S
move_1
...
move_S
```

One move per line, as in the tables above, for example `play 12 25 38`, `discard 3 4 5`, `buy momentum`, `level FL`, `paint 7 H`, `next`.

You may stop before the run is over. Any illegal move (a card not in your hand, a move from the wrong phase, an item you can't afford, more moves after the run is over) makes the whole output invalid.

## Score

Your score on a test is the total points of all your plays across the run, including the round you failed. An invalid output scores 0.

The time limit is **2 seconds** per test.

## Example

Round 1 of seed 7 starts with `23 9 50 17 45 8 26 16` in hand: Q♥ J♠ K♣ 6♥ 8♣ 10♠ 2♦ 5♥. The best play right now is a High Card (K♣: base 5 + 10 = 15, mult 1, **15 points**).

The next cards in the draw order are `40 33 47 29`: 3♣ 9♦ 10♣ 5♦. So `discard 26 16 17 45` draws them, and then `play 33 8 9 23 50` (9♦ 10♠ J♠ Q♥ K♣) is a Straight: base 30 + 9 + 10 + 10 + 10 + 10 = 79, mult 4, **316 points**, enough for the round's target of 300 with three plays to spare.

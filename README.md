# Manual-Sims-Game

Small trading and strategy games.

| Game | What it is | Where |
| --- | --- | --- |
| Informed Flow | Browser market making game: quote a bid and ask against noise, tipped and informed counterparties | [counterparty/index.html](counterparty/index.html) |
| Poker Hands | Bot competition: a jokerless Balatro played by Python bots | [poker_hands/](poker_hands/), bots in [bots/](bots/) |

---

## Poker Hands

### Rules

1. The deck is shuffled once per seed. You hold `hand_size` cards (default 7).
2. Each turn you either **play** 1 to 5 cards or **discard** 1 to 5 cards. Either way those cards are gone, and you draw back up to your hand size.
3. A play scores the points of its best poker hand type (table below). You get `plays` plays (X, default 8) and `discards` discards (Y, default 3).
4. The game ends when you are out of plays, or when the deck is empty and your hand is used up.
5. Your score is your total points. Money is only for the shop.

| Hand | Points | Money | | Hand | Points | Money |
| --- | ---: | ---: | --- | --- | ---: | ---: |
| High Card | 5 | 1 | | Full House | 60 | 6 |
| Pair | 10 | 1 | | Four of a Kind | 90 | 8 |
| Two Pair | 20 | 2 | | Straight Flush | 120 | 10 |
| Three of a Kind | 30 | 3 | | Five of a Kind* | 150 | 12 |
| Straight | 40 | 4 | | Flush House* | 160 | 14 |
| Flush | 45 | 4 | | Flush Five* | 200 | 16 |

\* Only possible with a random deck, which can contain duplicate cards. Straights and flushes need all 5 cards. Played cards score as the highest-paying type they make.

**Shop.** Buying doesn't use up your turn.

| Item | Effect | Price |
| --- | --- | --- |
| `level:<hand>` | That hand pays +50% of its base points per level | 4 × current level |
| `hand_size` | See one more card at once (max 10) | 12 × (times bought + 1) |
| `discard` | One more discard | 6 × (times bought + 1) |
| `play` | One more play | 15 × (times bought + 1) |

**Variants.** You can change all of these in a JSON config; see [poker_hands/config.py](poker_hands/config.py) for every setting.

- `"deck": "random"`: 52 cards, each picked at random from the 52 faces, so the deck changes from game to game.
- `"points_variation": 0.25`: each hand's points are scaled by a random factor in [0.75, 1.25] per seed.
- `"economy": false`: no money and no shop.

### Writing a bot

A bot is one `.py` file in `bots/`. It defines either `play(state)` or `STRATEGIES = [s1, s2, s3]` (1 to 3 functions). Each strategy plays every seed, and **the bot's score is the mean of its strategies' average scores**. Start from [bots/greedy_bot.py](bots/greedy_bot.py).

A strategy returns one of these actions:

```python
{"action": "play",    "cards": [0, 3, 4]}       # indices into state["hand"]
{"action": "discard", "cards": [1, 2]}
{"action": "buy",     "item": "hand_size"}      # or "discard", "play", "level:Flush"
```

`state` contains:

| Key | Meaning |
| --- | --- |
| `hand` | your cards, like `["AS", "TD", "7H"]` (ranks `23456789TJQKA`, suits `SHDC`) |
| `plays_left`, `discards_left`, `score`, `money`, `hand_size` | your resources |
| `deck_remaining` | cards left to draw |
| `unseen` | count of every card you haven't seen yet: the deck plus opponents' hands |
| `points`, `levels`, `money_table` | what each hand type pays you right now |
| `shop` | item → price, for everything you can buy now |
| `opponents`, `history` | other seats' public state, and every action so far (shared mode) |
| `mode`, `seat`, `round`, `max_play_cards`, `max_discard_cards` | game info |

If an action is illegal, or your bot raises an exception, your game ends with the score you have so far. Helpers are in `poker_hands`: `best_play(hand, points)`, `classify(cards, points)`, `rank_of(card)`, `check_action(state, action)`.

### Running

```bash
python3 -m poker_hands run bots                          # solo: every bot gets its own copy of the same deck
python3 -m poker_hands run bots --mode shared            # everyone draws from one deck, taking turns
python3 -m poker_hands run bots --mode shared --table pairs   # every pair head to head, both seat orders
python3 -m poker_hands run bots --config configs/random_deck.json --games 500 --out results.json
python3 -m poker_hands play --seed 7                     # play it yourself
python3 -m poker_hands play --vs bots/greedy_bot.py      # play against a bot on a shared deck
python3 -m unittest discover -s tests -t .               # tests
```

The same seed always produces the same deck, so results can be reproduced. Shared mode also reports each bot's win rate.

Bots run in the same Python process as the engine, with no time limit or sandbox. Only run bot files from people you trust.

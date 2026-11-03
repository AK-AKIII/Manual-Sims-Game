# Poker Run: example solvers

Four example solvers for [Poker Run](../problem/statement.md), each showing one
classic search or optimisation method. Read them, run them, then write a
better one.

| File | Method | In one sentence |
| --- | --- | --- |
| `common.py` | shared toolbox | fast scorer, round simulator, move generators, greedy and beam round players, the simulation-guided shop, the driver |
| `beam_search.py` | beam search | keep the `WIDTH` best partial rounds at each step |
| `monte_carlo.py` | flat Monte Carlo | judge each move by noisy greedy rollouts to the end of the round |
| `mcts.py` | Monte Carlo Tree Search (UCT) | grow a search tree where rollouts look promising |
| `tuned.py` + `tune.py` | learned heuristic | a fast rule-based policy whose ~35 weights are found by an evolution strategy |

All of them read the input on stdin and print the output on stdout:

```
python3 -m poker_run gen --seed 7 > in.txt
python3 poker_run/solvers/beam_search.py < in.txt > out.txt
python3 -m poker_run check in.txt out.txt --no-json
```

and can be scored on many tests at once:

```
python3 -m poker_run batch --seeds 1-20 --solver "python3 poker_run/solvers/beam_search.py"
```

Each solver's work is bounded by its parameters (beam width, rollouts,
iterations, shop horizon), so its output does not depend on the machine:
the same input always gives the same output. `TIME_LIMIT` = 1.7 s of wall
time is only a safety net (environment variable `POKER_TIME` changes it); on
a machine much slower than ours, or a busy one, it kicks in and the solver
stops searching early (still printing a valid output, but a weaker one).
Randomised solvers use fixed seeds.

`python3 poker_run/solvers/common.py` self-tests the fast scorer against the
simulator.

All the solvers also play the optional balanced ruleset
([`../problem/balanced.md`](../problem/balanced.md)): the environment variable
`POKER_RULES=balanced` selects it (`python3 -m poker_run batch --rules balanced
...` sets it for you). Every number the solvers use comes from the game's
ruleset; `tuned.py` then loads `tuned_weights_balanced.json`, weights tuned
for those rules (`POKER_RULES=balanced python3 poker_run/solvers/tune.py`).

## Two things to understand first

**1. Inside a round, nothing is random.** The input gives the draw order of
every round. Playing or discarding k cards always draws the next k cards of
the pile, so a round is a deterministic puzzle of 7-9 moves. You can try a
move, see exactly what happens, and undo it. `common.RoundCtx` does this on a
light `RoundState` (hand, position in the pile, plays/discards left, score),
much faster than `Game.clone()`. A finished round is turned into one number:

```
value = 10^12 (if the round is cleared) + DOLLAR * money earned + points
```

Clearing comes first, because failing ends the run. Money comes next because
it builds the engine: ending a round early (`end`) keeps the unused plays, $1
each. `DOLLAR` (points per $) decides when to stop: about 5000 worked best,
so we stop as soon as the target is met in early rounds and keep playing only
when a play is worth more than 5000 points. In the last round money is
worthless and every solver maximises points.

**2. The shop is where the score is.** Targets double every round, from 300 to
38400. The baseline's engine never grows, so it dies around round 4. All the
solvers here share `common.Shop`, which judges each purchase by *simulating
the next rounds with their real draw orders*:

```
repeat (at most max_steps times):
    for every affordable purchase (buy/swap a modifier, reroll + buy,
            level a hand type, handsize, discards, bundles of deck edits):
        apply it to a copy of the game
        play the next `horizon` rounds with the fast greedy player (no more shopping)
        value = sum over those rounds of log2(points / target)
                - penalty if the next round would be failed
                + money_value * (money kept + its interest)
                + prior bonus for multiplicative modifiers (mod_bias)
    buy the purchase with the largest gain, stop if none gains
```

Two lessons from building it:

* **Greedy spending is a trap.** A flat bonus (odd_job, high_court) looks as
  good as a x2 multiplier when the engine is small, so a myopic shop fills its
  five slots with flat bonuses and has no money or slot left when tricolor or
  last_call shows up. Limiting purchases per shop (`max_steps=3`) and adding a
  prior bonus for the multipliers (`mod_bias`) each added ~10%.
* **Fewer purchases early.** Allowing only one purchase in each of the
  first four shops (`early_rounds=4, early_steps=1`) raised the mean score by
  ~20%: money saved early earns interest and buys the multipliers later.
* **Look further ahead.** Simulating 4-5 rounds per purchase instead of 2
  cleared clearly more rounds (the last rounds are what the engine is for).
* **Spend everything in the last shop** (`final_spend`): money is worthless
  after it.

## The solvers

### beam_search.py

Beam search over the moves of a round. Each layer expands every kept state
by every candidate move (best ~12 plays, ~10 discards, `end`), scores finished
rounds exactly, merges duplicates (same hand, same pile position, same plays
and discards left) and keeps the `WIDTH` best unfinished states by a heuristic:
points so far + best play in hand + a share of it per remaining play. If one
more play clears the round the state is valued like the finished round it
leads to. It starts from the greedy solution, so it is never worse than
greedy.

Parameters: `WIDTH` (10), `DOLLAR` (5000), `SHOP` (`horizon=5`).

### monte_carlo.py

Flat Monte Carlo. At each decision, every candidate move gets `ROLLOUTS`
rollouts: finish the round with an epsilon-greedy policy (greedy move, or a
random candidate with probability `EPS`). The move with the best rollout
wins.

*Max, not mean.* In games with hidden information you average rollouts,
because one lucky rollout proves nothing. Here the future is known: the only
randomness in a rollout is our own choice of moves, and every rollout is a
sequence we can actually play. So the best rollout is a proven lower bound on
the move's value; the mean only measures how well the move forgives our own
random mistakes. The solver also keeps the best complete round ever simulated
(the incumbent) and outputs it.

Parameters: `ROLLOUTS` (4), `EPS` (0.25), `MAX_CANDS` (10), `SHOP` (`horizon=3`,
leaving time for rollouts).

### mcts.py

UCT. Selection by `Q + C * sqrt(ln N / n)`, expansion with progressive
widening (a node with n visits may have `1 + PW_C * n^PW_ALPHA` children, taken
in prior order: `end`, plays by points, then discards), epsilon-greedy
rollouts, backup of rewards squashed into [0, 1]. Because the game is
deterministic, Q mixes the mean with the best reward seen below the node
(`BETA`), and the answer is the best round ever simulated. After `ITERS`
iterations it commits to the next move, keeps that subtree, and searches
again.

Parameters: `ITERS` (40), `C_UCB` (0.4), `PW_C`/`PW_ALPHA` (1.5/0.5), `BETA`
(0.5), `EPS` (0.2), `SHOP` (`horizon=3`).

### tuned.py and tune.py

No simulation in the shop: every action gets a utility from a formula with
weights (`mod_<name>` for each modifier, `level`, `level_decay`, `handsize`,
`discards`, `remove`, `paint`, savings reserve `save`, ...), using what a
single greedy simulation of the next round says about hand types and suits.
Rounds use a narrow beam (`width`). It runs in ~0.15 s, so `tune.py` can try
hundreds of weight vectors:

```
python3 poker_run/solvers/tune.py --minutes 8      # improves tuned_weights.json
python3 poker_run/solvers/tune.py --fresh          # restart from DEFAULT_WEIGHTS
```

`tune.py` is a (1+1) evolution strategy with the 1/5th success rule: mutate
~30% of the weights on a log scale, keep the mutant if its mean score on the
training seeds 1001-1060 is higher, widen the mutation after a success and
narrow it after a failure. Never tune on the public seeds 1-10: you want
weights that generalise, not weights that memorise ten tests.

## Results

Measured with `python3 -m poker_run batch --seeds 1-20 --solver "..."` on an
Apple-silicon laptop (Python 3.13), default `TIME_LIMIT` (1.7 s). "won" =
runs that cleared all 8 rounds. All four solvers also produce valid outputs
on seeds 1-50 (0 invalid).

| Solver | Mean score | Mean rounds cleared | Won (of 20) | Max time |
| --- | ---: | ---: | ---: | ---: |
| baseline (`python3 -m poker_run.baseline`) | 9,069 | 3.25 | 0 | 0.05 s |
| `beam_search.py` | **95,359** | **7.10** | 4 | 1.37 s |
| `monte_carlo.py` | 89,745 | 7.05 | 2 | 1.41 s |
| `mcts.py` | 77,544 | 6.75 | 0 | 1.26 s |
| `tuned.py` | 84,157 | 6.60 | 0 | 0.17 s |

Every solver is about 10x the baseline, and the differences between them
are smaller than the difference the shop makes: the same beam search with
the first, naive version of the shop (buy greedily, simulate 2 rounds) scored
66,000-79,000 on the training seeds, the tuned shop ~95,000.
Most runs now die in round 8 (target 38,400) with 25,000-37,000 points: the
next step is a stronger engine, not a better round search (even a 15x wider
beam in the last round rarely reaches the target).

What the shop ends up buying (beam_search, seeds 1001-1060): tricolor and
last_call in 50 of 60 runs, then rose_tint, coal_seam, full_spread,
twin_engine and mirror; levels go almost only to Full House (with known draws
and 3 discards, Full House is available nearly every round) and Straight.
thin_deck, collector, momentum, short_hand, savings, spare_hands and
ace_dividend were never bought.

The tuner (`tune.py --fresh --minutes 9`, then 6 more minutes) took tuned.py
from 60,216 to 88,587 mean score on the training seeds 1001-1060. Look at
`tuned_weights.json`: it learned large weights for tricolor (32), last_call
(29) and twin_engine (14), to keep about $12 for interest, and the widest
beam it was allowed (6).


## Ideas to go further

* **Plan the modifier set for the whole run.** All seven shop queues are in
  the input. Choose the five modifiers you want to end with (e.g. tricolor +
  last_call + twin_engine + two flat bonuses), work out where each can be
  bought, and save money for them. Our shop is myopic; `mod_bias` and the
  purchase limits are crude patches for that.
* **Simulated annealing / local search over whole-run plans.** Represent a
  run as "what to buy in each shop" and let the round player play it out;
  mutate the plan (swap a purchase, move it to another shop) and keep
  improvements. A fast round player makes a whole run cost a few ms.
* **Plan purchases jointly with the next round's draws.** We already
  simulate the next rounds; go further and search over *bundles* of
  purchases (level FH twice + buy twin_engine) instead of one at a time.
* **Deck editing builds.** None of our solvers edits the deck much. Painting
  cards toward one suit makes flushes (and Flush House / Flush Five) easy;
  removing whole ranks makes pairs and full houses come more often and powers
  thin_deck; promote turns near-duplicates into Five of a Kind. Since the draw
  order is known you can pick exactly the cards that will show up early.
* **Better last rounds.** In the final round money is worthless: a much
  wider beam or a longer MCTS for that round alone pays off, and the last
  shop can afford an exact search with the real round solver.
* **Spend the saved time.** tuned.py takes 0.15 s; running it with several
  weight vectors (or perturbations) and keeping the best run is a cheap way
  to use the rest of the 2 s.
* **Tune the search solvers too.** WIDTH, DOLLAR, the Shop parameters and
  mod_bias were set by a handful of experiments; tune.py can be adapted to
  tune them (with fewer seeds per candidate, they are slower).

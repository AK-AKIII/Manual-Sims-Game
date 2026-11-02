# Generator

`python -m poker_run gen --seed S` writes test case `S`. The generator is `generate()` in [instance.py](../instance.py); [web/engine.js](../web/engine.js) has the same code, so the browser shows the exact same cases.

1. Seed a mulberry32 generator with `S` (32-bit). `below(n)` is `floor(next() * n)`.
2. For each round 1..R, shuffle the list `0, 1, ..., 51` with Fisher–Yates (`for i from 51 down to 1: swap(i, below(i + 1))`). That is the round's draw order.
3. For each shop 1..R-1, shuffle the 18 modifier names in the order of the statement's table the same way and keep the first Q = 12. That is the shop's offer queue.

The parameters are fixed: `R=8, H=8, P=4, D=3, M=5, Q=12`, with targets `300 × 2^(r-1)`, rounded to tens: `300 600 1200 2400 4800 9600 19200 38400`.

Public seeds are 1–10. Use other seeds for your own testing, and keep a disjoint set for tuning parameters so you don't overfit.

## Reference numbers

The baseline (`python -m poker_run.baseline`, greedy with no look-ahead) on the public seeds:

| Seed | Score | Rounds cleared |
| ---: | ---: | ---: |
| 1 | 29393 | 5 |
| 2 | 21453 | 5 |
| 3 | 1231 | 1 |
| 4 | 11559 | 4 |
| 5 | 4427 | 3 |
| 6 | 23550 | 5 |
| 7 | 2448 | 2 |
| 8 | 10302 | 4 |
| 9 | 15298 | 5 |
| 10 | 2688 | 2 |

Mean 12235, mean rounds cleared 3.6. Its outputs and the checker's verdicts are in `tests/fixtures/poker_run/public/`. On seeds 1–40 the baseline clears 3.2 rounds on average and never all 8.

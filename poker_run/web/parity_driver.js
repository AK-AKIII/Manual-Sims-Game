// Parity driver for tests/test_poker_run_web.py.
// Reads a JSON list of cases on stdin and prints a JSON list of results.
//   {"op": "gen", "seed": 7, "params": {...}}        -> input text
//   {"op": "check", "input": "...", "output": "..."}  -> [exit code, verdict]
//   {"op": "trace", "input": "...", "moves": [...]}   -> state after each move (see snapshot)
"use strict";
const PR = require("./engine.js");

function snapshot(g) {
  const hand = g.hand.slice();
  return {
    phase: g.phase, round: g.round, total: g.total, money: g.money, round_score: g.round_score,
    plays_left: g.plays_left, discards_left: g.discards_left, hand, pile: g.pile.slice(),
    cleared: g.cleared, moves: g.moves, deck_size: g.deck_size,
    rank: g.rank.slice(), suit: g.suit.slice(), in_deck: g.in_deck.slice(),
    levels: Object.assign({}, g.levels), mods: g.mods.map(([m, s]) => [m, Object.assign({}, s)]),
    offers: g.visibleOffers(), prices: g.prices(), rerolls: g.rerolls, offer_pos: g.offer_pos,
    hand_size: g.hand_size,
    eval: hand.length ? g.evaluate(hand.slice(0, Math.min(5, hand.length))) : null,
  };
}

function run(c) {
  if (c.op === "gen") return PR.formatInstance(PR.generate(c.seed, c.params || {}));
  if (c.op === "check") return PR.check(c.input, c.output);
  if (c.op === "trace") {
    const g = new PR.Game(PR.parseInstance(c.input));
    const out = [snapshot(g)];
    for (const m of c.moves) {
      try { g.apply(m); } catch (e) {
        if (!(e instanceof PR.IllegalMove)) throw e;
        out.push({ error: e.message });
        break;
      }
      out.push(snapshot(g));
    }
    return out;
  }
  throw new Error("unknown op " + c.op);
}

let data = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { data += d; });
process.stdin.on("end", () => {
  const cases = JSON.parse(data);
  const results = cases.map((c) => {
    try { return run(c); } catch (e) { return { crash: String(e && e.stack || e) }; }
  });
  process.stdout.write(JSON.stringify(results));
});

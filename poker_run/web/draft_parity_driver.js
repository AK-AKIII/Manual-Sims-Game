// Test driver for tests/test_poker_run_multiplayer.py (JS <-> Python parity of the
// multiplayer layer): drives multiplayer.js Matches with seeded random move attempts
// (legal and illegal: wrong seat, drafted buys, sells, rerolls, unaffordable buys)
// and records every attempt with its outcome and the state after it. Python then
// replays the same attempts through poker_run/multiplayer.py and must agree exactly.
//   node draft_parity_driver.js '{"cases": [{"seed": 1, "rules": "classic", "mode": "draft", "players": [...], "rng": 7}]}'
"use strict";
const PR = require("./engine.js");
const Bot = require("./bot.js");
const MP = require("./multiplayer.js");

function rngOf(seed) {
  const r = new PR.Rng(seed >>> 0);
  return { f: () => r.next(), pick: (xs) => xs[Math.floor(r.next() * xs.length)] };
}

function snapshot(m) {
  const owners = {};
  if (m.mode === "draft") for (const p of m.players) for (const [name] of p.game.mods) owners[name] = p.seat;
  const t = m.turn;
  return {
    turn: t,
    order: t && m.mode === "draft" && t.stage === "shop" ? m.shopOrder(t.round) : null,
    owners,
    totals: m.players.map((p) => p.game.total),
    moves: m.players.map((p) => p.moves.length),
  };
}

function runCase(c) {
  const inst = PR.generate(c.seed);
  const m = new MP.Match(inst, { mode: c.mode, rules: c.rules, players: c.players });
  const rnd = rngOf(c.rng || 1);
  const attempts = [];   // [seat, move, error or null, snapshot]
  const humans = m.players.filter((p) => !p.bot).map((p) => p.seat);
  const tryIt = (seat, move) => {
    const err = m.tryMove(move, seat);
    let thrown = null;
    if (!err) m.apply(move, seat);
    else {
      try { m.apply(move, seat); } catch (e) { if (!(e instanceof PR.IllegalMove)) throw e; thrown = e.message; }
    }
    attempts.push([seat, move, err, thrown, snapshot(m)]);
    return !err;
  };
  let shopTries = 0, guard = 0;
  while (!m.over && guard++ < 5000) {
    let seat;
    if (m.mode === "race") seat = rnd.pick(humans.filter((s) => !m.players[s].game.over));
    else seat = m.turn.seat;
    const g = m.players[seat].game;
    if (rnd.f() < 0.08 && humans.length > 1) {   // someone else tries to move
      const other = rnd.pick(humans.filter((s) => s !== seat));
      const og = m.players[other].game;
      tryIt(other, og.phase === "play" && og.hand.length ? "play " + og.hand[0] : "next");
      continue;
    }
    if (g.phase === "play") {
      shopTries = 0;
      const lvl = rnd.f() < 0.7 ? "hard" : "normal";
      const mv = Bot.roundMoves(g.clone(), lvl, {})[0];
      tryIt(seat, rnd.f() < 0.05 ? "end" : mv);
      continue;
    }
    // shop: a few random attempts, then the normal bot's choice, eventually "next"
    shopTries++;
    const x = rnd.f();
    const slots = g.visibleSlots().filter((s) => !s.taken);
    if (shopTries > 6) tryIt(seat, "next");
    else if (x < 0.45 && slots.length) tryIt(seat, "buy " + rnd.pick(slots).name);
    else if (x < 0.6 && g.mods.length) tryIt(seat, "sell " + rnd.pick(g.mods)[0]);
    else if (x < 0.7) tryIt(seat, "reroll");
    else if (x < 0.75) tryIt(seat, "buy " + rnd.pick(Object.keys(PR.MODIFIERS)));
    else {
      const avail = (name) => m.ownerOf(name, seat) < 0;
      const mv = Bot.shopMoves(g.clone(), "normal", {}, avail)[0];
      tryIt(seat, mv);
    }
  }
  return {
    attempts,
    log: m.log,
    standings: m.standings().map((r) => [r.seat, r.total, r.cleared, r.place]),
    orders: [...Array(inst.rounds - 1).keys()].map((r) => m.shopOrder(r)),
    over: m.over,
  };
}

const args = JSON.parse(process.argv[2] || "{}");
process.stdout.write(JSON.stringify((args.cases || []).map(runCase)));

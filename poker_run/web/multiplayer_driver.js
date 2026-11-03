// Test driver for tests/test_poker_run_multiplayer.py: runs the bots (bot.js) and the
// multiplayer layer (multiplayer.js) and prints a JSON report for Python to check
// against poker_run's own simulator, checker and baseline.
//   node multiplayer_driver.js '{"seeds": [1, 2, 3]}'
"use strict";
const PR = require("./engine.js");
const Bot = require("./bot.js");
const MP = require("./multiplayer.js");

const args = JSON.parse(process.argv[2] || "{}");
const seeds = args.seeds || [1, 2, 3];
const out = { bots: [], hotseat: [], draft: [], scenario: {}, errors: [] };

function expectIllegal(f, label) {
  try { f(); } catch (e) {
    if (e instanceof PR.IllegalMove) return e.message;
    throw e;
  }
  out.errors.push(`${label}: expected an IllegalMove`);
  return null;
}

// Drive a human seat with a bot's moves (a stand-in for a person clicking).
function humanStage(m, level, hands) {
  const t = m.turn, g = m.players[t.seat].game.clone();
  const avail = (name) => m.ownerOf(name, t.seat) < 0;
  return g.phase === "play" ? Bot.roundMoves(g, level, hands) : Bot.shopMoves(g, level, hands, avail);
}

for (const rules of ["classic", "balanced"]) {
  for (const seed of seeds) {
    const inst = PR.generate(seed);
    const text = PR.formatInstance(inst);
    // 1. bots: every level, a full run each
    for (const level of ["easy", "normal", "hard"]) {
      const moves = Bot.solve(inst, rules, level);
      out.bots.push({ rules, seed, level, moves, js: PR.check(text, PR.formatOutput(moves), rules) });
    }
    // 2. pass-and-play: 3 seats driven by different levels; turns interleave round by round
    const levels = ["hard", "easy", "normal"];
    const hot = new MP.Match(inst, { mode: "rounds", rules, players: levels.map((l, i) => ({ name: "P" + (i + 1) })) });
    const hh = levels.map(() => ({}));
    while (hot.turn) {
      const t = hot.turn;
      for (const mv of humanStage(hot, levels[t.seat], hh[t.seat])) hot.apply(mv);
    }
    out.hotseat.push({ rules, seed, levels, moves: hot.players.map((p) => p.moves),
      history: hot.history.map((h) => [h.seat, h.round, h.phase]),
      solo: levels.map((l) => Bot.solve(inst, rules, l)),
      distinct: new Set(hot.players.map((p) => p.game)).size === levels.length });
    // 3. draft: a mix of bots and stand-in humans, 2 to 4 seats
    const seats = [["normal", "bot:hard"], ["bot:easy", "hard", "bot:normal"], ["bot:normal", "bot:hard", "bot:easy", "bot:normal"]][seed % 3];
    const dr = new MP.Match(inst, { mode: "draft", rules,
      players: seats.map((s, i) => (s.startsWith("bot:") ? { name: "Bot" + i, bot: s.slice(4) } : { name: "Human" + i })) });
    const dh = seats.map(() => ({}));
    while (dr.turn) {
      const t = dr.turn;
      for (const mv of humanStage(dr, seats[t.seat], dh[t.seat])) dr.apply(mv);
    }
    const again = new MP.Match(inst, dr.toJSON());
    out.draft.push({ rules, seed, seats, moves: dr.players.map((p) => p.moves),
      history: dr.history.map((h) => [h.seat, h.move, h.round, h.phase]),
      orders: [...Array(inst.rounds - 1).keys()].map((r) => dr.shopOrder(r)),
      js: dr.players.map((p) => PR.check(text, dr.output(p.seat), rules)),
      standings: dr.standings().map((r) => [r.seat, r.total, r.place]),
      restored: JSON.stringify(again.players.map((p) => [p.moves, p.game.total])) === JSON.stringify(dr.players.map((p) => [p.moves, p.game.total])) });
  }
}

// 4. a scripted draft: Ann and Ben, same deal; Ann buys an offer, Ben can't; Ann sells it, Ben can.
{
  const inst = PR.generate(args.scenarioSeed || 4);
  const m = new MP.Match(inst, { mode: "draft", players: [{ name: "Ann" }, { name: "Ben" }] });
  const s = out.scenario;
  s.wrongSeat = expectIllegal(() => m.apply("play " + m.players[1].game.hand[0], 1), "wrong seat");
  // play round 1 with the baseline for both (Ann first, then Ben)
  for (const seat of [0, 1]) {
    if (m.turn.seat !== seat || m.turn.stage !== "play") out.errors.push(`round 1: expected seat ${seat} to play, got ${JSON.stringify(m.turn)}`);
    for (const mv of humanStage(m, "normal", {})) m.apply(mv);
  }
  s.cleared = m.players.map((p) => p.game.cleared);
  s.order0 = m.shopOrder(0);
  s.totals = m.players.map((p) => p.game.total);
  const first = m.turn.seat, second = 1 - first;
  const slots = m.shopSlots(first);
  const g = m.players[first].game;
  const pick = slots.find((x) => g.money >= g.rules.price(x.name));
  s.first = first;
  s.pick = pick ? pick.name : null;
  if (pick) {
    m.apply("buy " + pick.name);
    s.undoInTurn = m.canUndo();
    m.apply("next");
    s.undoAfterTurn = m.canUndo();
    s.turnAfter = m.turn;
    s.ownerSeenBySecond = m.shopSlots(second).filter((x) => x.name === pick.name).map((x) => x.owner);
    s.secondTry = m.tryMove("buy " + pick.name, second);
    s.secondBuy = expectIllegal(() => m.apply("buy " + pick.name), "buy a drafted modifier");
    m.apply("next");
    // Round 2: play it out, then the first buyer sells; the other player may buy it again if offered
    for (let k = 0; k < 2 && m.turn && m.turn.stage === "play"; k++) for (const mv of humanStage(m, "normal", {})) m.apply(mv);
    s.round2 = m.players.map((p) => [p.game.phase, p.game.round]);
    s.claimLater = m.ownerOf(pick.name, second);
    s.order1 = m.shopOrder(1);
    while (m.turn && m.turn.stage === "shop" && m.turn.round === 1) {
      if (m.turn.seat === first) {
        m.apply("sell " + pick.name);
        s.claimAfterSell = m.ownerOf(pick.name, second);
      }
      m.apply("next");
    }
  }
  s.json = JSON.stringify(m.toJSON()).length > 0;
}

process.stdout.write(JSON.stringify(out));

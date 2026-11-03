/* Poker Run bots: computer players for the browser game's multiplayer modes.
 *
 * Works as a browser global (window.PokerBot, after engine.js) and as a Node
 * module (require("./bot.js")). Every bot only ever makes moves that are legal
 * for an ordinary solo Game, so a bot's move list is a valid output file for
 * its seed (tests/test_poker_run_multiplayer.py checks this with both checkers).
 *
 * Levels
 *   easy      plays its best hand every time, never discards, buys at most one
 *             modifier per shop and never levels hands
 *   normal    an exact port of poker_run/baseline.py (the same moves, move for
 *             move; "baseline" is an alias)
 *   hard      the baseline plus a look-ahead when it discards: the draw order is
 *             known, so it tries several discards and keeps the one that leaves
 *             the best play in hand; in the shop it buys the modifiers it rates
 *             best first
 *
 * A bot is stateless apart from `hands`, a { code: count } object of the hand
 * types it has played (in first-played order, like Python's Counter), which the
 * caller keeps between rounds. Shop moves can be restricted by `avail(name)`
 * (the shared-shop draft uses it to hide modifiers other players own).
 */
(function (root, factory) {
  const api = factory(typeof PokerRun !== "undefined" ? PokerRun : require("./engine.js"));
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PokerBot = api;
})(typeof self !== "undefined" ? self : this, function (PR) {
  "use strict";

  const LEVELS = {
    easy: "plays its best hand, never discards, buys little",
    normal: "the reference greedy bot (poker_run/baseline.py)",
    hard: "greedy, with a look-ahead on discards and a modifier wish list",
  };
  const level = (name) => (name === "baseline" ? "normal" : Object.prototype.hasOwnProperty.call(LEVELS, name) ? name : "normal");

  // Every k-subset of items (k = 1..5), in itertools.combinations order.
  function combos(items, k, f) {
    const n = items.length, idx = [];
    for (let i = 0; i < k; i++) idx.push(i);
    if (k > n) return;
    for (;;) {
      f(idx.map((i) => items[i]));
      let i = k - 1;
      while (i >= 0 && idx[i] === i + n - k) i--;
      if (i < 0) return;
      idx[i]++;
      for (let j = i + 1; j < k; j++) idx[j] = idx[j - 1] + 1;
    }
  }

  // baseline.best_play: the highest-scoring cards to play now (ties: more cards, then first found).
  function bestPlay(game, hand) {
    hand = hand || game.hand;
    let best = null, bestKey = null;
    for (let k = 1; k <= Math.min(5, hand.length); k++) {
      combos(hand, k, (cards) => {
        const res = game.evaluate(cards);
        if (bestKey === null || res.points > bestKey[0] || (res.points === bestKey[0] && k > bestKey[1])) {
          bestKey = [res.points, k];
          best = { cards, res };
        }
      });
    }
    return best;
  }

  function count(hands, code) { hands[code] = (hands[code] || 0) + 1; }

  // The best play's points if `cards` were discarded now (the pile is known).
  function afterDiscard(game, cards) {
    const g = game.clone();
    g.apply("discard " + cards.join(" "));
    if (g.phase !== "play" || !g.hand.length) return -1;
    return bestPlay(g).res.points;
  }

  // Discards worth trying: the k lowest-chip cards outside the best play (the baseline's
  // choice is k = 5), and for each k the cards a best play in hand + the next k draws leaves out.
  function discardCandidates(game, best) {
    const chipsOf = (c) => PR.chips(game.rank[c]);
    const scored = new Set(best.res.scored);
    const junk = game.hand.filter((c) => !scored.has(c)).sort((a, b) => chipsOf(a) - chipsOf(b));
    const out = new Map();
    const add = (cards) => { if (cards.length) out.set(cards.slice().sort((a, b) => a - b).join(" "), cards); };
    for (let k = 1; k <= Math.min(5, junk.length); k++) add(junk.slice(0, k));
    for (let k = 1; k <= Math.min(5, game.hand.length, game.pile.length); k++) {
      const union = game.hand.concat(game.pile.slice(0, k));
      const plan = bestPlay(game, union);
      const keep = new Set(plan.cards);
      const rest = game.hand.filter((c) => !keep.has(c)).sort((a, b) => chipsOf(a) - chipsOf(b));
      if (rest.length >= k) add(rest.slice(0, k));
    }
    return [...out.values()];
  }

  // Play the current round to its end on `game` (mutated). Returns the moves.
  function roundMoves(game, lvl, hands) {
    hands = hands || {};
    lvl = level(lvl);
    const moves = [];
    const go = (m) => { game.apply(m); moves.push(m); };
    while (game.phase === "play") {
      const best = bestPlay(game);
      const need = game.target - game.round_score;
      if (lvl !== "easy" && best.res.points * game.plays_left < need && game.discards_left > 0) {
        if (lvl === "normal") {
          const scored = new Set(best.res.scored);
          const junk = game.hand.filter((c) => !scored.has(c)).sort((a, b) => PR.chips(game.rank[a]) - PR.chips(game.rank[b]));
          if (junk.length) { go("discard " + junk.slice(0, 5).join(" ")); continue; }
        } else {
          let pick = null, top = best.res.points;
          for (const cards of discardCandidates(game, best)) {
            const v = afterDiscard(game, cards);
            if (v > top) { top = v; pick = cards; }
          }
          if (pick) { go("discard " + pick.join(" ")); continue; }
        }
      }
      go("play " + best.cards.join(" "));
      count(hands, best.res.hand);
    }
    return moves;
  }

  // How much the hard bot likes a modifier (higher first); unknown or weak ones last.
  const PREFER = ["tricolor", "last_call", "twin_engine", "mirror", "short_hand", "rose_tint", "coal_seam",
    "high_court", "full_spread", "odd_job", "even_split", "low_road", "momentum", "collector", "thin_deck",
    "savings", "spare_hands", "ace_dividend"];
  const preferRank = (name) => { const i = PREFER.indexOf(name); return i < 0 ? PREFER.length : i; };

  function mostPlayed(hands) {
    let code = null, n = 0;
    for (const [c, k] of Object.entries(hands)) if (k > n) { code = c; n = k; }
    return code;
  }

  // Shop moves for the current shop on `game` (mutated), ending with "next".
  // avail(name): may this modifier be bought (default: always).
  function shopMoves(game, lvl, hands, avail) {
    hands = hands || {};
    lvl = level(lvl);
    avail = avail || (() => true);
    const moves = [];
    const go = (m) => { game.apply(m); moves.push(m); };
    let offers = game.visibleOffers();
    if (lvl === "hard") offers = offers.slice().sort((a, b) => preferRank(a) - preferRank(b));
    let bought = 0;
    for (const name of offers) {
      if (lvl === "easy" && bought) break;
      if (avail(name) && !game.hasMod(name) && game.mods.length < game.inst.slots && game.money >= game.rules.price(name)) {
        go("buy " + name);
        bought++;
      }
    }
    if (lvl !== "easy") {
      const code = mostPlayed(hands);
      if (code) while (game.money >= game.levelPrice(code)) go("level " + code);
    }
    go("next");
    return moves;
  }

  // A whole run for one bot. Returns the move list.
  function solve(inst, rules, level) {
    const game = new PR.Game(inst, rules);
    const hands = {};
    const moves = [];
    while (!game.over) {
      const part = game.phase === "play" ? roundMoves(game, level, hands) : shopMoves(game, level, hands);
      for (const m of part) moves.push(m);
    }
    return moves;
  }

  return { LEVELS, level, bestPlay, roundMoves, shopMoves, solve, combos };
});

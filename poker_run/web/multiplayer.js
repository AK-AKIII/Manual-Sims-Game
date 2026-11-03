/* Poker Run multiplayer: several players on one deal, as a pure layer over
 * engine.js Games (no DOM; testable in Node). See MULTIPLAYER.md for the rules.
 *
 * Works as a browser global (window.PokerMulti, after engine.js and bot.js) and
 * as a Node module (require("./multiplayer.js")).
 *
 *   const m = new PokerMulti.Match(inst, { mode: "draft", rules: "classic",
 *     players: [{ name: "Ann" }, { name: "Bot", bot: "normal" }] });
 *   m.turn             // { seat, stage: "play" | "shop", round } or null when the match is over
 *   m.apply("play 1 2") // a move for the player whose turn it is; bots then move by themselves
 *   m.players[seat]    // { name, bot, game, moves, rounds, hands }
 *
 * Modes
 *   race    every player plays the whole run on their own; bots play theirs at
 *           once (vs Bot: the page reveals the bot's rounds as the player clears theirs)
 *   rounds  pass-and-play: turns go round by round in seat order; a turn is one
 *           round and the shop after it
 *   draft   rounds, with a shared shop: all players play round r (seat order),
 *           then visit the shop one at a time, lowest total first; a modifier
 *           someone owns can't be bought by anyone else
 *
 * Each player has an independent PokerRun.Game: the layer never changes how a
 * Game plays, it only decides whose turn it is and (in a draft) refuses some
 * "buy" moves. So every player's move list is a valid output for the seed.
 */
(function (root, factory) {
  const PR = typeof PokerRun !== "undefined" ? PokerRun : require("./engine.js");
  const Bot = typeof PokerBot !== "undefined" ? PokerBot : require("./bot.js");
  const api = factory(PR, Bot);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PokerMulti = api;
})(typeof self !== "undefined" ? self : this, function (PR, Bot) {
  "use strict";

  const MODES = ["race", "rounds", "draft"];
  const MAX_PLAYERS = 4;

  class Match {
    // opts: { mode, rules, players: [{ name, bot: null | "easy" | "normal" | "hard" }], log: [[seat, move], ...] }
    constructor(inst, opts) {
      opts = opts || {};
      if (!MODES.includes(opts.mode)) throw new Error(`unknown mode ${opts.mode}: use one of ${MODES.join(", ")}`);
      const players = opts.players || [];
      if (players.length < 1 || players.length > MAX_PLAYERS) throw new Error(`1 to ${MAX_PLAYERS} players`);
      this.inst = inst;
      this.mode = opts.mode;
      this.rules = PR.getRuleset(opts.rules).name;
      this.setup = players.map((p, i) => ({ name: String(p.name || `Player ${i + 1}`), bot: p.bot ? Bot.level(p.bot) : null }));
      this._reset();
      for (const [seat, move] of opts.log || []) this._apply(seat, move);
      this.runBots();
    }

    _reset() {
      this.players = this.setup.map((p, seat) => ({
        seat, name: p.name, bot: p.bot, game: new PR.Game(this.inst, this.rules),
        moves: [],   // this player's moves: a valid output file for the seed
        rounds: [],  // per round: { round, score, cleared, total, money, plays: [{ hand, points }], discards, moves, shop }
        hands: {},   // hand code -> times played (the bots level their most played hand)
      }));
      this.history = [];   // every move in the order made: { seat, move, round, phase }
      this.turnStart = 0;  // history index where the current turn began (undo stops there)
    }

    // ---- whose turn

    // How far a player has got; the turn is the player with the smallest step.
    _step(pl) {
      const g = pl.game;
      if (g.over) return Infinity;
      const sub = g.phase === "shop" ? 1 : 0;
      return this.mode === "rounds" ? g.round : 2 * g.round + sub;
    }

    get over() { return this.players.every((p) => p.game.over); }

    get turn() {
      if (this.mode === "race") {
        const p = this.players.find((q) => !q.bot && !q.game.over);
        return p ? { seat: p.seat, stage: p.game.phase, round: p.game.round } : null;
      }
      let step = Infinity;
      for (const p of this.players) step = Math.min(step, this._step(p));
      if (step === Infinity) return null;
      let pick;
      if (this.mode === "draft" && step % 2 === 1) {
        const r = (step - 1) / 2;
        pick = this.shopOrder(r).map((s) => this.players[s]).find((p) => this._step(p) === step);
      } else pick = this.players.find((p) => this._step(p) === step);
      return { seat: pick.seat, stage: pick.game.phase, round: pick.game.round };
    }

    _turnKey() {
      const t = this.turn;
      if (!t) return "";
      // race: a "turn" is a round's play or one shop visit (undo stays inside it)
      return this.mode === "rounds" ? `${t.seat}:${t.round}` : `${t.seat}:${t.round}:${t.stage}`;
    }

    canAct(seat) {
      const pl = this.players[seat];
      if (!pl || pl.game.over) return false;
      if (this.mode === "race") return true;
      const t = this.turn;
      return !!t && t.seat === seat;
    }

    // Draft: the order of the shop visits after round r (seats). Every player who cleared
    // round r, lowest total after round r first; ties go in seat order starting with seat r mod N.
    shopOrder(r) {
      const n = this.players.length;
      const total = (p) => p.rounds.slice(0, r + 1).reduce((a, x) => a + x.score, 0);
      return this.players.filter((p) => p.game.cleared > r && r + 1 < this.inst.rounds)
        .map((p) => ({ seat: p.seat, total: total(p), tie: (((p.seat - r) % n) + n) % n }))
        .sort((a, b) => a.total - b.total || a.tie - b.tie)
        .map((x) => x.seat);
    }

    // ---- the shared shop

    // Draft: the seat of another player who owns this modifier, or -1.
    ownerOf(name, seat) {
      if (this.mode !== "draft") return -1;
      for (const p of this.players) if (p.seat !== seat && p.game.hasMod(name)) return p.seat;
      return -1;
    }

    // The shop window of a player, with who owns each offer (draft).
    shopSlots(seat) {
      const g = this.players[seat].game;
      return g.visibleSlots().map((s) => Object.assign({}, s, { owner: this.ownerOf(s.name, seat) }));
    }

    // Would this move be legal for this player now? null or the error message.
    tryMove(move, seat) {
      if (seat === undefined) seat = this.turn ? this.turn.seat : -1;
      if (!this.canAct(seat)) return this.players[seat] && this.players[seat].game.over ? "the run is already over" : "it is not this player's turn";
      const tok = PR._py.pySplit(String(move));
      if (tok[0] === "buy" && tok.length === 2 && this.players[seat].game.phase === "shop") {
        const owner = this.ownerOf(tok[1], seat);
        if (owner >= 0) return `${tok[1]} was drafted by ${this.players[owner].name}`;
      }
      return this.players[seat].game.tryMove(move);
    }

    // ---- moves

    _apply(seat, move) {
      move = String(move);
      if (!this.canAct(seat)) {
        const pl = this.players[seat];
        throw new PR.IllegalMove(!pl ? `no player ${seat}` : pl.game.over ? `${pl.name}'s run is already over` : `it is not ${pl.name}'s turn`);
      }
      const err = this.tryMove(move, seat);
      if (err) throw new PR.IllegalMove(err);
      const pl = this.players[seat], g = pl.game;
      const r = g.round, phase = g.phase, key = this._turnKey();
      const tok = PR._py.pySplit(move);
      const res = tok[0] === "play" ? g.evaluate(tok.slice(1).map(PR._py.pyInt)) : null;
      g.apply(move);
      pl.moves.push(move);
      this.history.push({ seat, move, round: r, phase });
      const rec = pl.rounds[r] || (pl.rounds[r] = { round: r, score: 0, cleared: null, total: 0, money: 0, plays: [], discards: 0, moves: [], shop: [] });
      if (phase === "play") {
        rec.moves.push(move);
        if (res) { rec.plays.push({ hand: res.hand, points: res.points }); pl.hands[res.hand] = (pl.hands[res.hand] || 0) + 1; }
        if (tok[0] === "discard") rec.discards += 1;
        rec.score = g.round_score;
        rec.total = g.total;
        if (g.phase !== "play") rec.cleared = g.cleared > r;
      } else rec.shop.push(move);
      rec.money = g.money;
      if (this._turnKey() !== key) this.turnStart = this.history.length;
    }

    // A move for a human player (default: whose turn it is); then the bots move. Throws IllegalMove.
    apply(move, seat) {
      if (seat === undefined) {
        const t = this.turn;
        if (!t) throw new PR.IllegalMove("the match is over");
        seat = t.seat;
      }
      if (this.players[seat] && this.players[seat].bot) throw new PR.IllegalMove(`${this.players[seat].name} is a bot`);
      this._apply(seat, move);
      this.runBots();
    }

    // Let the bots move: in a race each bot plays its whole run; otherwise bots
    // take their turns until it is a human's turn (or the match is over).
    runBots() {
      const stage = (p) => {
        const g = p.game.clone(), hands = Object.assign({}, p.hands);
        return g.phase === "play" ? Bot.roundMoves(g, p.bot, hands)
          : Bot.shopMoves(g, p.bot, hands, (name) => this.ownerOf(name, p.seat) < 0);
      };
      if (this.mode === "race") {
        for (const p of this.players) {
          while (p.bot && !p.game.over) for (const m of stage(p)) this._apply(p.seat, m);
        }
        return;
      }
      for (let t = this.turn; t && this.players[t.seat].bot; t = this.turn) {
        for (const m of stage(this.players[t.seat])) this._apply(t.seat, m);
      }
    }

    // Undo the last move, if it is the current turn's own (not before the turn began).
    canUndo() {
      const t = this.turn, last = this.history[this.history.length - 1];
      return !!t && !!last && last.seat === t.seat && this.history.length > this.turnStart;
    }

    undo() {
      if (!this.canUndo()) return false;
      const log = this.log.slice(0, -1);
      this._reset();
      for (const [seat, move] of log) this._apply(seat, move);
      return true;
    }

    // ---- results

    get log() { return this.history.map((h) => [h.seat, h.move]); }

    // Players best first (total, then rounds cleared), with places (ties share a place).
    standings() {
      const rows = this.players.map((p) => ({ seat: p.seat, name: p.name, bot: p.bot, total: p.game.total,
        cleared: p.game.cleared, over: p.game.over, round: p.game.round, phase: p.game.phase, money: p.game.money }));
      rows.sort((a, b) => b.total - a.total || b.cleared - a.cleared || a.seat - b.seat);
      rows.forEach((row, i) => {
        const prev = rows[i - 1];
        row.place = prev && prev.total === row.total && prev.cleared === row.cleared ? prev.place : i + 1;
      });
      return rows;
    }

    winners() {
      if (!this.over) return [];
      return this.standings().filter((r) => r.place === 1).map((r) => r.seat);
    }

    output(seat) { return PR.formatOutput(this.players[seat].moves); }

    toJSON() { return { mode: this.mode, rules: this.rules, players: this.setup, log: this.log }; }
  }

  return { MODES, MAX_PLAYERS, Match };
});

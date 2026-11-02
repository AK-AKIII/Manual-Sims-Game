/* Poker Run engine: an exact JavaScript port of poker_run/rules.py,
 * instance.py, sim.py and checker.py. The Python files are the source of
 * truth; tests/test_poker_run_web.py checks that this file agrees with them
 * (generated inputs, checker verdicts, per-move state).
 *
 * Works as a browser global (window.PokerRun) and as a Node module.
 *
 * A few Python behaviours are copied on purpose so that verdicts (including
 * error strings) match exactly: str.split()/strip()/splitlines() whitespace
 * sets, int() parsing (signs, underscores, Unicode digits, the 4300-digit
 * limit), repr() of strings, and list slicing with negative bounds.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PokerRun = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------------------------------------------------------------- Python helpers

  // Characters for which Python's str.isspace() is true.
  const PY_WS = "\t\n\u000b\f\r\u001c\u001d\u001e\u001f \u0085       " +
    "          　";
  const WS_SET = new Set(PY_WS);
  const WS_RUN = new RegExp("[" + PY_WS.replace(/[\\\]^-]/g, "\\$&") + "]+");

  function pySplit(s) {
    return s.split(WS_RUN).filter((t) => t.length > 0);
  }

  function pyStrip(s) {
    let a = 0, b = s.length;
    while (a < b && WS_SET.has(s[a])) a++;
    while (b > a && WS_SET.has(s[b - 1])) b--;
    return s.slice(a, b);
  }

  // str.splitlines(): \n \r \r\n \v \f \x1c \x1d \x1e \x85
  function pySplitlines(s) {
    const out = [];
    let cur = "";
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      if (ch === "\r") {
        out.push(cur); cur = "";
        if (s[i + 1] === "\n") i++;
      } else if ("\n\u000b\f\u001c\u001d\u001e\u0085  ".includes(ch)) {
        out.push(cur); cur = "";
      } else cur += ch;
    }
    if (cur.length) out.push(cur);
    return out;
  }

  const NON_PRINTABLE = /[\p{C}\p{Z}]/u;

  // repr() of a str.
  function pyRepr(s) {
    const q = s.includes("'") && !s.includes('"') ? '"' : "'";
    let out = q;
    for (const ch of s) {
      const cp = ch.codePointAt(0);
      if (ch === q || ch === "\\") out += "\\" + ch;
      else if (ch === "\t") out += "\\t";
      else if (ch === "\n") out += "\\n";
      else if (ch === "\r") out += "\\r";
      else if (cp < 0x20 || cp === 0x7f) out += "\\x" + cp.toString(16).padStart(2, "0");
      else if (cp < 0x7f) out += ch;
      else if (ch !== " " && NON_PRINTABLE.test(ch)) {
        if (cp < 0x100) out += "\\x" + cp.toString(16).padStart(2, "0");
        else if (cp < 0x10000) out += "\\u" + cp.toString(16).padStart(4, "0");
        else out += "\\U" + cp.toString(16).padStart(8, "0");
      } else out += ch;
    }
    return out + q;
  }

  function pyListRepr(items) {
    return "[" + items.map(pyRepr).join(", ") + "]";
  }

  const ND = /^\p{Nd}$/u;
  function digitValue(ch) {
    // Unicode decimal digits come in contiguous runs of 10 starting at zero.
    if (ch >= "0" && ch <= "9") return ch.charCodeAt(0) - 48;
    if (!ND.test(ch)) return -1;
    let cp = ch.codePointAt(0), start = cp;
    while (start > 0 && ND.test(String.fromCodePoint(start - 1))) start--;
    return (cp - start) % 10;
  }

  class PyValueError extends Error {}

  // int(s) for a token without surrounding whitespace. Returns a Number when
  // it is a safe integer, otherwise a BigInt (prints exactly like Python).
  function pyInt(s) {
    const chars = Array.from(s);
    const bad = () => new PyValueError("invalid literal for int() with base 10: " +
      Array.from(pyRepr(s)).slice(0, 200).join(""));
    let i = 0, neg = false;
    if (chars[0] === "+" || chars[0] === "-") { neg = chars[0] === "-"; i = 1; }
    const digits = [];
    let prevUnderscore = true; // disallow a leading underscore
    for (; i < chars.length; i++) {
      const ch = chars[i];
      if (ch === "_") {
        if (prevUnderscore) throw bad();
        prevUnderscore = true;
        continue;
      }
      const d = digitValue(ch);
      if (d < 0) throw bad();
      digits.push(d);
      prevUnderscore = false;
    }
    if (!digits.length || prevUnderscore) throw bad();
    if (digits.length > 4300) {
      throw new PyValueError("Exceeds the limit (4300 digits) for integer string conversion: value has " +
        digits.length + " digits; use sys.set_int_max_str_digits() to increase the limit");
    }
    let big = BigInt(digits.join("").replace(/^0+(?=.)/, ""));
    if (neg) big = -big;
    return toNum(big);
  }

  function toNum(big) {
    if (big >= BigInt(Number.MIN_SAFE_INTEGER) && big <= BigInt(Number.MAX_SAFE_INTEGER)) return Number(big);
    return big;
  }

  // a[start:end] with Python's slice semantics (step 1).
  function pySlice(a, start, end) {
    const n = a.length;
    const norm = (x) => (x < 0 ? Math.max(0, n + x) : Math.min(n, x));
    const s = norm(start), e = norm(end);
    return s < e ? a.slice(s, e) : [];
  }

  // Python round() of a float: half to even.
  function roundHalfEven(x) {
    const f = Math.floor(x), d = x - f;
    if (d > 0.5) return f + 1;
    if (d < 0.5) return f;
    return f % 2 === 0 ? f : f + 1;
  }

  // ---------------------------------------------------------------- rules.py

  const RANKS = "23456789TJQKA";
  const SUITS = "SHDC";
  const N_CARDS = 52;

  function startFace(id) { return [id % 13, Math.floor(id / 13)]; }
  function faceStr(rank, suit) { return RANKS[rank] + SUITS[suit]; }
  function chips(rank) { return rank <= 8 ? rank + 2 : rank === 12 ? 11 : 10; }

  const HANDS = [
    ["HC", "High Card", 5, 1, 10, 1],
    ["PR", "Pair", 10, 2, 15, 1],
    ["TP", "Two Pair", 20, 2, 20, 1],
    ["TK", "Three of a Kind", 25, 3, 20, 2],
    ["ST", "Straight", 30, 4, 30, 2],
    ["FL", "Flush", 35, 4, 25, 2],
    ["FH", "Full House", 45, 4, 25, 2],
    ["FK", "Four of a Kind", 60, 6, 30, 3],
    ["SF", "Straight Flush", 80, 8, 40, 3],
    ["FV", "Five of a Kind", 100, 10, 35, 3],
    ["HF", "Flush House", 120, 11, 40, 3],
    ["F5", "Flush Five", 150, 12, 50, 3],
  ];
  const HAND_CODES = HANDS.map((h) => h[0]);
  const HAND_NAMES = Object.fromEntries(HANDS.map((h) => [h[0], h[1]]));

  function handValue(code, level) {
    const h = HANDS[HAND_CODES.indexOf(code)];
    return [h[2] + (level - 1) * h[4], h[3] + (level - 1) * h[5]];
  }

  const MODIFIERS = {
    rose_tint: [5, "+3 mult for each scored heart or diamond"],
    coal_seam: [5, "+3 mult for each scored spade or club"],
    low_road: [4, "+12 base for each scored 2, 3, 4, 5 or 6"],
    high_court: [5, "+3 mult for each scored J, Q or K"],
    ace_dividend: [4, "+$2 for each scored ace"],
    twin_engine: [6, "+4 mult for each rank that appears 2+ times among the scored cards"],
    short_hand: [7, "x2 mult when you play 3 or fewer cards"],
    full_spread: [5, "+40 base when you play exactly 5 cards"],
    last_call: [7, "x3 mult on the last play of a round"],
    momentum: [6, "+2 mult for each play in a row before this one of the same hand type"],
    collector: [6, "gains +10 base for good each time you play a Straight or Straight Flush; adds its base to every play"],
    thin_deck: [6, "+1 mult for each card your deck is below 52"],
    mirror: [6, "scored cards add their chips twice"],
    tricolor: [6, "x2 mult when the scored cards have 3 or more suits"],
    savings: [5, "interest cap +$5"],
    spare_hands: [4, "+$2 for each unused discard when you clear a round"],
    odd_job: [5, "+2 mult for each scored A, 3, 5, 7 or 9"],
    even_split: [5, "+15 base for each scored 2, 4, 6, 8 or 10"],
  };
  const MODIFIER_IDS = Object.keys(MODIFIERS);
  const isModifier = (name) => Object.prototype.hasOwnProperty.call(MODIFIERS, name);

  const PRICE_REROLL = 2, PRICE_REMOVE = 3, PRICE_PAINT = 3, PRICE_PROMOTE = 3;
  const PRICE_DISCARDS = 8, PRICE_HAND_SIZE = 10;
  const LEVEL_BASE = 2, LEVEL_STEP = 2;
  const SHOP_WINDOW = 3, MIN_DECK = 20, MAX_HAND_SIZE = 10;
  const ROUND_REWARD = 4, PLAY_BONUS = 1, INTEREST_PER = 5, INTEREST_CAP = 5;

  // ---------------------------------------------------------------- instance.py

  class Rng {
    constructor(seed) { this.a = Number(BigInt.asUintN(32, BigInt(seed))); }
    next() {
      const a = this.a = (this.a + 0x6D2B79F5) >>> 0;
      let t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    }
    below(n) { return Math.floor(this.next() * n); }
    shuffle(items) {
      for (let i = items.length - 1; i > 0; i--) {
        const j = this.below(i + 1);
        const tmp = items[i]; items[i] = items[j]; items[j] = tmp;
      }
      return items;
    }
  }

  const DEFAULT_PARAMS = { rounds: 8, hand_size: 8, plays: 4, discards: 3, slots: 5, queue: 12 };
  const TARGET_BASE = 300, TARGET_GROWTH = 2.0;

  function targetsFor(rounds) {
    const out = [];
    for (let r = 0; r < rounds; r++) {
      const x = roundHalfEven(TARGET_BASE * Math.pow(TARGET_GROWTH, r) / 10);
      out.push(toNum(BigInt(x) * 10n));
    }
    return out;
  }

  function makeInstance(rounds, hand_size, plays, discards, slots, queue, targets, orders, offers) {
    return { rounds, hand_size, plays, discards, slots, queue, targets, orders, offers };
  }

  function generate(seed, params) {
    const p = Object.assign({}, DEFAULT_PARAMS);
    for (const [k, v] of Object.entries(params || {})) if (v !== null && v !== undefined) p[k] = v;
    const rng = new Rng(seed);
    const orders = [];
    for (let r = 0; r < p.rounds; r++) orders.push(rng.shuffle([...Array(N_CARDS).keys()]));
    const offers = [];
    for (let r = 0; r < p.rounds - 1; r++) offers.push(rng.shuffle(MODIFIER_IDS.slice()).slice(0, p.queue));
    return makeInstance(p.rounds, p.hand_size, p.plays, p.discards, p.slots, p.queue,
      targetsFor(p.rounds), orders, offers);
  }

  function formatInstance(inst) {
    const lines = [[inst.rounds, inst.hand_size, inst.plays, inst.discards, inst.slots, inst.queue].join(" "),
      inst.targets.map(String).join(" ")];
    for (const o of inst.orders) lines.push(o.join(" "));
    for (const q of inst.offers) lines.push(q.join(" "));
    return lines.join("\n") + "\n";
  }

  class BadInput extends Error {}

  // Sizes from the input may be BigInts (huge numbers); the engine works with
  // Numbers for these, which only matters for absurd inputs.
  const asNum = (x) => (typeof x === "bigint" ? Number(x) : x);

  function parseInstance(text) {
    const tok = pySplit(text);
    let pos = 0;
    const take = (n) => {
      n = asNum(n);
      if (pos + n > tok.length) throw new BadInput("input ends early");
      const out = pySlice(tok, pos, pos + n);
      pos += n;
      return out;
    };
    const ints = (n) => take(n).map((t) => {
      try { return pyInt(t); } catch (e) {
        if (e instanceof PyValueError) throw new BadInput("expected an integer: " + e.message);
        throw e;
      }
    });
    const [rounds, hand_size, plays, discards, slots, queue] = ints(6).map(asNum);
    if (!(1 <= rounds && 1 <= hand_size && 1 <= plays && 0 <= discards && 0 <= slots && 0 <= queue)) {
      throw new BadInput("bad parameters");
    }
    const targets = ints(rounds);
    const orders = [];
    for (let r = 0; r < rounds; r++) orders.push(ints(N_CARDS));
    const offers = [];
    for (let r = 0; r < rounds - 1; r++) offers.push(take(queue));
    if (pos !== tok.length) throw new BadInput("extra data after the offer queues");
    orders.forEach((order, r) => {
      const s = order.slice().sort((x, y) => (x < y ? -1 : x > y ? 1 : 0));
      if (s.length !== N_CARDS || s.some((v, i) => v !== i)) {
        throw new BadInput(`draw order ${r + 1} is not a permutation of 0..51`);
      }
    });
    for (const q of offers) {
      if (q.some((m) => !isModifier(m))) throw new BadInput(`unknown modifier in ${pyListRepr(q)}`);
    }
    return makeInstance(rounds, hand_size, plays, discards, slots, queue, targets, orders, offers);
  }

  // ---------------------------------------------------------------- sim.py

  const STARTING_MONEY = 4;

  class IllegalMove extends Error {}

  function classify(ranks, suits) {
    const n = ranks.length;
    const count = new Map();
    for (const r of ranks) count.set(r, (count.get(r) || 0) + 1);
    const groups = [...count.values()].sort((a, b) => b - a);
    const flush = n === 5 && new Set(suits).size === 1;
    const distinct = [...count.keys()].sort((a, b) => a - b);
    const straight = n === 5 && distinct.length === 5 &&
      (distinct[4] - distinct[0] === 4 || distinct.join(",") === "0,1,2,3,12");
    const fullHouse = groups[0] === 3 && groups[1] === 2;
    const top = groups[0];
    let code;
    if (top === 5 && flush) code = "F5";
    else if (fullHouse && flush) code = "HF";
    else if (top === 5) code = "FV";
    else if (straight && flush) code = "SF";
    else if (top === 4) code = "FK";
    else if (fullHouse) code = "FH";
    else if (flush) code = "FL";
    else if (straight) code = "ST";
    else if (top === 3) code = "TK";
    else if (groups.length > 1 && groups[1] >= 2) code = "TP";
    else if (top === 2) code = "PR";
    else code = "HC";
    let scored;
    if (n === 5 && ["F5", "HF", "FV", "SF", "FH", "FL", "ST"].includes(code)) scored = [0, 1, 2, 3, 4];
    else if (code === "HC") scored = [ranks.indexOf(Math.max(...ranks))];
    else scored = ranks.map((r, i) => i).filter((i) => count.get(ranks[i]) >= 2);
    return [code, scored];
  }

  const PLAY_MOVES = new Set(["play", "discard", "end"]);
  const SHOP_MOVES = new Set(["buy", "sell", "level", "remove", "paint", "promote", "discards", "handsize",
    "reroll", "next"]);

  class Game {
    constructor(inst) {
      if (inst === undefined) return; // used by clone()
      this.inst = inst;
      this.rank = [];
      this.suit = [];
      for (let i = 0; i < N_CARDS; i++) { const f = startFace(i); this.rank.push(f[0]); this.suit.push(f[1]); }
      this.in_deck = new Array(N_CARDS).fill(true);
      this.deck_size = N_CARDS;
      this.levels = Object.fromEntries(HAND_CODES.map((c) => [c, 1]));
      this.mods = [];
      this.money = STARTING_MONEY;
      this.total = 0;
      this.extra_discards = 0;
      this.extra_hand = 0;
      this.cleared = 0;
      this.moves = 0;
      this.offer_pos = 0; this.offers_taken = new Set(); this.rerolls = 0;
      this._startRound(0);
    }

    clone() {
      const g = new Game();
      Object.assign(g, this);
      g.rank = this.rank.slice(); g.suit = this.suit.slice(); g.in_deck = this.in_deck.slice();
      g.levels = Object.assign({}, this.levels);
      g.mods = this.mods.map(([m, s]) => [m, Object.assign({}, s)]);
      g.pile = this.pile.slice(); g.hand = this.hand.slice(); g.offers_taken = new Set(this.offers_taken);
      return g;
    }

    // ---- what you can see
    get over() { return this.phase === "over"; }
    get target() { return this.inst.targets[this.round]; }
    get hand_size() { return this.inst.hand_size + this.extra_hand; }
    face(card) { return [this.rank[card], this.suit[card]]; }
    hasMod(name) { return this.mods.some(([m]) => m === name); }
    modState(name) { const m = this.mods.find(([n]) => n === name); return m ? m[1] : null; }

    visibleOffers() {
      if (this.phase !== "shop") return [];
      const queue = this.inst.offers[this.round];
      const window = queue.slice(this.offer_pos, this.offer_pos + SHOP_WINDOW);
      return window.filter((m, k) => !this.offers_taken.has(this.offer_pos + k));
    }

    // Like visibleOffers, with the queue slot of each offer (for the UI).
    visibleSlots() {
      if (this.phase !== "shop") return [];
      const queue = this.inst.offers[this.round];
      const out = [];
      for (let k = this.offer_pos; k < Math.min(this.offer_pos + SHOP_WINDOW, queue.length); k++) {
        out.push({ slot: k, name: queue[k], taken: this.offers_taken.has(k) });
      }
      return out;
    }

    levelPrice(code) { return LEVEL_BASE + LEVEL_STEP * this.levels[code]; }

    prices() {
      return {
        reroll: PRICE_REROLL + this.rerolls,
        remove: PRICE_REMOVE,
        paint: PRICE_PAINT,
        promote: PRICE_PROMOTE,
        discards: PRICE_DISCARDS * (this.extra_discards + 1),
        handsize: PRICE_HAND_SIZE * (this.extra_hand + 1),
      };
    }

    // Score of playing these cards now, without playing them. With detail=true
    // also returns a per-modifier breakdown for the UI.
    evaluate(cards, detail) {
      const ranks = cards.map((c) => this.rank[c]);
      const suits = cards.map((c) => this.suit[c]);
      const [code, scored] = classify(ranks, suits);
      const sRanks = scored.map((i) => ranks[i]);
      const sSuits = scored.map((i) => suits[i]);
      let [base, mult] = handValue(code, this.levels[code]);
      const handBase = base, handMult = mult;
      const cardChips = sRanks.reduce((a, r) => a + chips(r), 0);
      base += cardChips;
      let times = 1, money = 0;
      const parts = [];
      const cnt = (arr, f) => arr.filter(f).length;
      for (const [name, state] of this.mods) {
        const b0 = base, m0 = mult, t0 = times, $0 = money;
        if (name === "rose_tint") mult += 3 * cnt(sSuits, (s) => s === 1 || s === 2);
        else if (name === "coal_seam") mult += 3 * cnt(sSuits, (s) => s === 0 || s === 3);
        else if (name === "low_road") base += 12 * cnt(sRanks, (r) => r <= 4);
        else if (name === "high_court") mult += 3 * cnt(sRanks, (r) => 9 <= r && r <= 11);
        else if (name === "ace_dividend") money += 2 * cnt(sRanks, (r) => r === 12);
        else if (name === "twin_engine") mult += 4 * [...new Set(sRanks)].filter((r) => cnt(sRanks, (x) => x === r) >= 2).length;
        else if (name === "short_hand") { if (cards.length <= 3) times *= 2; }
        else if (name === "full_spread") { if (cards.length === 5) base += 40; }
        else if (name === "last_call") { if (this.plays_left === 1) times *= 3; }
        else if (name === "momentum") { if (state.last === code) mult += 2 * state.streak; }
        else if (name === "collector") base += state.bonus + (code === "ST" || code === "SF" ? 10 : 0);
        else if (name === "thin_deck") mult += Math.max(0, N_CARDS - this.deck_size);
        else if (name === "mirror") base += sRanks.reduce((a, r) => a + chips(r), 0);
        else if (name === "tricolor") { if (new Set(sSuits).size >= 3) times *= 2; }
        else if (name === "odd_job") mult += 2 * cnt(sRanks, (r) => r === 1 || r === 3 || r === 5 || r === 7 || r === 12); // 3 5 7 9 A
        else if (name === "even_split") base += 15 * cnt(sRanks, (r) => r <= 8 && r % 2 === 0);
        if (detail) parts.push({ name, base: base - b0, mult: mult - m0, times: times / t0, money: money - $0 });
      }
      const res = { hand: code, scored: scored.map((i) => cards[i]), base, mult, times,
        points: base * mult * times, money };
      if (detail) Object.assign(res, { handBase, handMult, cardChips, parts });
      return res;
    }

    // ---- moves
    apply(line) {
      const tok = typeof line === "string" ? pySplit(line) : Array.from(line);
      if (!tok.length) throw new IllegalMove("empty move");
      if (this.phase === "over") throw new IllegalMove("the run is already over");
      const cmd = tok[0], args = tok.slice(1);
      const table = this.phase === "play" ? PLAY_MOVES : SHOP_MOVES;
      if (!table.has(cmd)) {
        const where = this.phase === "play" ? "during a round" : "in the shop";
        throw new IllegalMove(`'${cmd}' is not a move ${where}`);
      }
      this["_" + cmd](args);
      this.moves += 1;
    }

    _cards(args, limit) {
      let cards;
      try { cards = args.map(pyInt); } catch (e) {
        if (e instanceof PyValueError) throw new IllegalMove("card ids must be integers");
        throw e;
      }
      if (!(1 <= cards.length && cards.length <= limit)) throw new IllegalMove(`give 1 to ${limit} cards`);
      if (new Set(cards).size !== cards.length) throw new IllegalMove("card ids must be distinct");
      for (const c of cards) if (!this.hand.includes(c)) throw new IllegalMove(`card ${c} is not in your hand`);
      return cards;
    }

    _play(args) {
      if (this.plays_left <= 0) throw new IllegalMove("no plays left");
      const cards = this._cards(args, 5);
      const res = this.evaluate(cards);
      for (const [name, state] of this.mods) {
        if (name === "momentum") {
          if (state.last === res.hand) state.streak += 1;
          else { state.last = res.hand; state.streak = 1; }
        } else if (name === "collector" && (res.hand === "ST" || res.hand === "SF")) state.bonus += 10;
      }
      this.round_score += res.points;
      this.total += res.points;
      this.money += res.money;
      this.plays_left -= 1;
      this.hand = this.hand.filter((c) => !cards.includes(c));
      this._refill();
      if (this.plays_left === 0 || !this.hand.length) this._endRound();
    }

    _discard(args) {
      if (this.discards_left <= 0) throw new IllegalMove("no discards left");
      const cards = this._cards(args, 5);
      this.discards_left -= 1;
      this.hand = this.hand.filter((c) => !cards.includes(c));
      this._refill();
      if (!this.hand.length) this._endRound();
    }

    _end(args) {
      if (args.length) throw new IllegalMove("'end' takes no arguments");
      if (this.round_score < this.target) {
        throw new IllegalMove(`round score ${this.round_score} is below the target ${this.target}`);
      }
      this._endRound();
    }

    _refill() {
      while (this.hand.length < this.hand_size && this.pile.length) this.hand.push(this.pile.shift());
    }

    _startRound(r) {
      this.round = r;
      this.phase = "play";
      this.round_score = 0;
      this.plays_left = this.inst.plays;
      this.discards_left = this.inst.discards + this.extra_discards;
      this.pile = this.inst.orders[r].filter((c) => this.in_deck[c]);
      this.hand = [];
      this._refill();
    }

    _endRound() {
      if (this.round_score < this.target) { this.phase = "over"; return; }
      this.cleared += 1;
      const cap = INTEREST_CAP + (this.hasMod("savings") ? 5 : 0);
      let reward = ROUND_REWARD + PLAY_BONUS * this.plays_left + Math.min(Math.floor(this.money / INTEREST_PER), cap);
      if (this.hasMod("spare_hands")) reward += 2 * this.discards_left;
      this.money += reward;
      if (this.round + 1 === this.inst.rounds) { this.phase = "over"; return; }
      this.phase = "shop";
      this.offer_pos = 0;
      this.offers_taken = new Set();
      this.rerolls = 0;
    }

    // ---- shop moves
    _pay(cost) {
      if (this.money < cost) throw new IllegalMove(`costs $${cost}, you have $${this.money}`);
      this.money -= cost;
    }

    _one(args, what) {
      if (args.length !== 1) throw new IllegalMove(`give exactly one ${what}`);
      return args[0];
    }

    _deckCard(args, extra) {
      extra = extra || 0;
      if (args.length !== 1 + extra) throw new IllegalMove("give a card id" + (extra ? " and a suit" : ""));
      let c;
      try { c = pyInt(args[0]); } catch (e) {
        if (e instanceof PyValueError) throw new IllegalMove("card ids must be integers");
        throw e;
      }
      if (!(0 <= c && c < N_CARDS) || !this.in_deck[c]) throw new IllegalMove(`card ${c} is not in your deck`);
      return c;
    }

    _buy(args) {
      const name = this._one(args, "modifier name");
      const queue = this.inst.offers[this.round];
      let slot = null;
      for (let k = this.offer_pos; k < Math.min(this.offer_pos + SHOP_WINDOW, queue.length); k++) {
        if (queue[k] === name && !this.offers_taken.has(k)) { slot = k; break; }
      }
      if (slot === null) throw new IllegalMove(`${name} is not on offer`);
      if (this.hasMod(name)) throw new IllegalMove(`you already own ${name}`);
      if (this.mods.length >= this.inst.slots) throw new IllegalMove(`all ${this.inst.slots} modifier slots are full`);
      this._pay(MODIFIERS[name][0]);
      this.offers_taken.add(slot);
      this.mods.push([name, name === "momentum" ? { last: "", streak: 0 } : name === "collector" ? { bonus: 0 } : {}]);
    }

    _sell(args) {
      const name = this._one(args, "modifier name");
      for (let k = 0; k < this.mods.length; k++) {
        if (this.mods[k][0] === name) {
          this.mods.splice(k, 1);
          this.money += Math.floor(MODIFIERS[name][0] / 2);
          return;
        }
      }
      throw new IllegalMove(`you don't own ${name}`);
    }

    _level(args) {
      const code = this._one(args, "hand code");
      if (!HAND_CODES.includes(code)) throw new IllegalMove(`unknown hand code ${code}`);
      this._pay(this.levelPrice(code));
      this.levels[code] += 1;
    }

    _remove(args) {
      const c = this._deckCard(args);
      if (this.deck_size <= MIN_DECK) throw new IllegalMove(`your deck can't go below ${MIN_DECK} cards`);
      this._pay(PRICE_REMOVE);
      this.in_deck[c] = false;
      this.deck_size -= 1;
    }

    _paint(args) {
      const c = this._deckCard(args, 1);
      // Python: `args[1] not in SUITS` is a substring test on the string "SHDC".
      if (args[1].length !== 1 || !SUITS.includes(args[1])) throw new IllegalMove("suit must be one of S H D C");
      this._pay(PRICE_PAINT);
      this.suit[c] = SUITS.indexOf(args[1]);
    }

    _promote(args) {
      const c = this._deckCard(args);
      if (this.rank[c] === 12) throw new IllegalMove("an ace can't be promoted");
      this._pay(PRICE_PROMOTE);
      this.rank[c] += 1;
    }

    _discards(args) {
      if (args.length) throw new IllegalMove("'discards' takes no arguments");
      this._pay(this.prices().discards);
      this.extra_discards += 1;
    }

    _handsize(args) {
      if (args.length) throw new IllegalMove("'handsize' takes no arguments");
      if (this.hand_size >= MAX_HAND_SIZE) throw new IllegalMove(`hand size is already ${MAX_HAND_SIZE}`);
      this._pay(this.prices().handsize);
      this.extra_hand += 1;
    }

    _reroll(args) {
      if (args.length) throw new IllegalMove("'reroll' takes no arguments");
      if (this.offer_pos + SHOP_WINDOW >= this.inst.offers[this.round].length) {
        throw new IllegalMove("the offer queue is used up");
      }
      this._pay(this.prices().reroll);
      this.rerolls += 1;
      this.offer_pos += SHOP_WINDOW;
    }

    _next(args) {
      if (args.length) throw new IllegalMove("'next' takes no arguments");
      this._startRound(this.round + 1);
    }

    // Would this move be legal right now? Returns null or the error message.
    tryMove(line) {
      try { this.clone().apply(line); return null; } catch (e) {
        if (e instanceof IllegalMove) return e.message;
        throw e;
      }
    }
  }

  // ---------------------------------------------------------------- checker.py

  const VALID = 0, ILLEGAL = 1, UNPARSABLE = 2, BAD_INPUT = 3;

  function parseOutput(text) {
    const lines = pySplitlines(pyStrip(text)).map(pyStrip);
    if (!lines.length || !lines[0]) throw new PyValueError("empty output");
    let s;
    try { s = pyInt(lines[0]); } catch (e) {
      if (e instanceof PyValueError) throw new PyValueError("first line must be the number of moves");
      throw e;
    }
    const moves = lines.slice(1).filter((l) => l);
    // eslint-disable-next-line eqeqeq
    if (s < 0 || moves.length != s) throw new PyValueError(`first line says ${s} moves, found ${moves.length}`);
    return moves;
  }

  function verdict(game, moves) {
    return { valid: true, score: game.total, cleared: game.cleared, rounds: game.inst.rounds,
      finished: game.over, moves, money: game.money };
  }

  // Returns [exit code, verdict]. Verdict keys match checker.py exactly.
  function check(inputText, outputText) {
    let inst;
    try { inst = parseInstance(inputText); } catch (e) {
      if (e instanceof BadInput) return [BAD_INPUT, { valid: false, score: 0, error: `bad input: ${e.message}` }];
      throw e;
    }
    let moves;
    try { moves = parseOutput(outputText); } catch (e) {
      if (e instanceof PyValueError) return [UNPARSABLE, { valid: false, score: 0, error: `unparsable output: ${e.message}` }];
      throw e;
    }
    const game = new Game(inst);
    for (let k = 1; k <= moves.length; k++) {
      const move = moves[k - 1];
      try { game.apply(move); } catch (e) {
        if (e instanceof IllegalMove) {
          return [ILLEGAL, { valid: false, score: 0, error: `move ${k} (${pyRepr(move)}): ${e.message}`, move: k }];
        }
        throw e;
      }
    }
    return [VALID, verdict(game, moves.length)];
  }

  function formatOutput(moves) {
    return moves.length + "\n" + moves.map((m) => m + "\n").join("");
  }

  return {
    // rules
    RANKS, SUITS, N_CARDS, HANDS, HAND_CODES, HAND_NAMES, MODIFIERS, MODIFIER_IDS,
    PRICE_REROLL, PRICE_REMOVE, PRICE_PAINT, PRICE_PROMOTE, PRICE_DISCARDS, PRICE_HAND_SIZE,
    LEVEL_BASE, LEVEL_STEP, SHOP_WINDOW, MIN_DECK, MAX_HAND_SIZE,
    ROUND_REWARD, PLAY_BONUS, INTEREST_PER, INTEREST_CAP, STARTING_MONEY,
    startFace, faceStr, chips, handValue,
    // instance
    Rng, DEFAULT_PARAMS, targetsFor, generate, formatInstance, parseInstance, BadInput,
    // sim
    classify, Game, IllegalMove,
    // checker
    VALID, ILLEGAL, UNPARSABLE, BAD_INPUT, parseOutput, check, verdict, formatOutput,
    // python helpers (exported for tests)
    _py: { pySplit, pyStrip, pySplitlines, pyRepr, pyInt, pySlice, roundHalfEven, PyValueError },
  };
});

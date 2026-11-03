/* Landing page: a live mini-demo of round 1 built on the real engine
 * (/play/engine.js, window.PokerRun), plus contest status and facts. */
(function () {
  "use strict";
  const PR = window.PokerRun;
  const { esc, fmtInt, fmtScore, countUp, toast } = window.UI;
  const SND = () => window.Sound || { select() {}, deselect() {}, score() {}, success() {}, error() {}, tick() {} };
  const GLYPH = ["♠︎", "♥︎", "♦︎", "♣︎"];
  const SUIT_NAME = ["spades", "hearts", "diamonds", "clubs"];
  const rankLabel = (r) => (r === 8 ? "10" : PR.RANKS[r]);
  const HINT7 = [26, 16, 17, 45]; // the statement's example discard on seed 7

  let seed = 7, g = null, sel = [], busy = false, prevHand = [];
  const body = document.getElementById("demo-body");

  function cardHTML(id, o) {
    o = o || {};
    const r = g.rank[id], s = g.suit[id];
    const cls = ["card", s === 1 || s === 2 ? "red" : ""];
    if (o.cls) cls.push(o.cls);
    const label = rankLabel(r), glyph = GLYPH[s];
    const tag = o.tag ? `<span class="tag">${esc(o.tag)}</span>` : "";
    const inner = `<span class="tl">${label}<i>${glyph}</i></span><span class="cid">${id}</span>` +
      `<span class="mid">${glyph}</span><span class="br">${label}<i>${glyph}</i></span>${tag}`;
    const aria = `${label} of ${SUIT_NAME[s]}, card ${id}` + (o.aria ? ", " + o.aria : "");
    if (o.button) {
      return `<button type="button" data-quiet class="${cls.join(" ")}" data-id="${id}" aria-pressed="${o.pressed ? "true" : "false"}" aria-label="${esc(aria)}"${o.style ? ` style="${o.style}"` : ""}>${inner}</button>`;
    }
    return `<div class="${cls.join(" ")}" role="img" aria-label="${esc(aria)}">${inner}</div>`;
  }

  function sortedHand() {
    return g.hand.slice().sort((a, b) => g.rank[b] - g.rank[a] || g.suit[a] - g.suit[b] || a - b);
  }

  function skeleton() {
    body.innerHTML = `
      <div class="demo-stats">
        <div class="stat"><div class="k">Target</div><div class="v" id="d-target">300</div></div>
        <div class="stat"><div class="k">Score</div><div class="v gold" id="d-score" data-v="0">0</div></div>
        <div class="stat"><div class="k">Plays</div><div class="v" id="d-plays">4</div></div>
        <div class="stat"><div class="k">Discards</div><div class="v" id="d-discards">3</div></div>
      </div>
      <div class="progress" id="d-progress" role="progressbar" aria-label="Round score toward the target" aria-valuemin="0" aria-valuemax="300" aria-valuenow="0"><div class="ghost" id="d-ghost"></div><div class="bar" id="d-bar"></div></div>
      <div class="table-felt">
        <div class="hand" id="d-hand" role="group" aria-label="Your hand: select up to 5 cards"></div>
      </div>
      <div class="next-draws" id="d-next"></div>
      <div class="preview" id="d-preview" aria-live="polite"></div>
      <div class="demo-actions">
        <div class="btn-row">
          <button type="button" class="btn primary" id="d-play" data-quiet>Play</button>
          <button type="button" class="btn" id="d-discard" data-quiet>Discard</button>
          <button type="button" class="btn ghost" id="d-best" title="Select the highest-scoring play in this hand">Best play</button>
        </div>
        <button type="button" class="btn ghost" id="d-new">New seed</button>
      </div>
      <div class="demo-foot">
        <span class="kbd-help"><span class="kbd">1</span>–<span class="kbd">8</span> select · <span class="kbd">P</span> play · <span class="kbd">D</span> discard</span>
        <a id="d-full" href="/play/#seed=7">Full game <span aria-hidden="true">→</span></a>
      </div>`;
    document.getElementById("d-play").addEventListener("click", () => act("play"));
    document.getElementById("d-discard").addEventListener("click", () => act("discard"));
    document.getElementById("d-best").addEventListener("click", selectBest);
    document.getElementById("d-new").addEventListener("click", () => {
      let s;
      do { s = 1 + Math.floor(Math.random() * 9999); } while (s === seed);
      deal(s);
    });
    document.getElementById("d-hand").addEventListener("click", (ev) => {
      const b = ev.target.closest("button.card");
      if (!b || busy) return;
      toggle(Number(b.dataset.id));
    });
  }

  function deal(s) {
    seed = s;
    g = new PR.Game(PR.generate(seed));
    sel = [];
    prevHand = [];
    busy = false;
    document.getElementById("demo-seed").textContent = `· seed ${seed}, round 1`;
    document.getElementById("d-full").href = `/play/#seed=${seed}`;
    const ov = document.querySelector(".demo-result");
    if (ov) ov.remove();
    const sc = document.getElementById("d-score");
    sc.dataset.v = "0"; sc.textContent = "0";
    render(true);
  }

  function toggle(id) {
    const i = sel.indexOf(id);
    if (i >= 0) { sel.splice(i, 1); SND().deselect(); }
    else if (sel.length < 5) { sel.push(id); SND().select(); }
    else { toast("info", "At most 5 cards can be played or discarded at once.", { silent: true }); return; }
    render(false);
  }

  function selectBest() {
    const h = g.hand, n = h.length;
    let best = null;
    for (let mask = 1; mask < (1 << n); mask++) {
      let k = 0;
      for (let b = mask; b; b &= b - 1) k++;
      if (k > 5) continue;
      const cards = h.filter((_, i) => mask & (1 << i));
      const r = g.evaluate(cards);
      if (!best || r.points > best.points || (r.points === best.points && cards.length < best.cards.length)) best = { points: r.points, cards };
    }
    if (best) { sel = best.cards.slice(); SND().select(); render(false); }
  }

  function formula(r) {
    const modBase = r.base - r.handBase - r.cardChips;
    const modMult = r.mult - r.handMult;
    let s = `(${r.handBase} + ${r.cardChips} chips` + (modBase ? ` + ${modBase}` : "") + `) × (${r.handMult}` + (modMult ? ` + ${modMult}` : "") + ")";
    if (r.times !== 1) s += ` × ${r.times}`;
    return s + ` = ${fmtInt(r.points)}`;
  }

  function render(fresh) {
    const handEl = document.getElementById("d-hand");
    const hand = sortedHand();
    const res = sel.length ? g.evaluate(sel, true) : null;
    const scored = new Set(res ? res.scored : []);
    handEl.innerHTML = hand.map((id, i) => {
      const on = sel.includes(id);
      const newCard = fresh || !prevHand.includes(id);
      let cls = on ? "sel " + (scored.has(id) ? "scored" : "kicker") : "";
      if (newCard) cls += " deal";
      return cardHTML(id, {
        button: true, pressed: on, cls, tag: on ? (scored.has(id) ? "scores" : "kicker") : "",
        aria: `key ${i + 1}` + (on ? ", selected" : ""),
        style: newCard ? `animation-delay:${(fresh ? i : 0) * 45}ms` : "",
      });
    }).join("");
    prevHand = hand.slice();

    // stats
    document.getElementById("d-plays").textContent = g.plays_left;
    document.getElementById("d-discards").textContent = g.discards_left;
    document.getElementById("d-target").textContent = fmtInt(g.target);
    const target = Number(g.target);
    const pct = Math.min(100, (100 * g.round_score) / target);
    document.getElementById("d-bar").style.width = pct + "%";
    const prog = document.getElementById("d-progress");
    prog.classList.toggle("done", g.round_score >= target);
    prog.setAttribute("aria-valuenow", String(g.round_score));
    prog.setAttribute("aria-valuemax", String(target));
    const ghost = document.getElementById("d-ghost");
    if (res && g.phase === "play") {
      const gp = Math.min(100 - pct, (100 * res.points) / target);
      ghost.style.left = pct + "%"; ghost.style.width = gp + "%";
    } else { ghost.style.width = "0"; }

    // next draws: the input tells you
    const nxt = g.pile.slice(0, 5);
    document.getElementById("d-next").innerHTML = nxt.length
      ? `<span>Next draws</span><span class="cards">${nxt.map((id) => cardHTML(id, { cls: "mini" })).join("")}</span>`
      : "";

    // preview
    const pv = document.getElementById("d-preview");
    if (!res) {
      const tip = seed === 7 && g.plays_left === 4 && g.discards_left === 3
        ? ` Hint: discard <button type="button" class="btn sm" id="d-hint">26 16 17 45</button>`
        : "";
      pv.innerHTML = `<div class="hand-name" style="font-size:15px;font-weight:500">Select 1 to 5 cards</div><div class="sub">See the hand and its score.${tip}</div>`;
      const hb = document.getElementById("d-hint");
      if (hb) hb.addEventListener("click", () => { sel = HINT7.filter((c) => g.hand.includes(c)); render(false); });
    } else {
      const kick = sel.length - res.scored.length;
      const after = g.round_score + res.points;
      const note = after >= target ? `Clears the target.` : `Round: ${fmtInt(after)} / ${fmtInt(target)}.`;
      pv.innerHTML = `<div class="row1"><span class="hand-name">${esc(PR.HAND_NAMES[res.hand])}</span><span class="pts">${fmtInt(res.points)} pts</span></div>
        <div class="formula">base <b>${res.base}</b> × mult <b>${res.mult}</b> · ${esc(formula(res))}</div>
        <div class="sub">${res.scored.length} scored card${res.scored.length === 1 ? "" : "s"}${kick ? `, ${kick} kicker${kick === 1 ? "" : "s"}` : ""}. ${note}</div>`;
    }

    const playErr = sel.length ? g.tryMove("play " + sel.join(" ")) : "select cards first";
    const discErr = sel.length ? g.tryMove("discard " + sel.join(" ")) : "select cards first";
    const pb = document.getElementById("d-play"), db = document.getElementById("d-discard");
    pb.disabled = !!playErr || busy; db.disabled = !!discErr || busy;
    pb.title = playErr && sel.length ? playErr : "";
    db.title = discErr && sel.length ? (discErr === "no discards left" ? "No discards left this round" : discErr) : "";
    document.getElementById("d-best").disabled = busy || g.phase !== "play";
  }

  function act(kind) {
    if (busy || !sel.length || g.phase !== "play") return;
    const line = kind + " " + sel.join(" ");
    const err = g.tryMove(line);
    if (err) { toast("bad", err); return; }
    busy = true;
    const res = kind === "play" ? g.evaluate(sel) : null;
    const handEl = document.getElementById("d-hand");
    sel.forEach((id) => {
      const el = handEl.querySelector(`[data-id="${id}"]`);
      if (el) el.classList.add("fly");
    });
    if (res) {
      const f = document.createElement("div");
      f.className = "floater";
      f.textContent = `+${fmtInt(res.points)}  ${PR.HAND_NAMES[res.hand]}`;
      handEl.parentNode.appendChild(f);
      setTimeout(() => f.remove(), 1400);
      SND().score();
    } else SND().deselect();
    const wait = window.UI.reduced() ? 60 : 360;
    setTimeout(() => {
      g.apply(line);
      sel = [];
      busy = false;
      countUp(document.getElementById("d-score"), g.round_score, { dur: 700 });
      render(false);
      if (g.phase !== "play") setTimeout(showResult, window.UI.reduced() ? 50 : 650);
    }, wait);
  }

  function showResult() {
    const cleared = g.cleared > 0;
    const ov = document.createElement("div");
    ov.className = "demo-result";
    ov.setAttribute("role", "dialog");
    ov.setAttribute("aria-modal", "false");
    ov.setAttribute("aria-labelledby", "d-res-title");
    ov.innerHTML = `<div>
      <h3 id="d-res-title">${cleared ? "Round 1 cleared" : "Run over"}</h3>
      <div class="big">${fmtInt(g.round_score)}</div>
      <p>${cleared
        ? `Target ${fmtInt(g.target)} beaten. Seven rounds to go.`
        : `Target was ${fmtInt(g.target)}. A better line exists.`}</p>
      <div class="btn-row">
        <a class="btn primary" href="/play/#seed=${seed}">Play seed ${seed} <span class="arrow" aria-hidden="true">→</span></a>
        <button type="button" class="btn" data-r="again">Replay</button>
        <button type="button" class="btn ghost" data-r="new">New seed</button>
      </div></div>`;
    document.getElementById("demo").appendChild(ov);
    if (cleared) SND().success(); else SND().error();
    ov.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-r]");
      if (!b) return;
      if (b.dataset.r === "again") deal(seed);
      else document.getElementById("d-new").click();
      const first = document.querySelector("#d-hand button.card");
      if (first) first.focus();
    });
    const a = ov.querySelector("a.btn");
    if (a) a.focus({ preventScroll: true });
  }

  function keys(ev) {
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const demo = document.getElementById("demo");
    if (!demo.contains(document.activeElement) && !demo.matches(":hover")) return;
    if (document.querySelector(".demo-result")) return;
    const k = ev.key.toLowerCase();
    if (/^[1-9]$/.test(k)) {
      const hand = sortedHand(), i = Number(k) - 1;
      if (i < hand.length && !busy) { toggle(hand[i]); ev.preventDefault(); }
    } else if (k === "p") { act("play"); ev.preventDefault(); }
    else if (k === "d") { act("discard"); ev.preventDefault(); }
    else if (k === "escape") { sel = []; render(false); }
  }

  // ---------- contest status + facts
  async function contest() {
    const line = document.getElementById("contest-status");
    let c;
    try { c = await window.API.contest(); } catch (e) { line.innerHTML = ""; return; }
    if (c.name && c.name !== "Poker Run") document.getElementById("contest-name").textContent = c.name;
    const f = (k) => document.querySelector(`[data-fact="${k}"]`);
    const M = window.Motion;
    const later = (fn) => (M ? M.whenVisible(document.getElementById("facts"), fn) : fn());
    if (c.hidden_count !== undefined) later(() => countUp(f("hidden_count"), c.hidden_count, { dur: 900 }));
    if (c.time_limit !== undefined) f("time_limit").textContent = c.time_limit + " s";
    if (c.full_per_hour !== undefined) later(() => countUp(f("full_per_hour"), c.full_per_hour, { dur: 900 }));
    if (Array.isArray(c.public_seeds) && c.public_seeds.length) {
      const ps = c.public_seeds.map(Number);
      const contiguous = ps.every((x, i) => i === 0 || x === ps[i - 1] + 1);
      f("public_seeds").textContent = contiguous && ps.length > 2 ? `${ps[0]}–${ps[ps.length - 1]}` : ps.join(", ");
    }
    line.innerHTML = `<a class="pill" href="/contest/" style="text-decoration:none"><span class="dot-live" aria-hidden="true"></span><span class="t"></span></a>`;
    const dotEl = line.querySelector(".dot-live"), tEl = line.querySelector(".t");
    const tick = () => {
      const p = window.UI.contestPhase(c);
      const dot = p.phase === "open" || p.phase === "always" ? "" : p.phase === "upcoming" ? " warn" : " off";
      const txt = p.phase === "open" ? `Open · ends in ${window.UI.shortDuration(p.until - Date.now() - p.offset)}`
        : p.phase === "upcoming" ? `Starts in ${window.UI.shortDuration(p.until - Date.now() - p.offset)}`
        : p.phase === "ended" ? "Contest over · final standings" : "Open · no deadline";
      dotEl.className = "dot-live" + dot;
      if (tEl.textContent !== txt) tEl.textContent = txt;
    };
    tick();
    setInterval(tick, 1000);
  }


  document.addEventListener("DOMContentLoaded", () => {
    if (PR && body) {
      skeleton();
      deal(7);
      document.addEventListener("keydown", keys);
    }
    contest();
  });
})();

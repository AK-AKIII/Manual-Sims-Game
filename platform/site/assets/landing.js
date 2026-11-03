/* Landing page: a live mini-demo of round 1 built on the real engine
 * (/play/engine.js, window.PokerRun), plus contest status and facts. */
(function () {
  "use strict";
  const PR = window.PokerRun;
  const { esc, fmtInt, countUp, toast } = window.UI;
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
        <div class="stat"><div class="k">Round score</div><div class="v gold" id="d-score" data-v="0">0</div></div>
        <div class="stat"><div class="k">Plays left</div><div class="v" id="d-plays">4</div></div>
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
        <button type="button" class="btn ghost" id="d-new">Deal another seed</button>
      </div>
      <div class="demo-foot">
        <span class="kbd-help">Keys: <span class="kbd">1</span>–<span class="kbd">8</span> select · <span class="kbd">P</span> play · <span class="kbd">D</span> discard · <span class="kbd">Esc</span> clear</span>
        <a class="btn sm" id="d-full" href="/play/#seed=7">Open the full game <span class="arrow" aria-hidden="true">→</span></a>
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
      ? `<span>Next draws <span class="opt-inline">(the input lists all ${g.pile.length})</span>:</span><span class="cards">${nxt.map((id) => cardHTML(id, { cls: "mini" })).join("")}</span>`
      : "";

    // preview
    const pv = document.getElementById("d-preview");
    if (!res) {
      const tip = seed === 7 && g.plays_left === 4 && g.discards_left === 3
        ? ` Tip from the statement: discard <button type="button" class="btn sm" id="d-hint">26 16 17 45</button> to draw into a straight.`
        : "";
      pv.innerHTML = `<div class="hand-name" style="font-size:16px">Select 1 to 5 cards</div><div class="sub">Clicking shows the hand they make and exactly what it scores.${tip}</div>`;
      const hb = document.getElementById("d-hint");
      if (hb) hb.addEventListener("click", () => { sel = HINT7.filter((c) => g.hand.includes(c)); render(false); });
    } else {
      const kick = sel.length - res.scored.length;
      const after = g.round_score + res.points;
      const note = after >= target ? `Reaches the target (${fmtInt(after)} / ${fmtInt(target)}).` : `Round would be at ${fmtInt(after)} / ${fmtInt(target)}.`;
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
        ? `You beat the target of ${fmtInt(g.target)}${g.plays_left ? ` with ${g.plays_left} play${g.plays_left === 1 ? "" : "s"} to spare` : ""}. In the full game you'd now have $${g.money} to spend in the shop, and seven more rounds to go, each target twice the last.`
        : `${fmtInt(g.round_score)} of ${fmtInt(g.target)}: the round ended below its target. Every future card is in the input, so a better line exists. Try again?`}</p>
      <div class="btn-row">
        <a class="btn primary" href="/play/#seed=${seed}">Play seed ${seed} in full <span class="arrow" aria-hidden="true">→</span></a>
        <button type="button" class="btn" data-r="again">Replay this hand</button>
        <button type="button" class="btn ghost" data-r="new">Deal another seed</button>
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
    line.innerHTML = `<a class="pill" href="/contest/" style="text-decoration:none;color:inherit"><span class="dot-live" aria-hidden="true"></span><span class="t"></span></a>`;
    const dotEl = line.querySelector(".dot-live"), tEl = line.querySelector(".t");
    const tick = () => {
      const p = window.UI.contestPhase(c);
      const dot = p.phase === "open" || p.phase === "always" ? "" : p.phase === "upcoming" ? " warn" : " off";
      const txt = p.phase === "open" ? `Contest open · ends in ${window.UI.shortDuration(p.until - Date.now() - p.offset)}`
        : p.phase === "upcoming" ? `Contest starts in ${window.UI.shortDuration(p.until - Date.now() - p.offset)}`
        : p.phase === "ended" ? "Contest over · leaderboard is final" : "Contest open · submissions welcome";
      dotEl.className = "dot-live" + dot;
      if (tEl.textContent !== txt) tEl.textContent = txt;
    };
    tick();
    setInterval(tick, 1000);
  }


  // ------------------------------------------------------------------ scroll story (desktop)
  function storyCard(game, id, extra) {
    const r = game.rank[id], su = game.suit[id];
    const glyph = GLYPH[su];
    return `<div class="card${su === 1 || su === 2 ? " red" : ""}${extra ? " " + extra : ""}"><span class="tl">${rankLabel(r)}<i>${glyph}</i></span><span class="cid">${id}</span><span class="mid">${glyph}</span><span class="br">${rankLabel(r)}<i>${glyph}</i></span></div>`;
  }

  function buildStory() {
    const vis = document.getElementById("story-visual");
    if (!vis || !PR) return;
    const inst = PR.generate(7);
    const g = new PR.Game(inst);
    // 1: the input and the first deal
    const order = inst.orders[0];
    const text = PR.formatInstance(inst).split("\n");
    vis.querySelector(".s1").innerHTML =
      `<div class="infile"><b>${esc(text[0])}</b>\n${esc(text[1])}\n${esc(order.slice(0, 14).join(" "))} …\n${esc(inst.orders[1].slice(0, 14).join(" "))} …\n…\n${esc(inst.offers[0].slice(0, 4).join(" "))} …</div>` +
      `<div class="row-cards">${order.slice(0, 8).map((id, i) => storyCard(g, id).replace('class="card', `style="--i:${i}" class="card`)).join("")}</div>` +
      `<div class="cap">Seed 7: your first 8 cards are the first 8 ids of round 1's draw order.</div>`;
    // 2: discard into a straight, then play it
    g.apply("discard 26 16 17 45");
    const play = [33, 8, 9, 23, 50];
    const res = g.evaluate(play, true);
    const hand = g.hand.slice().sort((a, b) => g.rank[a] - g.rank[b] || a - b);
    vis.querySelector(".s2").innerHTML =
      `<div class="burst">+${fmtInt(res.points)}<small>${esc(PR.HAND_NAMES[res.hand])}</small></div>` +
      `<div class="row-cards">${hand.map((id, i) => storyCard(g, id, play.includes(id) ? "hit" : "miss").replace('class="card', `style="--i:${play.includes(id) ? i : 0}" class="card`)).join("")}</div>` +
      `<div class="formula">base ${res.base} × mult ${res.mult} = ${fmtInt(res.points)}</div>` +
      `<div class="cap">After <b>discard 26 16 17 45</b> the hand holds a straight: target ${fmtInt(g.target)} beaten in one play.</div>`;
    // 3: the first shop's window
    const offers = inst.offers[0].slice(0, 3);
    vis.querySelector(".s3").innerHTML =
      `<div class="wallet"><span class="pill">Money <b class="gold">$8</b></span><span class="pill">Slots <b>0 / 5</b></span></div>` +
      `<div class="offers3">${offers.map((m, i) => `<div class="offer" style="--i:${i}"><b>${esc(m.replace(/_/g, " "))}</b><span>${esc(PR.MODIFIERS[m][1])}</span><em>$${PR.MODIFIERS[m][0]}</em></div>`).join("")}</div>` +
      `<div class="cap">Seed 7's first shop shows three offers at a time. <b>reroll</b> moves the window along the queue.</div>`;
    // 4: doubling targets
    const t = inst.targets.map(Number), max = t[t.length - 1];
    vis.querySelector(".s4").innerHTML =
      `<div class="bars">${t.map((v, i) => `<div class="bar"><span>${v >= 1000 ? v / 1000 + "k" : v}</span><i style="--i:${i};height:${Math.max(1.5, (100 * v) / max)}%"></i></div>`).join("")}</div>` +
      `<div class="ticks">${t.map((_, i) => `<span>R${i + 1}</span>`).join("")}</div>` +
      `<div class="cap">Targets double each round. Round 8 alone needs <b>${fmtInt(max)}</b> points.</div>`;
    // 5: a submission through the judge
    vis.querySelector(".s5").innerHTML =
      `<div class="subcard"><div class="top"><b>#42 <span class="kind full">full</span></b><span class="badge queued" id="s5-badge">queued</span></div>` +
      `<div class="judge"><div class="top"><span id="s5-label">Waiting in the queue</span><span class="muted num" id="s5-pct"></span></div><div class="pbar"><i id="s5-bar"></i></div></div>` +
      `<div class="vgrid" id="s5-grid">${"<span class=\"v pending\"></span>".repeat(50)}</div></div>` +
      `<div class="score" id="s5-score"></div><div class="cap">Live in the contest hub: status, judging test N of 50, per-test verdicts.</div>`;

    let s5timer = null;
    const runJudge = () => {
      clearInterval(s5timer);
      const badge = document.getElementById("s5-badge"), label = document.getElementById("s5-label"), bar = document.getElementById("s5-bar");
      const cells = document.querySelectorAll("#s5-grid .v"), pct = document.getElementById("s5-pct"), score = document.getElementById("s5-score");
      cells.forEach((c) => { c.className = "v pending"; });
      badge.className = "badge queued"; badge.textContent = "queued"; label.textContent = "Waiting in the queue";
      bar.style.transform = "scaleX(0)"; pct.textContent = ""; score.textContent = ""; score.dataset.v = "0";
      let k = -8;
      s5timer = setInterval(() => {
        k++;
        if (k <= 0) return;
        if (k <= 50) {
          badge.className = "badge running"; badge.textContent = "judging";
          label.textContent = `Judging test ${k} of 50`;
          bar.style.transform = `scaleX(${k / 50})`; pct.textContent = Math.round((100 * k) / 50) + "%";
          cells[k - 1].className = "v " + (k === 17 || k === 38 ? "TLE" : "AC");
        } else {
          clearInterval(s5timer);
          badge.className = "badge done"; badge.textContent = "done"; label.textContent = "48 / 50 valid";
          countUp(score, 31482907, { dur: 900 });
        }
      }, 55);
    };

    const steps = Array.from(document.querySelectorAll(".story-step"));
    let active = 1;
    const setStep = (n) => {
      if (n === active) return;
      active = n;
      vis.dataset.step = String(n);
      steps.forEach((st) => st.classList.toggle("active", Number(st.dataset.step) === n));
      if (n === 5) runJudge(); else clearInterval(s5timer);
    };
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries) => {
        for (const e of entries) if (e.isIntersecting) setStep(Number(e.target.dataset.step));
      }, { rootMargin: "-45% 0px -45% 0px" });
      steps.forEach((st) => io.observe(st));
    }
    // scenes start in their "before" state; let the first one play when it scrolls in
    vis.dataset.step = "0";
    active = 0;
    const M = window.Motion;
    if (M) M.whenVisible(vis, () => { if (!active) setStep(1); }, { margin: "0px 0px -25% 0px" });
    else setStep(1);
  }

  // ------------------------------------------------------------------ hero parallax (desktop, motion allowed)
  function initParallax() {
    const M = window.Motion;
    const hero = document.getElementById("hero");
    if (!M || !hero || M.reduced()) return;
    const demo = document.getElementById("demo");
    const glow = hero.querySelector(".hero-glow");
    const deco = Array.from(hero.querySelectorAll(".card.deco")).map((el) => ({ el, speed: Number(el.dataset.speed), rot: Number(el.dataset.rot) }));
    let wide = window.innerWidth > 1000, heroH = hero.offsetHeight;
    window.addEventListener("resize", () => { wide = window.innerWidth > 1000; heroH = hero.offsetHeight; }, { passive: true });
    let lastY = -1;
    M.onScrollFrame((y) => {
      if (y > heroH + 200 && lastY > heroH + 200) return;
      lastY = y;
      const yy = Math.min(y, heroH + 200);
      if (glow) glow.style.transform = `translate3d(0, ${(yy * 0.35).toFixed(1)}px, 0) scale(${(1 + yy / 3000).toFixed(3)})`;
      for (const d of deco) d.el.style.transform = `translate3d(0, ${(yy * d.speed).toFixed(1)}px, 0) rotate(${(d.rot + yy * d.speed * 0.04).toFixed(2)}deg)`;
      if (demo && wide && !document.documentElement.classList.contains("intro-on")) {
        demo.style.transform = yy > 0 ? `translate3d(0, ${(yy * -0.07).toFixed(1)}px, 0) rotate(${(yy * -0.0025).toFixed(3)}deg)` : "";
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (PR && body) {
      skeleton();
      deal(7);
      document.addEventListener("keydown", keys);
    }
    buildStory();
    initParallax();
    contest();
  });
})();

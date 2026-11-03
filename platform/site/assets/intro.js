/* Opening intro for the landing page (~1.8 s, once per session, skippable).
 * One DPR-aware 2D canvas; the 52 card faces are pre-rendered once.
 * Beats: quick riffle -> spiral -> fan behind the wordmark -> 8 cards fly into
 * the demo hand while the flat cover fades open. Click, key, wheel or touch skips. */
(function () {
  "use strict";
  const root = document.documentElement;
  const KEY = "pr-intro";
  const T = { riffle: 0.35, burst: 0.8, form: 1.3, end: 1.8 };
  const RF = T.riffle / 0.6; // the riffle was drawn for 0.6 s; scale its beats
  const GLYPH = ["♠︎", "♥︎", "♦︎", "♣︎"];
  const RANK = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"];
  const SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif';
  const WORD = "Poker Run";
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeBack = (t) => { const c1 = 1.1, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); };
  const seg = (t, a, b) => clamp((t - a) / (b - a), 0, 1);

  function rnd(seed) { // mulberry32: same layout every time
    let a = seed >>> 0;
    return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }

  function face(id, w, h, s) {
    const c = document.createElement("canvas");
    c.width = Math.ceil(w * s); c.height = Math.ceil(h * s);
    const g = c.getContext("2d");
    g.scale(s, s);
    const r = w * 0.08, rank = id % 13, suit = Math.floor(id / 13), red = suit === 1 || suit === 2;
    g.beginPath(); g.roundRect ? g.roundRect(0.5, 0.5, w - 1, h - 1, r) : g.rect(0.5, 0.5, w - 1, h - 1);
    g.fillStyle = "#fbfaf7"; g.fill(); g.lineWidth = 1; g.strokeStyle = "#dedbd3"; g.stroke();
    g.fillStyle = red ? "#c8282f" : "#17171a"; g.textAlign = "center"; g.textBaseline = "alphabetic";
    const corner = (x, y) => {
      g.font = `600 ${w * 0.25}px ${SANS}`; g.fillText(RANK[rank], x, y);
      g.font = `400 ${w * 0.2}px ${SANS}`; g.fillText(GLYPH[suit], x, y + w * 0.22);
    };
    corner(w * 0.17, w * 0.27);
    g.save(); g.translate(w, h); g.rotate(Math.PI); corner(w * 0.17, w * 0.27); g.restore();
    g.font = `400 ${w * 0.56}px ${SANS}`; g.textBaseline = "middle"; g.fillText(GLYPH[suit], w / 2, h / 2 + w * 0.04);
    return c;
  }

  function play() {
    const reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) { root.classList.remove("intro-on"); return; }
    try { sessionStorage.setItem(KEY, "1"); } catch (e) { /* private mode: plays again next time */ }
    clearTimeout(window.__introFailsafe);
    root.classList.add("intro-on");
    window.scrollTo(0, 0);

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cv = document.createElement("canvas");
    cv.className = "intro-canvas";
    cv.setAttribute("aria-hidden", "true");
    const ctx = cv.getContext("2d", { alpha: true });
    const skipBtn = document.createElement("button");
    skipBtn.type = "button"; skipBtn.className = "intro-skip"; skipBtn.textContent = "Skip intro";
    document.body.append(cv, skipBtn);
    root.classList.add("intro-live");

    let W = 0, H = 0, cx = 0, cy = 0, R0 = 0, CW = 62, CH = 87, wh = 0;
    const size = () => {
      wh = 0;
      W = window.innerWidth; H = window.innerHeight; cx = W / 2; cy = H / 2;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      R0 = Math.hypot(W, H) * 0.5;
      CW = clamp(Math.min(W, H) * 0.085, 40, 66); CH = CW * 1.4;
    };
    size();
    const sprites = [];
    for (let i = 0; i < 52; i++) sprites.push(face(i, CW, CH, dpr * 1.6));

    // per-card layout
    const rand = rnd(7);
    const handIds = Array.from(document.querySelectorAll("#d-hand .card")).map((el) => Number(el.dataset.id));
    const order = [];
    for (let i = 0; i < 52; i++) order.push(i);
    // deck order: the demo's 8 cards on top so they surface on landing
    order.sort((a, b) => (handIds.includes(b) ? 1 : 0) - (handIds.includes(a) ? 1 : 0) || rand() - 0.5);
    const cards = order.map((id, i) => ({
      id, i, z: 0.55 + rand() * 0.95, spin: (rand() - 0.5) * 6.5, ang: i * 2.39996 + 0.6,
      rad: 0.32 + 0.68 * Math.sqrt((i + 0.5) / 52) * (0.85 + rand() * 0.3),
      hand: handIds.indexOf(id),
    }));
    const fanSpan = Math.PI * 0.82;
    const fanAt = (i, n) => {
      const a = -fanSpan / 2 + (fanSpan * i) / (n - 1);
      const fr = clamp(wordHalf() + 100, 150, Math.min(W * 0.47, H * 0.6));
      const px = cx, py = cy + fr * 0.55;
      return { x: px + Math.sin(a) * fr, y: py - Math.cos(a) * fr, rot: a, s: 0.9 };
    };
    const FS = () => clamp(W * 0.09, 40, 112);
    function wordHalf() {
      if (!wh) { ctx.font = `600 ${FS()}px ${SANS}`; wh = ctx.measureText(WORD).width / 2; }
      return wh;
    }
    let targets = null; // demo card rects, read once at beat 4
    let t0 = performance.now(), last = t0, done = false, raf = 0;

    function cardState(c, t) {
      // beat 1: riffle
      const stackY = (k) => cy - k * 0.35;
      if (t < T.riffle) {
        const side = c.i % 2 ? 1 : -1;
        const split = easeInOut(seg(t, 0, 0.18 * RF));
        const back = easeInOut(seg(t, (0.18 + (c.i / 52) * 0.26) * RF, (0.26 + (c.i / 52) * 0.26) * RF));
        const half = Math.floor(c.i / 2);
        let x = cx + side * 74 * split * (1 - back), y = lerp(stackY(c.i), stackY(half) - 10 * split, split * (1 - back));
        let rot = side * 0.16 * split * (1 - back) + (back > 0 && back < 1 ? side * 0.06 * Math.sin(back * Math.PI) : 0);
        const cut = seg(t, 0.48 * RF, 0.6 * RF);
        if (c.i > 30 && cut > 0) x += Math.sin(cut * Math.PI) * 58;
        return { x, y, rot, s: 1.15, a: 1 };
      }
      const burst = (tt) => {
        const k = easeOut(seg(tt, T.riffle, T.burst));
        const a = c.ang + (1 - k) * 1.0 + tt * 0.35;
        const r = R0 * c.rad * k;
        return { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r * 0.82, rot: c.spin * k + tt * 0.4 * Math.sign(c.spin), s: lerp(1.15, c.z, k), a: lerp(1, 0.35 + 0.5 * (c.z - 0.55) / 0.95, k) };
      };
      if (t < T.burst) return burst(t);
      const from = burst(T.burst);
      const fan = fanAt(c.i, 52);
      if (t < T.form) {
        const k = seg(t, T.burst, T.form - 0.12 + (c.i / 52) * 0.1);
        const e = easeBack(k);
        // swirl: interpolate in polar coordinates around the centre
        const a0 = Math.atan2(from.y - cy, from.x - cx), a1 = Math.atan2(fan.y - cy, fan.x - cx);
        let da = a1 - a0; while (da > Math.PI) da -= 2 * Math.PI; while (da < -Math.PI) da += 2 * Math.PI;
        const r0 = Math.hypot(from.x - cx, from.y - cy), r1 = Math.hypot(fan.x - cx, fan.y - cy);
        const a = a0 + (da + 2 * Math.PI * (1 - easeOut(k)) * 0.35) * Math.min(1, e);
        const r = lerp(r0, r1, e);
        return { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r, rot: lerp(from.rot, fan.rot, e), s: lerp(from.s, fan.s, e), a: lerp(from.a, 1, Math.min(1, k * 1.5)) };
      }
      // beat 4: into the hero
      const k = easeInOut(seg(t, T.form + (c.hand >= 0 ? c.hand * 0.025 : 0), T.end - 0.04));
      if (c.hand >= 0 && targets && targets[c.hand]) {
        const r = targets[c.hand];
        return { x: lerp(fan.x, r.x, k), y: lerp(fan.y, r.y, k), rot: lerp(fan.rot, 0, k), s: lerp(fan.s, r.w / CW, k), a: 1 };
      }
      const dc = targets && targets.center ? targets.center : { x: cx, y: cy };
      return { x: lerp(fan.x, dc.x + (fan.x - cx) * 0.3, k), y: lerp(fan.y, dc.y, k), rot: fan.rot * (1 - k), s: fan.s * (1 - 0.6 * k), a: clamp(1 - k * 2.2, 0, 1) };
    }

    function drawWordmark(t) {
      if (t < T.burst - 0.1) return;
      const k = seg(t, T.burst - 0.1, T.burst + 0.5), out = easeInOut(seg(t, T.form, T.end - 0.05));
      const fs = FS();
      let sc = lerp(0.55, 1, easeBack(k)), x = cx, y = cy + fs * 0.12;
      if (out > 0 && targets && targets.h1) { // fly to where the real heading sits
        const h = targets.h1;
        sc = lerp(sc, h.w / (wordHalf() * 2), out);
        x = lerp(x, h.x, out); y = lerp(y, h.y, out);
      }
      ctx.save();
      ctx.globalAlpha = clamp(k * 1.6, 0, 1) * (1 - seg(t, T.end - 0.25, T.end));
      ctx.translate(x, y); ctx.scale(sc, sc);
      ctx.font = `600 ${fs}px ${SANS}`; ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      ctx.fillStyle = "#ecebe7"; ctx.fillText(WORD, 0, 0);
      ctx.restore();
    }

    function drawCover(t) {
      ctx.save();
      ctx.fillStyle = "#121316"; ctx.fillRect(0, 0, W, H);
      const open = easeInOut(seg(t, T.form + 0.05, T.end));
      if (open > 0) { // the cover fades out while the cards land
        ctx.globalCompositeOperation = "destination-out";
        ctx.fillStyle = `rgba(0,0,0,${open.toFixed(3)})`; ctx.fillRect(0, 0, W, H);
      }
      ctx.restore();
    }

    // ?introAt=1.6 renders that moment and holds it (for screenshots / review)
    const AT = (/[?&]introAt=([\d.]+)/.exec(location.search) || [])[1];
    function frame(now) {
      if (done) return;
      let t = (now - t0) / 1000, dt = Math.min(0.05, (now - last) / 1000);
      if (AT !== undefined) {
        t = Number(AT); dt = 1 / 60;
      }
      last = now;
      if (t >= T.form && !targets) {
        const rects = Array.from(document.querySelectorAll("#d-hand .card")).map((el) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width }; });
        const d = document.getElementById("demo");
        targets = rects;
        if (d) { const r = d.getBoundingClientRect(); targets.center = { x: r.left + r.width / 2, y: r.top + r.height * 0.45 }; }
        const h1 = document.querySelector(".hero h1");
        if (h1) { const rg = document.createRange(); rg.selectNodeContents(h1); const r = rg.getBoundingClientRect(); targets.h1 = { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width }; }
        root.classList.add("intro-rise");
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      drawCover(t);
      const states = cards.map((c) => Object.assign(cardState(c, t), { c }));
      if (t > T.riffle && t < T.burst + 0.4) states.sort((a, b) => a.s - b.s); // depth order while scattered
      else if (t >= T.form) states.sort((a, b) => (a.c.hand >= 0) - (b.c.hand >= 0));
      const wordFirst = t < T.form;
      if (!wordFirst) drawWordmark(t);
      for (const s of states) {
        if (s.a <= 0.01) continue;
        ctx.save();
        ctx.globalAlpha = s.a;
        ctx.translate(s.x, s.y); ctx.rotate(s.rot); ctx.scale(s.s, s.s);
        ctx.shadowColor = "rgba(0,0,0,.35)"; ctx.shadowBlur = 4 * s.s; ctx.shadowOffsetY = 1 * s.s;
        ctx.drawImage(sprites[s.c.id], -CW / 2, -CH / 2, CW, CH);
        ctx.restore();
      }
      if (wordFirst) drawWordmark(t);
      if (AT !== undefined) { skipBtn.remove(); return; }
      if (t >= T.end) { finish(); return; }
      raf = requestAnimationFrame(frame);
    }

    function finish() {
      if (done) return;
      done = true;
      cancelAnimationFrame(raf);
      root.classList.add("intro-rise");
      root.classList.remove("intro-on", "intro-live");
      cv.style.transition = "opacity .25s ease"; cv.style.opacity = "0";
      skipBtn.remove();
      setTimeout(() => cv.remove(), 300);
      setTimeout(() => root.classList.remove("intro-rise"), 1400);
      ["pointerdown", "keydown", "wheel", "touchmove"].forEach((e) => window.removeEventListener(e, skip, true));
      window.removeEventListener("resize", size);
    }
    function skip(ev) {
      if (ev && ev.type === "pointerdown" && window.Sound) window.Sound.swish();
      if (ev && ev.type === "keydown" && ev.key === "Tab") return;
      finish();
    }
    ["pointerdown", "keydown", "wheel", "touchmove"].forEach((e) => window.addEventListener(e, skip, { capture: true, passive: true }));
    window.addEventListener("resize", size);
    raf = requestAnimationFrame((now) => { t0 = now; last = now; frame(now); });
    window.__introSkip = finish;
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("click", (ev) => {
      const a = ev.target.closest && ev.target.closest("[data-replay-intro]");
      if (!a || !document.getElementById("demo")) return;
      ev.preventDefault();
      play();
    });
    if (!root.classList.contains("intro-on")) return;
    // wait for landing.js to deal the demo hand (it runs on the same event, earlier)
    requestAnimationFrame(play);
  });
})();

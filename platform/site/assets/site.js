/* Shared UI helpers for every page: formatting, toasts, count-up numbers,
 * button ripples and ticks, the signed-in chip in the header, contest clock,
 * section reveals and the problem page's table-of-contents scrollspy. */
(function () {
  "use strict";
  const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const S = () => window.Sound || null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmtInt(n) {
    if (n === null || n === undefined || n === "") return "–";
    const x = Number(n);
    return Number.isFinite(x) ? Math.round(x).toLocaleString("en-US") : String(n);
  }
  /** Timestamps may be ISO strings or epoch seconds; returns ms or null. */
  function parseTime(t) {
    if (t === null || t === undefined || t === "") return null;
    if (typeof t === "number") return t > 1e12 ? t : t * 1000;
    let s = String(t);
    if (/^\d{4}-\d\d-\d\d[ T]\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(s)) s = s.replace(" ", "T") + "Z"; // naive = UTC
    const ms = Date.parse(s);
    return Number.isNaN(ms) ? null : ms;
  }
  function fmtDate(t) {
    const ms = parseTime(t);
    if (ms === null) return "–";
    return new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function relTime(t, nowMs) {
    const ms = parseTime(t);
    if (ms === null) return "–";
    const d = Math.round(((nowMs || Date.now()) - ms) / 1000);
    if (d < 5) return "just now";
    if (d < 60) return d + " s ago";
    if (d < 3600) return Math.floor(d / 60) + " min ago";
    if (d < 86400) return Math.floor(d / 3600) + " h ago";
    return fmtDate(t);
  }
  function fmtSecs(x) {
    if (x === null || x === undefined) return "–";
    const n = Number(x);
    return Number.isFinite(n) ? n.toFixed(2) + " s" : "–";
  }

  // ---------- toasts
  let toastBox = null;
  function toast(kind, msg, opts) {
    opts = opts || {};
    if (!toastBox) {
      toastBox = document.createElement("div");
      toastBox.className = "toasts";
      toastBox.setAttribute("role", "status");
      toastBox.setAttribute("aria-live", "polite");
      document.body.appendChild(toastBox);
    }
    const el = document.createElement("div");
    el.className = "toast " + (kind || "info");
    const icon = kind === "ok" ? "✓" : kind === "bad" ? "!" : "i";
    el.innerHTML = `<span class="ic" aria-hidden="true">${icon}</span><div class="body">${opts.html ? msg : esc(msg)}</div>` +
      `<button type="button" class="x" aria-label="Dismiss">×</button>`;
    const close = () => {
      if (el.classList.contains("out")) return;
      el.classList.add("out");
      setTimeout(() => el.remove(), 320);
    };
    el.querySelector(".x").addEventListener("click", close);
    toastBox.appendChild(el);
    while (toastBox.children.length > 4) toastBox.firstElementChild.remove();
    setTimeout(close, opts.ms || (kind === "bad" ? 6500 : 4200));
    const snd = S();
    if (snd && !opts.silent) { if (kind === "ok") snd.success(); else if (kind === "bad") snd.error(); }
    return el;
  }

  // ---------- numbers that ease into their new value
  function countUp(el, to, opts) {
    opts = opts || {};
    if (!el) return;
    const fmt = opts.fmt || fmtInt;
    const target = Number(to);
    if (!Number.isFinite(target)) { el.textContent = fmt(to); el.dataset.v = ""; return; }
    const from = el.dataset.v !== undefined && el.dataset.v !== "" ? Number(el.dataset.v) : (opts.from !== undefined ? opts.from : 0);
    el.dataset.v = String(target);
    if (el._raf) cancelAnimationFrame(el._raf);
    if (reduced() || from === target) { el.textContent = fmt(target); return; }
    const dur = opts.dur || 600, t0 = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(from + (target - from) * e);
      if (k < 1) el._raf = requestAnimationFrame(step);
      else el._raf = null;
    };
    el._raf = requestAnimationFrame(step);
  }

  // ---------- ripple + soft tick on every button
  document.addEventListener("pointerdown", (ev) => {
    const btn = ev.target.closest && ev.target.closest(".btn, .tabs button, .segmented button");
    if (!btn || btn.disabled) return;
    if (!btn.classList.contains("btn")) return; // ripple only where overflow is clipped
    const r = btn.getBoundingClientRect();
    const size = Math.max(r.width, r.height) * 2.2;
    const rp = document.createElement("span");
    rp.className = "ripple";
    rp.style.width = rp.style.height = size + "px";
    rp.style.left = (ev.clientX - r.left - size / 2) + "px";
    rp.style.top = (ev.clientY - r.top - size / 2) + "px";
    btn.appendChild(rp);
    setTimeout(() => rp.remove(), 500);
  }, { passive: true });
  document.addEventListener("click", (ev) => {
    const el = ev.target.closest && ev.target.closest("button, a.btn, [role=tab]");
    if (!el || el.disabled || el.hasAttribute("data-sound-toggle") || el.hasAttribute("data-quiet")) return;
    const snd = S();
    if (snd) snd.tick();
  });

  // ---------- header: signed-in chip
  async function initNavUser() {
    const box = document.getElementById("nav-user");
    if (!box || !window.API) return;
    const sound = box.querySelector("[data-sound-toggle]");
    const slot = document.createElement("span");
    slot.style.display = "contents";
    box.insertBefore(slot, sound);
    const render = (me) => {
      if (me) {
        slot.innerHTML = `<span class="user-chip" title="Signed in as ${esc(me.username)}"><span class="avatar" aria-hidden="true">${esc(me.username[0].toUpperCase())}</span>` +
          `<span class="name">${esc(me.username)}</span>${me.is_admin ? '<span class="badge-admin">admin</span>' : ""}</span>` +
          `<button type="button" class="btn sm ghost" data-act="logout">Sign out</button>`;
      } else {
        slot.innerHTML = `<a class="btn sm" href="/contest/#signin">Sign in</a>`;
      }
    };
    slot.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-act=logout]");
      if (!b) return;
      b.classList.add("busy"); b.disabled = true;
      try {
        await API.logout();
        toast("ok", "Signed out. See you at the table.");
        render(null);
        document.dispatchEvent(new CustomEvent("auth-change", { detail: null }));
      } catch (e) {
        toast("bad", e.message);
        b.classList.remove("busy"); b.disabled = false;
      }
    });
    document.addEventListener("auth-change", (ev) => render(ev.detail));
    try { render(await API.me()); } catch (e) { render(null); }
  }

  // ---------- contest clock
  /** Returns {phase, label, until (ms) | null, offset} from a /api/contest response. */
  function contestPhase(c, clientNow) {
    const offset = (parseTime(c.now) || Date.now()) - Date.now();
    const now = (clientNow || Date.now()) + offset;
    const start = parseTime(c.start), end = parseTime(c.end);
    if (start !== null && now < start) return { phase: "upcoming", label: "Starts in", until: start, offset };
    if (end !== null && now < end) return { phase: "open", label: "Ends in", until: end, offset };
    if (end !== null && now >= end) return { phase: "ended", label: "Contest over", until: null, offset };
    return { phase: "always", label: "Open, no deadline", until: null, offset };
  }
  function splitDuration(ms) {
    let s = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60); s -= m * 60;
    return { d, h, m, s };
  }
  function shortDuration(ms) {
    const x = splitDuration(ms);
    if (x.d) return `${x.d}d ${x.h}h`;
    if (x.h) return `${x.h}h ${x.m}m`;
    if (x.m) return `${x.m}m ${x.s}s`;
    return `${x.s}s`;
  }

  // ---------- problem page: highlight the section in view
  function initToc() {
    const links = Array.from(document.querySelectorAll(".toc nav a[href^='#']"));
    if (!links.length || !("IntersectionObserver" in window)) return;
    const map = new Map(links.map((a) => [decodeURIComponent(a.getAttribute("href").slice(1)), a]));
    const visible = new Set();
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) { if (e.isIntersecting) visible.add(e.target.id); else visible.delete(e.target.id); }
      let best = null;
      for (const id of map.keys()) {
        if (visible.has(id)) { best = id; break; }
      }
      if (best) links.forEach((a) => a.classList.toggle("active", map.get(best) === a));
    }, { rootMargin: "-70px 0px -60% 0px" });
    for (const id of map.keys()) { const h = document.getElementById(id); if (h) io.observe(h); }
  }

  window.UI = { esc, fmtInt, parseTime, fmtDate, relTime, fmtSecs, toast, countUp, contestPhase, splitDuration, shortDuration, reduced };

  document.addEventListener("DOMContentLoaded", () => {
    initNavUser();
    initToc();
    const y = document.getElementById("year");
    if (y) y.textContent = String(new Date().getFullYear());
  });
})();

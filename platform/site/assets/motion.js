/* Scroll motion shared by every page. Calm by design:
 * - transform/opacity only; one rAF-throttled, passive scroll listener; no layout
 *   reads in the scroll path except cached sizes (refreshed on resize).
 * - Content is visible by default. Only elements that are below the fold when
 *   the page loads get the pre-reveal state, so the first screen is complete
 *   without JS and nothing is hidden in a first-screen screenshot.
 * - Everything here is off under prefers-reduced-motion (except the header
 *   compaction and progress line, which don't move content).
 *
 * window.Motion: { reduced, whenVisible(el, fn, opts), observeReveals(root) } */
(function () {
  "use strict";
  const mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  const reduced = () => mq.matches;

  // ---------------------------------------------------------------- scroll state (one listener)
  const subs = [];
  let ticking = false, viewH = window.innerHeight, docH = 1;
  function measure() {
    viewH = window.innerHeight;
    docH = Math.max(1, document.documentElement.scrollHeight - viewH);
  }
  function frame() {
    ticking = false;
    const y = window.scrollY || window.pageYOffset || 0;
    for (const fn of subs) fn(y, viewH, docH);
  }
  function onScroll() {
    if (!ticking) { ticking = true; requestAnimationFrame(frame); }
  }
  function onScrollFrame(fn) { subs.push(fn); }

  // ---------------------------------------------------------------- progress line + header
  function initChrome() {
    const bar = document.createElement("div");
    bar.className = "scroll-progress";
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
    const nav = document.querySelector(".site-nav");
    let compact = null;
    onScrollFrame((y) => {
      bar.style.transform = `scaleX(${Math.min(1, y / docH)})`;
      const c = y > 40;
      if (nav && c !== compact) { compact = c; nav.classList.toggle("scrolled", c); }
    });
  }

  // ---------------------------------------------------------------- reveals
  const REVEAL_SELECTOR = [
    "[data-reveal]", ".section .eyebrow", ".section h2.big", ".section .lead", ".step", ".story-step", ".fact",
    ".video-frame", ".video-meta", ".cta-band", ".ways-more", ".split > *", ".snippet", ".dl", ".prose > *", ".auth-aside", ".auth > .panel",
    ".side-card", ".page-head", ".status-bar", ".lb-tools", ".table-scroll", ".site-footer .wrap > *",
  ].join(",");
  let revealIO = null;
  const batch = [];
  let batchRaf = 0;

  function flushBatch() {
    batchRaf = 0;
    batch.sort((a, b) => a.top - b.top || a.left - b.left);
    batch.forEach((it, i) => {
      it.el.style.transitionDelay = Math.min(i, 6) * 70 + "ms";
      it.el.classList.add("revealed");
      it.el.classList.remove("pre-reveal");
      const clear = () => { it.el.style.transitionDelay = ""; it.el.classList.remove("revealed"); };
      setTimeout(clear, 1100 + Math.min(i, 6) * 70);
    });
    batch.length = 0;
  }

  function observeReveals(root) {
    if (reduced() || !("IntersectionObserver" in window)) return;
    if (!revealIO) {
      revealIO = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          revealIO.unobserve(e.target);
          batch.push({ el: e.target, top: e.boundingClientRect.top, left: e.boundingClientRect.left });
        }
        if (batch.length && !batchRaf) batchRaf = requestAnimationFrame(flushBatch);
      }, { rootMargin: "0px 0px -6% 0px", threshold: 0.01 });
    }
    const els = Array.from((root || document).querySelectorAll(REVEAL_SELECTOR));
    const set = new Set(els);
    const fold = window.innerHeight;
    // Measure everything first, then write (no interleaved layout).
    const tops = els.map((el) => el.getBoundingClientRect().top);
    els.forEach((el, i) => {
      if (el.classList.contains("pre-reveal") || el.dataset.revealDone) return;
      // a parent that also reveals already carries the motion
      for (let p = el.parentElement; p; p = p.parentElement) if (set.has(p)) return;
      if (tops[i] < fold - 4) return; // on the first screen: leave it alone
      el.dataset.revealDone = "1";
      el.classList.add("pre-reveal");
      revealIO.observe(el);
    });
  }

  // ---------------------------------------------------------------- run once when visible
  function whenVisible(el, fn, opts) {
    if (!el) return;
    if (!("IntersectionObserver" in window)) { fn(); return; }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { io.disconnect(); fn(); return; }
      }
    }, { rootMargin: (opts && opts.margin) || "0px 0px -10% 0px", threshold: (opts && opts.threshold) || 0 });
    io.observe(el);
  }

  // ---------------------------------------------------------------- card tilt (follows the pointer)
  function initTilt() {
    if (reduced() || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    let cur = null, raf = 0, last = null;
    const apply = () => {
      raf = 0;
      if (!cur || !last) return;
      const r = cur.getBoundingClientRect();
      const px = (last.clientX - r.left) / r.width, py = (last.clientY - r.top) / r.height;
      const ry = (px - 0.5) * 12, rx = (0.5 - py) * 12; // max 6deg each way
      const lift = cur.classList.contains("sel") ? -16 : -6;
      cur.style.transform = `perspective(520px) translateY(${lift}px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg)`;
      cur.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
      cur.style.setProperty("--my", (py * 100).toFixed(1) + "%");
    };
    const release = () => {
      if (!cur) return;
      cur.classList.remove("tilting");
      cur.style.transform = "";
      cur = null;
    };
    document.addEventListener("pointermove", (ev) => {
      const c = ev.target.closest && ev.target.closest(".card:not(.mini):not(.fly)");
      if (c !== cur) { release(); if (c) { cur = c; cur.classList.add("tilting"); } }
      last = ev;
      if (cur && !raf) raf = requestAnimationFrame(apply);
    }, { passive: true });
    document.addEventListener("pointerleave", release, { passive: true });
    document.addEventListener("pointerdown", (ev) => { if (cur && ev.target.closest(".card") === cur) { cur.style.transform = ""; } }, { passive: true });
    window.addEventListener("scroll", release, { passive: true });
  }

  // ---------------------------------------------------------------- smooth in-page anchors
  function initAnchors() {
    document.addEventListener("click", (ev) => {
      const a = ev.target.closest && ev.target.closest('a[href^="#"]');
      if (!a || a.hasAttribute("data-goto")) return;
      const id = decodeURIComponent(a.getAttribute("href").slice(1));
      if (!id) return;
      const t = document.getElementById(id);
      if (!t) return;
      ev.preventDefault();
      t.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" });
      try { history.replaceState(null, "", "#" + id); } catch (e) { /* ignore */ }
      if (!t.hasAttribute("tabindex")) t.setAttribute("tabindex", "-1");
      t.focus({ preventScroll: true });
    });
  }

  window.Motion = { reduced, whenVisible, observeReveals, onScrollFrame, measure };

  document.addEventListener("DOMContentLoaded", () => {
    measure();
    initChrome();
    initAnchors();
    initTilt();
    // reveals after the page script had its first render
    requestAnimationFrame(() => observeReveals(document));
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", () => { measure(); onScroll(); }, { passive: true });
    if ("ResizeObserver" in window) new ResizeObserver(() => { measure(); onScroll(); }).observe(document.body);
    frame();
  });
})();

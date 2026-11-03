/* Tiny shared WebAudio helper: very quiet, warm tones with soft attack and release.
 * - No AudioContext until the first user gesture, so nothing ever autoplays.
 * - On by default at low volume; the header speaker toggle switches it off, and
 *   the choice is remembered in localStorage (wrapped in try/catch).
 * - When off, no audio node is ever created or played. */
(function () {
  "use strict";
  const KEY = "pr-sound";
  const VOLUME = 0.05; // master gain: deliberately low
  let enabled = true;
  try { enabled = localStorage.getItem(KEY) !== "off"; } catch (e) { /* storage blocked: keep default */ }
  let ctx = null, master = null, unlocked = false;

  function ensure() {
    if (!enabled || !unlocked) return null;
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try {
        ctx = new AC();
        master = ctx.createGain();
        master.gain.value = VOLUME;
        // a gentle low-pass keeps everything warm
        const lp = ctx.createBiquadFilter();
        lp.type = "lowpass"; lp.frequency.value = 2400;
        master.connect(lp); lp.connect(ctx.destination);
      } catch (e) { ctx = null; return null; }
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx;
  }

  function tone(freq, start, dur, opts) {
    const c = ensure();
    if (!c) return;
    opts = opts || {};
    const t0 = c.currentTime + (start || 0);
    const osc = c.createOscillator();
    const g = c.createGain();
    osc.type = opts.type || "sine";
    osc.frequency.setValueAtTime(freq, t0);
    if (opts.glide) osc.frequency.exponentialRampToValueAtTime(opts.glide, t0 + dur);
    const peak = opts.gain || 1;
    const attack = opts.attack || 0.012;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g); g.connect(master);
    osc.start(t0); osc.stop(t0 + dur + 0.05);
  }

  const Sound = {
    get enabled() { return enabled; },
    setEnabled(on) {
      enabled = !!on;
      try { localStorage.setItem(KEY, enabled ? "on" : "off"); } catch (e) { /* ignore */ }
      if (!enabled && ctx) { ctx.close().catch(() => {}); ctx = null; master = null; }
      document.querySelectorAll("[data-sound-toggle]").forEach(syncToggle);
    },
    tick() { tone(760, 0, 0.05, { type: "sine", gain: 0.2, attack: 0.004 }); },
    select() { tone(660, 0, 0.09, { type: "triangle", gain: 0.4, attack: 0.006 }); },
    deselect() { tone(520, 0, 0.09, { type: "triangle", gain: 0.3, attack: 0.006 }); },
    success() {
      tone(587.33, 0, 0.32, { type: "sine", gain: 0.8, attack: 0.02 });
      tone(880, 0.11, 0.5, { type: "sine", gain: 0.7, attack: 0.02 });
    },
    score() {
      tone(523.25, 0, 0.25, { type: "triangle", gain: 0.6 });
      tone(659.25, 0.08, 0.28, { type: "triangle", gain: 0.55 });
      tone(783.99, 0.16, 0.45, { type: "sine", gain: 0.6 });
    },
    swish() {
      const c = ensure();
      if (!c) return;
      const len = Math.floor(c.sampleRate * 0.35), buf = c.createBuffer(1, len, c.sampleRate), d = buf.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len) * (0.6 + 0.4 * Math.sin(i / 90));
      const src = c.createBufferSource(), bp = c.createBiquadFilter(), g = c.createGain(), t0 = c.currentTime;
      src.buffer = buf; bp.type = "bandpass"; bp.Q.value = 0.9;
      bp.frequency.setValueAtTime(900, t0); bp.frequency.exponentialRampToValueAtTime(2600, t0 + 0.3);
      g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.5, t0 + 0.03); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.34);
      src.connect(bp); bp.connect(g); g.connect(master); src.start(t0);
    },
    error() { tone(220, 0, 0.32, { type: "sine", gain: 0.8, attack: 0.03, glide: 196 }); },
  };

  function syncToggle(btn) {
    btn.setAttribute("aria-pressed", enabled ? "true" : "false");
    btn.setAttribute("aria-label", enabled ? "Sound on (click to mute)" : "Sound off (click to unmute)");
    btn.title = enabled ? "Sound on" : "Sound off";
    btn.innerHTML = enabled
      ? '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M4 9v6h4l5 4V5L8 9H4z"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M16 9.5a3.5 3.5 0 0 1 0 5M18.5 7a7 7 0 0 1 0 10"/></svg>'
      : '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M4 9v6h4l5 4V5L8 9H4z"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M16.5 9.5l5 5M21.5 9.5l-5 5"/></svg>';
  }

  function unlock() { unlocked = true; }
  window.addEventListener("pointerdown", unlock, { capture: true, passive: true });
  window.addEventListener("keydown", unlock, { capture: true });

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-sound-toggle]").forEach((btn) => {
      syncToggle(btn);
      btn.addEventListener("click", () => {
        unlocked = true;
        Sound.setEnabled(!enabled);
        if (enabled) Sound.select();
      });
    });
  });

  window.Sound = Sound;
})();

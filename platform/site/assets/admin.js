/* Admin panel (/admin/): overview, students (with a detail drawer and account
 * actions), submissions, settings, live leaderboard, similarity check and the
 * audit log. Talks to the Admin API in platform/API.md through window.API.
 *
 * Sections are tabs (sidebar on desktop, scrollable strip on mobile) addressed
 * by the URL hash. Only the visible section polls, and nothing polls while the
 * browser tab is hidden. Dialogs are native <dialog>s (UI.dialog and the
 * drawer), so focus stays inside, Escape closes and focus returns. */
(function () {
  "use strict";
  const UI = window.UI, API = window.API;
  if (!UI || !API) return;
  const { esc, fmtInt, fmtDate, relTime, fmtSecs, toast, countUp, parseTime } = UI;
  const $ = (id) => document.getElementById(id);
  const SECTIONS = ["overview", "students", "submissions", "settings", "leaderboard", "similarity", "activity"];
  const ACTIVE = (s) => s.status === "queued" || s.status === "running";
  const sound = () => window.Sound || null;

  const st = { me: null, tab: null, contest: null, timer: null, booted: false, usersById: new Map() };

  // ------------------------------------------------------------------ small helpers
  function seedsText(ps) {
    if (!Array.isArray(ps) || !ps.length) return "–";
    const n = ps.map(Number);
    const contiguous = n.every((x, i) => i === 0 || x === n[i - 1] + 1);
    return contiguous && n.length > 2 ? `${n[0]}–${n[n.length - 1]}` : n.join(", ");
  }
  const pad2 = (n) => String(n).padStart(2, "0");
  /** ISO (UTC) -> value for <input type=datetime-local> in the browser's timezone. */
  function toLocalInput(iso) {
    const ms = parseTime(iso);
    if (ms === null) return "";
    const d = new Date(ms);
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  }
  /** datetime-local value (local time) -> UTC ISO; null when empty, undefined when invalid. */
  function fromLocalInput(v) {
    if (!v) return null;
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return undefined;
    return d.toISOString().replace(/\.\d{3}Z$/, "Z");
  }
  function tzLabel() {
    let name = "";
    try { name = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { name = ""; }
    const off = -new Date().getTimezoneOffset();
    const sign = off >= 0 ? "+" : "−";
    const a = Math.abs(off);
    const utc = `UTC${sign}${pad2(Math.floor(a / 60))}:${pad2(a % 60)}`;
    return name ? `${name} (${utc})` : utc;
  }
  const fullDate = (t) => {
    const ms = parseTime(t);
    return ms === null ? "–" : new Date(ms).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  function busy(btn, on) {
    if (!btn) return;
    btn.disabled = on;
    btn.classList.toggle("busy", on);
    btn.setAttribute("aria-busy", on ? "true" : "false");
  }
  async function copyText(text, container) {
    try {
      if (!navigator.clipboard) throw new Error("no clipboard");
      // a pending permission prompt can leave writeText unsettled: give up after a moment
      await Promise.race([navigator.clipboard.writeText(text), new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 1500))]);
      return true;
    } catch (e) { /* fall back below */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px;opacity:0";
    (container || document.body).appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }
  function genPassword(n) {
    const abc = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    const out = [];
    const buf = new Uint8Array(1);
    while (out.length < (n || 14)) {
      crypto.getRandomValues(buf);
      if (buf[0] < 256 - (256 % abc.length)) out.push(abc[buf[0] % abc.length]);
    }
    return out.join("");
  }
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const scoreOf = (v) => (v === null || v === undefined ? null : Number(v));
  function avatar(name, cls) { return `<span class="avatar${cls ? " " + cls : ""}" aria-hidden="true">${esc(String(name || "?")[0].toUpperCase())}</span>`; }
  function statusBadge(s) {
    const label = { queued: "queued", running: "judging", done: "done", error: "error" }[s.status] || s.status;
    return `<span class="badge ${esc(s.status)}">${esc(label)}</span>`;
  }
  const kindTag = (k) => `<span class="kind ${k === "full" ? "full" : ""}">${k === "full" ? "full" : "public"}</span>`;
  function scoreCell(s) {
    if (s.status !== "done") return `<span class="muted">–</span>`;
    if (s.kind === "full" && s.relative_total !== null && s.relative_total !== undefined) {
      return `<b>${fmtInt(s.relative_total)}</b><br><span class="muted" style="font-size:12px">raw ${fmtInt(s.raw_total)}</span>`;
    }
    return `<b>${fmtInt(s.raw_total)}</b><br><span class="muted" style="font-size:12px">raw total</span>`;
  }
  function testsCell(s) {
    const tot = s.tests_total;
    if (s.status === "running" && tot) {
      const done = s.tests_done || 0;
      return `${done}/${tot}<span class="mini-progress" aria-hidden="true"><i style="width:${(100 * done) / tot}%"></i></span>`;
    }
    if (s.status === "queued") return `<span class="muted">${s.position ? "#" + s.position + " in line" : "waiting"}</span>`;
    if (tot === null || tot === undefined || s.tests_ac === null || s.tests_ac === undefined) return "–";
    return `<span style="color:${s.tests_ac === tot ? "var(--ok)" : "inherit"}">${fmtInt(s.tests_ac)}</span><span class="muted">/${fmtInt(tot)}</span>`;
  }
  const when = (t) => `<span title="${esc(fullDate(t))}">${esc(relTime(t))}</span>`;
  const emptyRow = (cols, html) => `<tr><td colspan="${cols}"><div class="empty">${html}</div></td></tr>`;

  /** A signature to find "the same" control after a re-render (first data-* attribute or id). */
  function focusSig(el) {
    if (!el || el === document.body) return null;
    if (el.id) return "#" + CSS.escape(el.id);
    for (const a of el.attributes) if (a.name.startsWith("data-")) return `[${a.name}="${CSS.escape(a.value)}"]`;
    return null;
  }
  /** Keyed <tr> patching: rows are reused by key, innerHTML only changes when the html did. */
  function patchRows(tbody, want, cache) {
    const sig = tbody.contains(document.activeElement) ? focusSig(document.activeElement) : null;
    const existing = new Map(Array.from(tbody.children).filter((tr) => tr.id).map((tr) => [tr.id, tr]));
    let prev = null;
    for (const w of want) {
      let tr = existing.get(w.key);
      if (!tr) { tr = document.createElement("tr"); tr.id = w.key; cache.delete(w.key); }
      existing.delete(w.key);
      tr.className = w.cls || "";
      if (w.attrs) for (const k in w.attrs) tr.setAttribute(k, w.attrs[k]);
      if (cache.get(w.key) !== w.html) {
        const cv = tr.querySelector(".codeview");
        const scroll = cv ? cv.scrollTop : 0;
        tr.innerHTML = w.html;
        cache.set(w.key, w.html);
        const cv2 = tr.querySelector(".codeview");
        if (cv2) cv2.scrollTop = scroll;
        if (w.after) w.after(tr);
      }
      const after = prev ? prev.nextSibling : tbody.firstChild;
      if (after !== tr) tbody.insertBefore(tr, after);
      prev = tr;
    }
    existing.forEach((tr) => { tr.remove(); cache.delete(tr.id); });
    Array.from(tbody.children).forEach((tr) => { if (!tr.id) tr.remove(); });
    if (sig && !tbody.contains(document.activeElement)) {
      const el = tbody.querySelector(sig);
      if (el) el.focus({ preventScroll: true });
    }
  }

  /** 401/403 from an admin call: the session ended or lost admin rights. */
  function authLost(e) {
    if (!e || (e.status !== 401 && e.status !== 403)) return false;
    if (e.status === 403 && !/admin/i.test(e.message || "")) return false;
    API.forgetMe();
    API.me(true).then((me) => { st.me = me; if (!me || !me.is_admin) showDenied(me); }, () => showDenied(null));
    return true;
  }

  // ------------------------------------------------------------------ live label + polling
  function setLive(state, text) {
    $("adm-live-dot").className = "dot-live" + (state === "live" ? "" : state === "warn" ? " warn" : state === "bad" ? " bad" : " off");
    $("adm-live-text").textContent = text;
  }
  function schedule() {
    clearTimeout(st.timer);
    const sec = sections[st.tab];
    const ms = sec && sec.pollMs ? sec.pollMs() : 0;
    if (!ms) { setLive("off", "Up to date · " + new Date().toLocaleTimeString()); return; }
    if (document.hidden) { setLive("off", "Paused while this tab is hidden"); return; }
    setLive("live", `Live · every ${Math.round(ms / 1000)} s`);
    st.timer = setTimeout(async () => {
      if (document.hidden) { schedule(); return; }
      try { await sec.refresh(false); } catch (e) { /* the section shows its own error */ }
      schedule();
    }, ms);
  }
  document.addEventListener("visibilitychange", () => {
    if (!st.booted || !st.tab) return;
    if (document.hidden) { clearTimeout(st.timer); setLive("off", "Paused while this tab is hidden"); return; }
    const sec = sections[st.tab];
    if (sec.pollMs && sec.pollMs()) sec.refresh(false).finally(schedule);
  });
  // countdowns in [data-until] (ms since epoch, client clock)
  setInterval(() => {
    document.querySelectorAll("[data-until]").forEach((el) => {
      const left = Number(el.dataset.until) - Date.now();
      el.textContent = left > 0 ? UI.shortDuration(left) : "now";
    });
  }, 1000);

  // ------------------------------------------------------------------ tabs
  const tablist = () => $("adm-tabs");
  function selectTab(name, opts) {
    opts = opts || {};
    if (!SECTIONS.includes(name)) name = "overview";
    const changed = st.tab !== name;
    const prev = st.tab;
    st.tab = name;
    tablist().querySelectorAll("[role=tab]").forEach((t) => {
      const on = t.id === "at-" + name;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      if (on && opts.scrollTab !== false) t.scrollIntoView({ block: "nearest", inline: "nearest", behavior: UI.reduced() ? "auto" : "smooth" });
    });
    SECTIONS.forEach((n) => $("ap-" + n).classList.toggle("hidden", n !== name));
    if (opts.hash !== false && location.hash !== "#" + name) {
      try { history.replaceState(null, "", "#" + name); } catch (e) { /* ignore */ }
    }
    if (prev && changed && sections[prev].hide) sections[prev].hide();
    if (opts.focusPanel) $("ap-" + name).focus({ preventScroll: false });
    clearTimeout(st.timer);
    Promise.resolve(sections[name].show(changed)).catch(() => {}).finally(schedule);
  }
  function wireTabs() {
    const list = tablist();
    list.addEventListener("click", (ev) => {
      const b = ev.target.closest("[role=tab]");
      if (b) selectTab(b.id.slice(3));
    });
    list.addEventListener("keydown", (ev) => {
      const tabs = Array.from(list.querySelectorAll("[role=tab]"));
      const i = tabs.indexOf(document.activeElement);
      if (i < 0) return;
      let j = null;
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") j = (i + 1) % tabs.length;
      else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      else if (ev.key === "Home") j = 0;
      else if (ev.key === "End") j = tabs.length - 1;
      if (j === null) return;
      ev.preventDefault();
      tabs[j].focus();
      selectTab(tabs[j].id.slice(3));
    });
    const mq = window.matchMedia("(max-width: 900px)");
    const orient = () => list.setAttribute("aria-orientation", mq.matches ? "horizontal" : "vertical");
    orient();
    if (mq.addEventListener) mq.addEventListener("change", orient);
    window.addEventListener("hashchange", () => {
      const h = location.hash.slice(1);
      if (st.booted && SECTIONS.includes(h) && h !== st.tab) selectTab(h, { hash: false });
    });
    document.addEventListener("click", (ev) => {
      const g = ev.target.closest("[data-goto]");
      if (g && $("adm-app").contains(g)) { ev.preventDefault(); selectTab(g.dataset.goto, { focusPanel: true }); }
    });
  }

  // ================================================================== OVERVIEW
  function niceMax(v) {
    if (v <= 4) return 4;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }
  const dayLabel = (d) => {
    const ms = Date.parse(d + "T00:00:00Z");
    return Number.isNaN(ms) ? d : new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
  };
  /** Small inline SVG bar chart (stacked when several series). */
  function barChart(days, series, label, first) {
    const W = 440, H = 150, padL = 30, padR = 6, padT = 10, padB = 22;
    const plotH = H - padT - padB, n = Math.max(1, days.length);
    const totals = days.map((d) => series.reduce((a, s) => a + (Number(d[s.key]) || 0), 0));
    const max = niceMax(Math.max(1, ...totals));
    const bw = (W - padL - padR) / n, gap = Math.max(3, bw * 0.3);
    const y = (v) => padT + plotH * (1 - v / max);
    let svg = `<svg viewBox="0 0 ${W} ${H}" class="bars${first && !UI.reduced() ? " grow" : ""}" role="img" aria-label="${esc(label)}">`;
    for (const g of [0, max / 2, max]) {
      svg += `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(g)}" y2="${y(g)}"/><text class="ax" x="${padL - 6}" y="${y(g) + 4}" text-anchor="end">${fmtInt(g)}</text>`;
    }
    days.forEach((d, i) => {
      const x = padL + i * bw + gap / 2, w = bw - gap;
      let base = 0, segs = "";
      series.forEach((s, k) => {
        const v = Number(d[s.key]) || 0;
        if (!v) return;
        const top = y(base + v), bottom = y(base);
        const hgt = Math.max(1, bottom - top - (base > 0 ? 2 : 0));
        segs += `<rect class="seg ${s.cls}" x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${w.toFixed(1)}" height="${hgt.toFixed(1)}" rx="2"/>`;
        base += v;
        void k;
      });
      const tip = `${dayLabel(d.date)}: ` + series.map((s) => `${fmtInt(d[s.key] || 0)} ${s.label}`).join(", ");
      svg += `<g class="bar-g" style="--i:${i}" data-tip="${esc(tip)}"><rect class="hit" x="${(padL + i * bw).toFixed(1)}" y="${padT}" width="${bw.toFixed(1)}" height="${plotH}"/>${segs}</g>`;
    });
    [0, Math.floor((n - 1) / 2), n - 1].forEach((i) => {
      if (!days[i]) return;
      svg += `<text class="ax" x="${(padL + i * bw + bw / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${esc(dayLabel(days[i].date))}</text>`;
    });
    svg += "</svg>";
    const legend = series.length > 1
      ? `<div class="chart-legend">${series.map((s) => `<span><i class="sw ${s.cls}" aria-hidden="true"></i>${esc(s.label)}</span>`).join("")}</div>` : "";
    const table = `<table class="sr-only"><caption>${esc(label)}</caption><thead><tr><th scope="col">Day (UTC)</th>${series.map((s) => `<th scope="col">${esc(s.label)}</th>`).join("")}</tr></thead><tbody>` +
      days.map((d) => `<tr><th scope="row">${esc(d.date)}</th>${series.map((s) => `<td>${fmtInt(d[s.key] || 0)}</td>`).join("")}</tr>`).join("") + "</tbody></table>";
    return `<div class="chart-box">${svg}<div class="chart-tip hidden" aria-hidden="true"></div></div>${legend}${table}`;
  }
  function wireChartTips(box) {
    box.addEventListener("pointermove", (ev) => {
      const g = ev.target.closest && ev.target.closest(".bar-g");
      const wrap = ev.target.closest && ev.target.closest(".chart-box");
      if (!wrap) return;
      const tip = wrap.querySelector(".chart-tip");
      wrap.querySelectorAll(".bar-g.on").forEach((x) => { if (x !== g) x.classList.remove("on"); });
      if (!g) { tip.classList.add("hidden"); return; }
      g.classList.add("on");
      tip.textContent = g.dataset.tip;
      tip.classList.remove("hidden");
      const r = wrap.getBoundingClientRect();
      const x = Math.min(Math.max(ev.clientX - r.left, 70), r.width - 70);
      tip.style.left = x + "px";
      tip.style.top = Math.max(0, ev.clientY - r.top - 40) + "px";
    });
    box.addEventListener("pointerleave", () => {
      box.querySelectorAll(".chart-tip").forEach((t) => t.classList.add("hidden"));
      box.querySelectorAll(".bar-g.on").forEach((x) => x.classList.remove("on"));
    });
  }

  const overview = {
    data: null, first: true,
    pollMs: () => 5000,
    show() { return this.refresh(true); },
    async refresh() {
      try {
        const d = await API.adminOverview();
        this.data = d;
        if (d.contest) st.contest = d.contest;
        this.render(d);
        $("ov-updated").textContent = "Updated " + new Date().toLocaleTimeString();
        this.first = false;
      } catch (e) {
        if (authLost(e)) return;
        $("ov-updated").textContent = "Couldn't refresh: " + e.message;
        setLive("bad", "Connection problem");
        if (this.first) $("ov-stats").innerHTML = `<div class="err">${esc(e.message)}</div>`;
        throw e;
      }
    },
    render(d) {
      const c = d.counts || {};
      const cards = [
        ["registered", "Registered", c.registered, `${fmtInt(c.admins)} admin${c.admins === 1 ? "" : "s"} · ${fmtInt(c.active)} active`],
        ["active_24h", "Active · 24 h", c.active_24h, "signed in or submitted"],
        ["disabled", "Disabled", c.disabled, c.disabled ? "hidden from the board" : "none"],
        ["submissions_24h", "Submissions · 24 h", c.submissions_24h, `${fmtInt(c.queued)} queued · ${fmtInt(c.running)} running`],
        ["submissions", "Submissions · total", c.submissions, `${fmtInt(c.full)} full · ${fmtInt(c.public)} public`],
        ["errors", "Errored", c.errors, c.errors ? "see Submissions" : "all clear"],
      ];
      const box = $("ov-stats");
      if (!box.querySelector("[data-k]")) {
        box.innerHTML = cards.map(([k, label]) => `<div class="stat-card" data-k="${k}"><div class="k">${esc(label)}</div><div class="v num" data-v-of="${k}">0</div><div class="s"></div></div>`).join("");
      }
      cards.forEach(([k, , v, sub]) => {
        const card = box.querySelector(`[data-k="${k}"]`);
        countUp(card.querySelector(".v"), v);
        card.querySelector(".s").textContent = sub;
        card.classList.toggle("warn", (k === "errors" || k === "disabled") && Number(v) > 0);
      });
      $("ac-students").textContent = c.registered !== undefined ? String(c.registered) : "";

      // judge
      const j = d.judge || {};
      const seen = j.worker_last_seen;
      const secs = seen ? Math.max(0, Math.round((Date.now() - (parseTime(seen) || Date.now())) / 1000)) : null;
      const alive = !!j.worker_alive;
      const seenText = seen ? (secs < 90 ? `${secs} s ago` : relTime(seen)) : "never";
      const running = Array.isArray(j.running) ? j.running : [];
      $("ov-judge-body").innerHTML =
        `<div class="judge-row"><span class="dot-live${alive ? "" : " bad"}" aria-hidden="true"></span><b>${alive ? "Worker alive" : "Worker not responding"}</b>
          <span class="muted">· last seen ${esc(seenText)}</span></div>
         ${alive ? "" : `<p class="err" style="margin:10px 0 0">No heartbeat for over 30 s. Submissions will wait in the queue until the worker is back: check <code>docker compose ps</code> / the worker logs.</p>`}
         <div class="judge-q"><div class="fact"><div class="k">Queue</div><div class="v num">${fmtInt(j.queue || 0)}</div></div>
           <div class="fact"><div class="k">Running</div><div class="v num">${fmtInt(running.length)}</div></div></div>` +
        (running.length ? running.map((r) => {
          const tot = r.tests_total || 0, done = r.tests_done || 0, pct = tot ? Math.min(100, (100 * done) / tot) : 0;
          return `<div class="judge run-item"><div class="top"><span><b>#${esc(r.id)}</b> ${esc(r.username)} ${kindTag(r.kind)}</span><span class="muted num">${done}/${tot}</span></div>
            <div class="progress" role="progressbar" aria-label="Submission ${esc(r.id)} progress" aria-valuemin="0" aria-valuemax="${tot}" aria-valuenow="${done}"><div class="bar" style="width:${pct}%"></div></div></div>`;
        }).join("") : `<p class="muted st-note" style="margin:10px 0 0">Nothing is being judged right now.</p>`);

      // contest
      this.renderContest(d.contest || st.contest);

      // verdicts
      const v = d.verdicts || {};
      const keys = ["AC", "WA", "TLE", "RE"];
      const names = { AC: "valid", WA: "wrong answer", TLE: "too slow", RE: "crashed" };
      const total = keys.reduce((a, k) => a + (Number(v[k]) || 0), 0);
      $("ov-verdicts-body").innerHTML = total
        ? `<div class="vbar" aria-hidden="true">${keys.filter((k) => v[k]).map((k) => `<span class="vseg ${k}" style="flex-grow:${Number(v[k])}" title="${k}: ${fmtInt(v[k])} (${((100 * v[k]) / total).toFixed(1)}%)"></span>`).join("")}</div>
           <ul class="vlegend">${keys.map((k) => `<li><span class="v ${k}">${k}</span> ${esc(names[k])} <b class="num">${fmtInt(v[k] || 0)}</b> <span class="muted">${total ? ((100 * (v[k] || 0)) / total).toFixed(1) : "0"}%</span></li>`).join("")}</ul>`
        : `<p class="muted" style="margin:0">No tests judged yet.</p>`;

      // charts
      const days = Array.isArray(d.daily) ? d.daily : [];
      const daysPub = days.map((x) => Object.assign({}, x, { public: Math.max(0, (x.submissions || 0) - (x.full || 0)) }));
      $("ov-chart-reg").innerHTML = barChart(days, [{ key: "registrations", label: "registrations", cls: "s1" }], "Registrations per day, last 14 days", this.first);
      $("ov-chart-sub").innerHTML = barChart(daysPub, [{ key: "full", label: "full", cls: "s1" }, { key: "public", label: "public", cls: "s2" }], "Submissions per day, last 14 days, full and public", this.first);

      // top 5
      const top = Array.isArray(d.top) ? d.top : [];
      $("ov-top").innerHTML = top.length
        ? `<ol class="top5">${top.map((r) => `<li><span class="rk">${r.rank <= 3 ? `<span class="medal m${r.rank}">${r.rank}</span>` : esc(r.rank)}</span><b class="u">${esc(r.username)}</b><span class="num">${fmtInt(r.relative_total)}</span></li>`).join("")}</ol>`
        : `<p class="muted" style="margin:0">No finished full submissions yet.</p>`;
    },
    renderContest(c) {
      if (!c) return;
      const p = UI.contestPhase(c);
      const label = { upcoming: "Starts in", open: c.open === false ? "Paused · ends in" : "Open · ends in", ended: "Contest over", always: "Open, no deadline" }[p.phase];
      const dot = p.phase === "open" || p.phase === "always" ? (c.open === false ? "warn" : "") : p.phase === "upcoming" ? "warn" : "off";
      const frozenNow = c.freeze && (parseTime(c.freeze) || 0) <= Date.now() + p.offset;
      const rows = [
        ["Start", c.start ? fullDate(c.start) : "unset"], ["End", c.end ? fullDate(c.end) : "unset"],
        ["Freeze", c.freeze ? fullDate(c.freeze) + (frozenNow ? " (frozen now)" : "") : "none"],
        ["Registration", c.invite_required ? "invite code" : "open"], ["Full runs", `${fmtInt(c.full_per_hour)} / hour`],
        ["Banner", c.announcement ? "on" : "off"],
      ];
      $("ov-contest-body").innerHTML = `<div class="judge-row"><span class="dot-live${dot ? " " + dot : ""}" aria-hidden="true"></span><b>${esc(c.name || "Contest")}</b>
          <span class="muted">· ${esc(label)}${p.until ? ` <b class="num cd" data-until="${p.until - p.offset}">${esc(UI.shortDuration(p.until - Date.now() - p.offset))}</b>` : ""}</span></div>
        <dl class="kv">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
        <button type="button" class="btn sm ghost" data-goto="settings">Edit settings <span class="arrow" aria-hidden="true">→</span></button>`;
    },
  };

  // ================================================================== STUDENTS
  const students = {
    users: [], summary: null, sort: { key: "username", dir: 1 }, rowCache: new Map(), loaded: false,
    pollMs: () => 0,
    show(changed) { return changed || !this.loaded ? this.refresh(true) : null; },
    async refresh() {
      try {
        const d = await API.adminUsers();
        this.users = Array.isArray(d.users) ? d.users : [];
        this.summary = d.summary || null;
        st.usersById = new Map(this.users.map((u) => [u.id, u]));
        this.loaded = true;
        this.render();
        this.fillDatalist();
        $("us-updated").textContent = "Updated " + new Date().toLocaleTimeString();
      } catch (e) {
        if (authLost(e)) return;
        $("us-updated").textContent = "Couldn't load: " + e.message;
        if (!this.loaded) document.querySelector("#us-table tbody").innerHTML = emptyRow(7, `<div class="err">${esc(e.message)}</div>`);
        throw e;
      }
    },
    fillDatalist() {
      $("sb-users").innerHTML = this.users.map((u) => `<option value="${esc(u.username)}">`).join("");
    },
    sortVal(u, key) {
      switch (key) {
        case "username": return u.username.toLowerCase();
        case "created": return parseTime(u.created);
        case "last_login": return parseTime(u.last_login);
        case "status": return u.status === "disabled" ? 1 : 0;
        case "subs": return (u.public_submissions || 0) + (u.full_submissions || 0);
        case "best": return scoreOf(u.best_relative_total);
        case "rank": return u.rank === null || u.rank === undefined ? null : -u.rank; // so "descending" = #1 first
        default: return null;
      }
    },
    filtered() {
      const q = $("us-q").value.trim().toLowerCase();
      const f = $("us-filter").value;
      let list = this.users.filter((u) => {
        if (q && !u.username.toLowerCase().includes(q) && !String(u.email || "").toLowerCase().includes(q)) return false;
        if (f === "active") return u.status !== "disabled";
        if (f === "disabled") return u.status === "disabled";
        if (f === "admins") return u.is_admin;
        if (f === "full") return (u.full_submissions || 0) > 0;
        if (f === "noemail") return !u.email;
        return true;
      });
      const { key, dir } = this.sort;
      list = list.slice().sort((a, b) => {
        const x = this.sortVal(a, key), y = this.sortVal(b, key);
        if (x === y) return a.id - b.id;
        if (x === null) return 1;      // empty values last in both directions
        if (y === null) return -1;
        return (x < y ? -1 : 1) * dir;
      });
      return list;
    },
    render() {
      const s = this.summary || {};
      const chips = [["Registered", s.registered], ["Active", s.active], ["Disabled", s.disabled], ["Admins", s.admins], ["With a full run", s.with_full]];
      $("us-summary").innerHTML = chips.map(([k, v]) => `<span class="chip">${esc(k)} <b class="num">${fmtInt(v)}</b></span>`).join("");
      $("ac-students").textContent = String(this.users.length);
      document.querySelectorAll("#us-table th[data-sort]").forEach((th) => {
        th.setAttribute("aria-sort", th.dataset.sort === this.sort.key ? (this.sort.dir > 0 ? "ascending" : "descending") : "none");
      });
      const tbody = document.querySelector("#us-table tbody");
      const list = this.filtered();
      if (!list.length) {
        tbody.innerHTML = emptyRow(7, this.users.length ? "No student matches these filters." : `<span class="big" aria-hidden="true">♟</span>Nobody has registered yet.`);
        this.rowCache.clear();
        return;
      }
      const me = st.me ? st.me.username.toLowerCase() : "";
      patchRows(tbody, list.map((u) => {
        const dis = u.status === "disabled";
        const badges = (u.is_admin ? `<span class="badge-admin">admin</span>` : "") + (dis ? `<span class="badge-disabled">disabled</span>` : "") +
          (u.username.toLowerCase() === me ? `<span class="badge-you">you</span>` : "");
        return {
          key: "u-" + u.id, cls: "row u-row" + (dis ? " is-disabled" : ""), attrs: { "data-uid": String(u.id) },
          html: `<td><button type="button" class="u-open" data-open="${u.id}" aria-haspopup="dialog">${avatar(u.username, "sm")}<span class="u-txt"><span class="u-name">${esc(u.username)} ${badges}</span>` +
            `<span class="u-mail${u.email ? "" : " none"}">${u.email ? esc(u.email) : "no email"}</span></span></button></td>` +
            `<td class="opt nw">${when(u.created)}</td><td class="opt nw">${u.last_login ? when(u.last_login) : `<span class="muted">never</span>`}</td>` +
            `<td>${dis ? `<span class="badge error" title="${esc(u.disabled_reason || "")}">disabled</span>` : `<span class="badge done">active</span>`}</td>` +
            `<td class="r opt nw">${fmtInt(u.public_submissions)} <span class="muted">/</span> ${fmtInt(u.full_submissions)}</td>` +
            `<td class="r">${u.best_relative_total === null || u.best_relative_total === undefined ? `<span class="muted">–</span>` : fmtInt(u.best_relative_total)}</td>` +
            `<td class="r">${u.rank ? `#${fmtInt(u.rank)}` : `<span class="muted">–</span>`}</td>`,
        };
      }), this.rowCache);
    },
    wire() {
      $("us-q").addEventListener("input", () => this.render());
      $("us-q").addEventListener("keydown", (e) => { if (e.key === "Escape" && $("us-q").value) { e.stopPropagation(); $("us-q").value = ""; this.render(); } });
      $("us-filter").addEventListener("change", () => this.render());
      $("us-refresh").addEventListener("click", async (ev) => {
        const b = ev.currentTarget; busy(b, true);
        try { await this.refresh(); toast("ok", "Students refreshed.", { silent: true }); } catch (e) { toast("bad", e.message); } finally { busy(b, false); }
      });
      document.querySelector("#us-table thead").addEventListener("click", (ev) => {
        const th = ev.target.closest("th[data-sort]");
        if (!th) return;
        const key = th.dataset.sort;
        if (this.sort.key === key) this.sort.dir = -this.sort.dir;
        else this.sort = { key, dir: key === "username" || key === "status" ? 1 : -1 };
        this.render();
      });
      document.querySelector("#us-table tbody").addEventListener("click", (ev) => {
        const tr = ev.target.closest("tr[data-uid]");
        if (!tr || ev.target.closest("a")) return;
        drawer.open(Number(tr.dataset.uid));
      });
    },
    /** Update one user row in place after an action. */
    upsert(u) {
      const i = this.users.findIndex((x) => x.id === u.id);
      if (i >= 0) this.users[i] = Object.assign({}, this.users[i], u); else this.users.push(u);
      st.usersById.set(u.id, this.users[i >= 0 ? i : this.users.length - 1]);
      this.render();
    },
    remove(id) {
      this.users = this.users.filter((u) => u.id !== id);
      st.usersById.delete(id);
      this.render();
    },
  };

  // ------------------------------------------------------------------ student drawer
  const drawer = {
    uid: null, user: null, trigger: null,
    el: () => $("drawer"),
    open(uid, trigger) {
      const d = this.el();
      this.uid = uid;
      this.trigger = trigger || document.activeElement;
      const known = st.usersById.get(uid);
      this.user = null;
      this.head(known);
      $("dr-body").innerHTML = `<div class="dr-skel"><span class="skel" style="width:60%"></span><br><span class="skel" style="width:85%"></span><br><span class="skel" style="width:40%"></span></div>`;
      if (!d.open) d.showModal();
      $("dr-close").focus();
      this.load();
    },
    close() { const d = this.el(); if (d.open) d.close(); },
    onClosed() {
      const uid = this.uid;
      this.uid = null; this.user = null;
      const btn = document.querySelector(`#us-table [data-open="${uid}"]`);
      const t = btn || (this.trigger && this.trigger.isConnected ? this.trigger : null);
      if (t) t.focus({ preventScroll: true });
    },
    focusInside() { const b = $("dr-close"); if (b) b.focus(); },
    async load() {
      const uid = this.uid;
      try {
        const u = await API.adminUser(uid);
        if (this.uid !== uid) return;
        this.user = u;
        this.head(u);
        this.render(u);
        students.upsert(Object.assign({}, u, { submissions: undefined }));
      } catch (e) {
        if (authLost(e)) { this.close(); return; }
        if (this.uid !== uid) return;
        $("dr-body").innerHTML = `<div class="err">${esc(e.message)}</div>` + (e.status === 404 ? `<p class="muted">This account may have been deleted.</p>` : "");
        if (e.status === 404) students.remove(uid);
      }
    },
    head(u) {
      $("dr-title").textContent = u ? u.username : "Student";
      $("dr-avatar").textContent = u ? u.username[0].toUpperCase() : "?";
      $("dr-badges").innerHTML = u ? (u.is_admin ? `<span class="badge-admin">admin${u.admin_env ? " · PR_ADMINS" : ""}</span>` : "") +
        (u.status === "disabled" ? `<span class="badge-disabled">disabled</span>` : `<span class="badge-ok">active</span>`) +
        (this.isSelf(u) ? `<span class="badge-you">you</span>` : "") : "";
    },
    isSelf(u) { return !!(u && st.me && u.username.toLowerCase() === st.me.username.toLowerCase()); },
    render(u) {
      const self = this.isSelf(u);
      const dis = u.status === "disabled";
      const facts = [
        ["Email", u.email || "none"], ["Joined", fullDate(u.created)], ["Last login", u.last_login ? fullDate(u.last_login) : "never"],
        ["Last submission", u.last_submission ? fullDate(u.last_submission) : "never"],
        ["Submissions", `${fmtInt(u.public_submissions)} public · ${fmtInt(u.full_submissions)} full`],
        ["Best", u.best_relative_total === null || u.best_relative_total === undefined ? "no finished full run" : fmtInt(u.best_relative_total) + (u.best_submission_id ? ` (#${u.best_submission_id})` : "")],
        ["Rank", u.rank ? "#" + fmtInt(u.rank) : dis ? "not ranked while disabled" : "unranked"],
      ];
      const selfHint = `title="That's you"`;
      const subs = Array.isArray(u.submissions) ? u.submissions : [];
      $("dr-body").innerHTML = `
        ${dis ? `<div class="note dr-disabled"><b>Disabled ${esc(relTime(u.disabled_at))}.</b> ${u.disabled_reason ? "Reason: " + esc(u.disabled_reason.replace(/[.!?]?\s*$/, "")) + "." : "No reason given."} They can't sign in and aren't on the public leaderboard.</div>` : ""}
        <dl class="kv dr-facts">${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
        <div class="msg dr-msg" id="dr-msg" role="alert"></div>
        <h3 class="section-title">Actions</h3>
        <div class="dr-actions">
          ${dis ? `<button type="button" class="btn sm" data-act="enable">Enable account</button>`
                : `<button type="button" class="btn sm danger" data-act="disable" ${self ? `disabled ${selfHint}` : ""}>Disable…</button>`}
          <button type="button" class="btn sm" data-act="password">Reset password…</button>
          <button type="button" class="btn sm" data-act="email">${u.email ? "Edit email…" : "Add email…"}</button>
          ${u.is_admin ? `<button type="button" class="btn sm" data-act="unadmin" ${self ? `disabled ${selfHint}` : ""}>Remove admin</button>`
                       : `<button type="button" class="btn sm" data-act="admin">Make admin</button>`}
          <button type="button" class="btn sm danger" data-act="delete" ${self ? `disabled ${selfHint}` : ""}>Delete…</button>
        </div>
        ${self ? `<p class="muted st-note">Disable, delete and remove admin are off: that's you.</p>` : ""}
        ${u.admin_env ? `<p class="muted st-note">Listed in PR_ADMINS: an admin whatever the database says.</p>` : ""}
        <div class="adm-section-head sm"><h3 class="section-title" style="margin:0">Submissions <span class="count-pill">${subs.length}</span></h3>
          ${subs.length ? `<button type="button" class="btn sm ghost" data-act="show-subs">Open in Submissions <span class="arrow" aria-hidden="true">→</span></button>` : ""}</div>
        ${subs.length ? `<ul class="dr-subs">${subs.map((s) => `<li>
            <span class="dr-sub-id">#${esc(s.id)}</span>${kindTag(s.kind)}${statusBadge(s)}
            <span class="dr-sub-score num">${s.status === "done" ? fmtInt(s.kind === "full" ? s.relative_total : s.raw_total) : ""}</span>
            <span class="muted dr-sub-when">${when(s.created)}</span>
            <button type="button" class="btn sm ghost danger" data-del-sub="${s.id}" ${s.status === "running" ? 'disabled title="Running: wait for it to finish"' : ""} aria-label="Delete submission ${s.id}">Delete</button></li>`).join("")}</ul>`
          : `<p class="muted">No submissions yet.</p>`}`;
    },
    msg(text) { const m = $("dr-msg"); if (m) m.innerHTML = text ? `<div class="err">${esc(text)}</div>` : ""; if (text && sound()) sound().error(); },
    async after(u, text) {
      if (u && u.id) { this.user = Object.assign({}, this.user, u); students.upsert(u); }
      toast("ok", text);
      await this.load();
    },
    async act(act, btn) {
      const u = this.user;
      if (!u) return;
      this.msg("");
      const back = () => this.focusInside();
      if (act === "enable") {
        busy(btn, true);
        try { await this.after(await API.adminEnable(u.id), `${u.username} is enabled again.`); } catch (e) { if (!authLost(e)) this.msg(e.message); busy(btn, false); }
      } else if (act === "admin" || act === "unadmin") {
        busy(btn, true);
        try { await this.after(await API.adminSetAdmin(u.id, act === "admin"), act === "admin" ? `${u.username} is now an admin.` : `${u.username} is no longer an admin.`); }
        catch (e) { if (!authLost(e)) this.msg(e.message); busy(btn, false); }
      } else if (act === "disable") {
        const r = await UI.dialog({
          title: `Disable ${u.username}?`, danger: true, confirmLabel: "Disable account", returnFocus: back,
          html: `<p>They're signed out everywhere, their queued runs are cancelled and they drop off the public leaderboard. Their data stays; you can enable them again later.</p>
            <div class="field" style="margin:0"><label for="dlg-reason">Reason <span class="hint">optional, only admins see it</span></label>
            <textarea id="dlg-reason" rows="2" maxlength="200" placeholder="e.g. shared code with another account"></textarea></div>`,
          onConfirm: () => {
            const reason = document.getElementById("dlg-reason").value.trim();
            if (reason.length > 200) throw new Error("Keep the reason under 200 characters.");
            return API.adminDisable(u.id, reason);
          },
        });
        if (r) await this.after(r, `${u.username} is disabled.`);
      } else if (act === "password") {
        await UI.dialog({
          title: `New password for ${u.username}`, confirmLabel: "Set password", returnFocus: back,
          html: `<p class="muted" style="margin-top:0">This signs ${esc(u.username)} out everywhere${this.isSelf(u) ? " except this browser" : ""}. Send them the new password privately.</p>
            <div class="field" style="margin:0"><label for="dlg-pw">Password <span class="hint">8–256 characters</span></label>
            <div class="inline-input"><input id="dlg-pw" type="text" autocomplete="new-password" spellcheck="false" autocapitalize="off" maxlength="256" class="mono">
            <button type="button" class="btn sm" id="dlg-gen">Generate</button></div></div>`,
          onOpen: (api) => {
            api.el.querySelector("#dlg-gen").addEventListener("click", () => { const i = api.el.querySelector("#dlg-pw"); i.value = genPassword(14); i.focus(); i.select(); });
          },
          onConfirm: async (api) => {
            const pw = api.el.querySelector("#dlg-pw").value;
            if (pw.length < 8 || pw.length > 256) throw new Error("Passwords need 8–256 characters. Try Generate.");
            const r = await API.adminPassword(u.id, pw);
            api.body.innerHTML = `<div class="okmsg">Password changed. ${fmtInt(r.sessions_revoked)} session${r.sessions_revoked === 1 ? "" : "s"} signed out.</div>
              <p style="margin:12px 0 6px">New password for <b>${esc(u.username)}</b>. It won't be shown again:</p>
              <div class="inline-input"><code class="pw-once" id="dlg-pw-show">${esc(pw)}</code><button type="button" class="btn sm" id="dlg-copy">Copy</button></div>`;
            api.ok.remove(); api.ok = null;
            api.cancel.textContent = "Done";
            api.el.querySelector("#dlg-copy").addEventListener("click", async (ev) => {
              const btn = ev.currentTarget;
              const ok = await copyText(pw, api.el);
              btn.textContent = ok ? "Copied ✓" : "Copy failed: select it";
              if (!ok) { const r2 = document.createRange(); r2.selectNodeContents(api.el.querySelector("#dlg-pw-show")); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r2); }
            });
            api.el.querySelector("#dlg-copy").focus();
            toast("ok", `Password reset for ${u.username}.`);
            return false;
          },
        });
      } else if (act === "email") {
        const r = await UI.dialog({
          title: u.email ? `Edit ${u.username}'s email` : `Add an email for ${u.username}`, confirmLabel: "Save email", returnFocus: back,
          html: `<div class="field" style="margin:0"><label for="dlg-email">Email <span class="hint">leave empty to remove it</span></label>
            <input id="dlg-email" type="email" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="254" value="${esc(u.email || "")}"></div>`,
          onConfirm: (api) => {
            const v = api.el.querySelector("#dlg-email").value.trim();
            if (v && !(v.length <= 254 && EMAIL_RE.test(v))) throw new Error("That doesn't look like an email address.");
            if ((v || null) === (u.email || null)) return null;
            return API.adminSetEmail(u.id, v || null);
          },
        });
        if (r) await this.after(r, r.email ? `Email for ${u.username} set to ${r.email}.` : `Email removed from ${u.username}.`);
      } else if (act === "delete") {
        const n = (u.public_submissions || 0) + (u.full_submissions || 0);
        const r = await UI.dialog({
          title: `Delete ${u.username} permanently?`, danger: true, confirmLabel: "Delete forever", requireText: u.username, returnFocus: back,
          html: `<p>This removes the account, its sessions and <b>${fmtInt(n)} submission${n === 1 ? "" : "s"}</b> with their results. Scores are recomputed on the next read. The audit log keeps a record.</p>
            <p class="muted">To remove someone reversibly, disable them instead.</p>`,
          onConfirm: () => API.adminDeleteUser(u.id, u.username),
        });
        if (r) {
          toast("ok", `${r.deleted || u.username} deleted with ${fmtInt(r.submissions)} submission${r.submissions === 1 ? "" : "s"}.`);
          students.remove(u.id);
          submissionsSec.dirty = true;
          this.close();
        }
      } else if (act === "show-subs") {
        this.close();
        submissionsSec.setFilters({ user: u.username });
        selectTab("submissions", { focusPanel: true });
      }
    },
    async deleteSub(id) {
      const ok = await UI.dialog({
        title: `Delete submission #${id}?`, danger: true, confirmLabel: "Delete submission", returnFocus: () => this.focusInside(),
        html: `<p>This permanently removes its source and results. Scores are recomputed on the next read.</p>`,
        onConfirm: () => API.adminDeleteSubmission(id),
      });
      if (ok) { toast("ok", `Submission #${id} deleted.`); submissionsSec.dirty = true; await this.load(); }
    },
    wire() {
      const d = this.el();
      $("dr-close").addEventListener("click", () => this.close());
      d.addEventListener("close", () => this.onClosed());
      d.addEventListener("click", (ev) => {
        if (ev.target === d) { this.close(); return; }   // backdrop
        const a = ev.target.closest("[data-act]");
        if (a && !a.disabled) { this.act(a.dataset.act, a); return; }
        const del = ev.target.closest("[data-del-sub]");
        if (del && !del.disabled) this.deleteSub(Number(del.dataset.delSub));
      });
    },
  };

  // ================================================================== SUBMISSIONS
  const submissionsSec = {
    list: [], open: new Set(), srcOpen: new Set(), details: new Map(), detailErr: new Map(), rowCache: new Map(),
    filters: {}, loaded: false, dirty: false, busyFlag: false,
    pollMs() { return this.list.some(ACTIVE) ? 3000 : 0; },
    show(changed) { return changed || !this.loaded || this.dirty ? this.refresh(true) : null; },
    params() {
      const f = this.filters, p = {};
      ["user", "kind", "status", "verdict"].forEach((k) => { if (f[k]) p[k] = f[k]; });
      if (f.since) p.since = f.since;
      if (f.until) p.until = f.until;
      return p;
    },
    readFilters() {
      const since = fromLocalInput($("sb-since").value), until = fromLocalInput($("sb-until").value);
      this.filters = { user: $("sb-user").value.trim(), kind: $("sb-kind").value, status: $("sb-status").value, verdict: $("sb-verdict").value,
        since: since || "", until: until || "" };
    },
    setFilters(f) {
      $("sb-user").value = f.user || ""; $("sb-kind").value = f.kind || ""; $("sb-status").value = f.status || ""; $("sb-verdict").value = f.verdict || "";
      $("sb-since").value = ""; $("sb-until").value = "";
      this.readFilters();
      this.dirty = true;
    },
    async refresh() {
      // a filter change during a poll must not be lost: run once more when the current one ends
      if (this.busyFlag) { this.again = true; return this.pending; }
      this.busyFlag = true;
      this.pending = this.fetchOnce().finally(() => {
        this.busyFlag = false;
        if (this.again) { this.again = false; return this.refresh(); }
        return undefined;
      });
      return this.pending;
    },
    async fetchOnce() {
      try {
        const list = await API.adminSubmissions(this.params());
        this.list = Array.isArray(list) ? list : (list && Array.isArray(list.submissions) ? list.submissions : []);
        this.loaded = true; this.dirty = false;
        const jobs = [];
        for (const s of this.list) {
          if (!this.open.has(s.id)) continue;
          const d = this.details.get(s.id);
          if (!d || d.status !== s.status || ACTIVE(s) || d.tests_done !== s.tests_done) jobs.push(this.loadDetail(s.id, false));
        }
        this.render();
        await Promise.all(jobs);
        if (jobs.length) this.render();
        const n = this.list.length;
        const active = this.list.filter(ACTIVE).length;
        $("ac-submissions").textContent = String(n) + (n >= 1000 ? "+" : "");
        $("sb-updated").textContent = `${fmtInt(n)} submission${n === 1 ? "" : "s"}${n >= 1000 ? " (the newest 1,000: narrow the filters)" : ""}${active ? ` · ${active} in progress` : ""} · updated ${new Date().toLocaleTimeString()}`;
      } catch (e) {
        if (authLost(e)) return;
        $("sb-updated").textContent = "Couldn't load: " + e.message;
        if (!this.loaded || e.status === 400) { document.querySelector("#sb-table tbody").innerHTML = emptyRow(8, `<div class="err">${esc(e.message)}</div>`); this.rowCache.clear(); }
        throw e;
      }
    },
    async loadDetail(id, rerender) {
      try {
        this.details.set(id, await API.submission(id));
        this.detailErr.delete(id);
      } catch (e) { this.detailErr.set(id, e.message); }
      if (rerender !== false) this.render();
    },
    rowHTML(s) {
      const open = this.open.has(s.id);
      const exp = `<button type="button" class="expander" aria-expanded="${open}" aria-controls="sbd-${s.id}" data-exp="${s.id}"><span class="chev" aria-hidden="true">▶</span>#${s.id}</button>`;
      const user = `<button type="button" class="linkish" data-user-open="${esc(s.user_id)}" title="Open ${esc(s.username)}">${esc(s.username || "?")}</button>${s.user_disabled ? ` <span class="badge-disabled">disabled</span>` : ""}`;
      const actions = `<td class="r nw act-cell">` +
        (s.status === "queued" ? `<button type="button" class="btn sm" data-cancel="${s.id}" aria-label="Cancel submission ${s.id}">Cancel</button> ` : "") +
        `<button type="button" class="btn sm" data-rejudge="${s.id}" ${ACTIVE(s) ? "disabled" : ""} aria-label="Rejudge submission ${s.id}">Rejudge</button> ` +
        `<button type="button" class="btn sm danger" data-delete="${s.id}" ${s.status === "running" ? 'disabled title="Running: wait for it to finish"' : ""} aria-label="Delete submission ${s.id}">Delete</button></td>`;
      return `<td>${exp}</td><td class="user">${user}</td><td class="opt">${kindTag(s.kind)}</td><td>${statusBadge(s)}</td><td class="r">${scoreCell(s)}</td>` +
        `<td class="r opt nw">${testsCell(s)}</td><td class="r opt nw">${when(s.created)}</td>${actions}`;
    },
    detailHTML(s) {
      const d = this.details.get(s.id), err = this.detailErr.get(s.id);
      if (err) return `<div class="err">${esc(err)}</div>`;
      if (!d) return `<div><span class="skel" style="width:60%"></span><br><span class="skel" style="width:40%"></span></div>`;
      const results = Array.isArray(d.results) ? d.results.slice() : [];
      const bySeed = results.length && results[0].seed !== undefined;
      results.sort((a, b) => (a.test !== undefined && b.test !== undefined ? a.test - b.test : (a.seed || 0) - (b.seed || 0)));
      const tot = d.tests_total || s.tests_total || results.length;
      let out = "";
      if (s.status === "running" && tot) {
        const done = d.tests_done || s.tests_done || 0, pct = Math.min(100, (100 * done) / tot);
        out += `<div class="judge"><div class="top"><span><b>Judging test ${Math.min(done + 1, tot)} of ${tot}</b></span><span class="muted num">${Math.round(pct)}%</span></div>
          <div class="progress" role="progressbar" aria-label="Judging progress" aria-valuemin="0" aria-valuemax="${tot}" aria-valuenow="${done}"><div class="bar" style="width:${pct}%"></div></div></div>`;
      } else if (s.status === "queued") {
        out += `<div class="judge"><div class="top"><span><b>Waiting in the queue</b>${s.position ? ` · position ${s.position}` : ""}</span></div><div class="progress indet"><div class="bar"></div></div></div>`;
      }
      if (d.status === "error" || d.error) out += `<div class="err"><b>${/cancel/i.test(d.error || "") ? "Cancelled." : "Judge error."}</b> ${esc(d.error || "")}</div>`;
      const stats = [["User", d.username || s.username]];
      if (d.status === "done") {
        if (d.kind === "full") stats.push(["Relative total", fmtInt(d.relative_total)]);
        stats.push(["Raw total", fmtInt(d.raw_total)], ["Tests passed", `${fmtInt(d.tests_ac)} / ${fmtInt(tot)}`], ["Slowest test", fmtSecs(d.max_time)]);
      }
      stats.push(["Submitted", fullDate(d.created)]);
      if (d.started) stats.push(["Started", fullDate(d.started)]);
      if (d.finished) stats.push(["Finished", fullDate(d.finished)]);
      out += `<div class="detail-stats">${stats.map(([k, v]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v" style="font-size:16px">${esc(v)}</div></div>`).join("")}</div>`;
      if (tot && results.length) {
        const chips = results.map((r) => {
          const lab = r.test !== undefined ? r.test : r.seed;
          const tip = [r.test !== undefined ? `Test ${r.test}` : "", r.seed !== undefined ? `seed ${r.seed}` : "", r.verdict,
            r.score !== undefined && r.verdict === "AC" ? `${fmtInt(r.score)} pts` : "", r.time !== undefined && r.time !== null ? fmtSecs(r.time) : ""].filter(Boolean).join(" · ");
          return `<span class="v ${esc(r.verdict)}" tabindex="0" role="listitem" data-tip="${esc(tip)}" aria-label="${esc(tip)}">${esc(lab)}</span>`;
        });
        for (let i = results.length; i < tot; i++) chips.push(`<span class="v pending" role="listitem" aria-label="not judged yet">·</span>`);
        out += `<div><div class="section-title" style="margin-bottom:8px">${d.kind === "public" ? "Seeds" : "Tests"}</div><div class="vgrid" role="list" aria-label="Per-test verdicts">${chips.join("")}</div></div>`;
      }
      if (bySeed) {
        out += `<details class="src"><summary>Per-seed results · ${results.length}</summary><div class="table-scroll" style="background:transparent;margin-top:8px"><table class="data">
          <thead><tr>${d.kind === "full" ? `<th scope="col">Test</th>` : ""}<th scope="col">Seed</th><th scope="col">Verdict</th><th scope="col" class="r">Score</th><th scope="col">Cleared</th><th scope="col" class="r">Time</th><th scope="col" class="opt">Message</th></tr></thead><tbody>` +
          results.map((r) => `<tr>${d.kind === "full" ? `<td class="mono">${esc(r.test)}</td>` : ""}<td class="mono">${esc(r.seed)}</td><td><span class="v ${esc(r.verdict)}" style="height:24px">${esc(r.verdict)}</span></td>` +
            `<td class="r">${fmtInt(r.score)}</td><td class="nw">${esc(r.cleared === undefined ? "–" : r.cleared + "/8")}</td><td class="r nw">${fmtSecs(r.time)}</td>` +
            `<td class="opt muted" style="font-size:13px;max-width:320px;overflow-wrap:anywhere">${esc(r.message || "")}</td></tr>`).join("") + `</tbody></table></div></details>`;
      }
      if (typeof d.source === "string") {
        const lines = d.source.split("\n").length;
        out += `<details class="src" data-src="${s.id}"${this.srcOpen.has(s.id) ? " open" : ""}><summary>Source code · ${fmtInt(lines)} lines · ${(new Blob([d.source]).size / 1024).toFixed(1)} KB</summary>
          ${this.srcOpen.has(s.id) ? window.Code.view(d.source, { label: `Source of submission ${s.id}` }) : ""}</details>`;
      }
      return out;
    },
    render() {
      const tbody = document.querySelector("#sb-table tbody");
      if (!this.list.length) {
        const anyFilter = Object.values(this.filters).some(Boolean);
        tbody.innerHTML = emptyRow(8, anyFilter ? "No submission matches these filters." : `<span class="big" aria-hidden="true">♠︎</span>Nobody has submitted yet.`);
        this.rowCache.clear();
        return;
      }
      const want = [];
      for (const s of this.list) {
        want.push({ key: "sbr-" + s.id, cls: "row" + (this.open.has(s.id) ? " open" : "") + (s.user_disabled ? " is-disabled" : ""), html: this.rowHTML(s) });
        if (this.open.has(s.id)) want.push({ key: "sbd-" + s.id, cls: "detail", html: `<td colspan="8"><div class="detail-inner">${this.detailHTML(s)}</div></td>` });
      }
      patchRows(tbody, want, this.rowCache);
    },
    async bulk(which) {
      const label = which === "errored" ? "every errored submission (not cancelled ones)" : "every finished full submission";
      const r = await UI.dialog({
        title: which === "errored" ? "Rejudge all errored submissions?" : "Rejudge all full submissions?",
        confirmLabel: "Rejudge", danger: which === "full",
        html: `<p>This puts ${label} back in the queue. Submissions of disabled students are skipped; queued or running ones are left alone.</p>` +
          (which === "full" ? `<p class="muted">With many students this keeps the judge busy for a while, and their scores are blank until each run finishes again.</p>` : ""),
        onConfirm: () => API.adminRejudgeBulk(which),
      });
      if (r) {
        toast(r.count ? "ok" : "info", r.count ? `${fmtInt(r.count)} submission${r.count === 1 ? "" : "s"} queued for rejudging.` : "Nothing to rejudge.");
        this.details.clear();
        await this.refresh().catch(() => {});
        schedule();
      }
    },
    wire() {
      const tz = tzLabel();
      document.querySelectorAll(".tz-hint").forEach((el) => { el.textContent = tz.replace(/ \(.*\)$/, "") === tz ? tz : tz.match(/\((.*)\)/)[1]; el.title = tz; });
      $("sb-filters").addEventListener("submit", (ev) => {
        ev.preventDefault();
        const s = $("sb-since").value, u = $("sb-until").value;
        if (s && u && fromLocalInput(s) > fromLocalInput(u)) { toast("bad", "“From” is after “To”."); $("sb-since").focus(); return; }
        this.readFilters(); this.open.clear();
        this.refresh().catch(() => {}).finally(schedule);
      });
      ["sb-kind", "sb-status", "sb-verdict"].forEach((id) => $(id).addEventListener("change", () => $("sb-filters").requestSubmit()));
      $("sb-reset").addEventListener("click", () => { this.setFilters({}); this.refresh().catch(() => {}).finally(schedule); });
      $("sb-zip-pick").addEventListener("change", () => { $("sb-zip").href = "/api/admin/submissions.zip?pick=" + encodeURIComponent($("sb-zip-pick").value); });
      $("sb-bulk-errored").addEventListener("click", () => this.bulk("errored"));
      $("sb-bulk-full").addEventListener("click", () => this.bulk("full"));
      const table = $("sb-table");
      table.addEventListener("toggle", (ev) => {
        const det = ev.target;
        if (!det.matches || !det.matches("details[data-src]")) return;
        const id = Number(det.dataset.src);
        if (det.open === this.srcOpen.has(id)) return;
        if (det.open) this.srcOpen.add(id); else this.srcOpen.delete(id);
        this.render();
      }, true);
      table.addEventListener("click", async (ev) => {
        const exp = ev.target.closest("[data-exp]");
        if (exp) {
          const id = Number(exp.dataset.exp);
          if (this.open.has(id)) this.open.delete(id); else { this.open.add(id); this.details.delete(id); this.loadDetail(id); }
          this.render();
          return;
        }
        const uo = ev.target.closest("[data-user-open]");
        if (uo) { const uid = Number(uo.dataset.userOpen); if (uid) drawer.open(uid, uo); return; }
        const rj = ev.target.closest("[data-rejudge]");
        if (rj) {
          const id = Number(rj.dataset.rejudge);
          busy(rj, true);
          try { await API.rejudge(id); toast("ok", `Submission #${id} is queued for rejudging.`); this.details.delete(id); await this.refresh(); schedule(); }
          catch (e) { if (!authLost(e)) toast("bad", e.message); busy(rj, false); }
          return;
        }
        const cn = ev.target.closest("[data-cancel]");
        if (cn) {
          const id = Number(cn.dataset.cancel);
          busy(cn, true);
          try { await API.adminCancel(id); toast("ok", `Submission #${id} cancelled.`); this.details.delete(id); await this.refresh(); schedule(); }
          catch (e) { if (!authLost(e)) toast("bad", e.message); busy(cn, false); }
          return;
        }
        const del = ev.target.closest("[data-delete]");
        if (del) {
          const id = Number(del.dataset.delete);
          const s = this.list.find((x) => x.id === id);
          const ok = await UI.dialog({
            title: `Delete submission #${id}?`, danger: true, confirmLabel: "Delete submission",
            html: `<p>This permanently removes the source and results of <b>#${id}</b>${s ? ` by <b>${esc(s.username)}</b> (${esc(s.kind)}, ${esc(s.status)})` : ""}. Scores are recomputed on the next read. The audit log keeps its totals.</p>`,
            onConfirm: () => API.adminDeleteSubmission(id),
            returnFocus: () => $("ap-submissions").focus(),
          });
          if (ok) { toast("ok", `Submission #${id} deleted.`); this.open.delete(id); this.details.delete(id); await this.refresh().catch(() => {}); schedule(); }
        }
      });
    },
  };

  // ================================================================== SETTINGS
  const ENV = { name: "PR_CONTEST_NAME", start: "PR_START", end: "PR_END", freeze: "PR_FREEZE", invite: "PR_INVITE", full_per_hour: "PR_FULL_PER_HOUR", announcement: null, announcement_on: null };
  const LABELS = { name: "name", start: "start", end: "end", freeze: "freeze", invite: "invite", full_per_hour: "full runs per hour", announcement: "announcement text", announcement_on: "banner on/off" };
  const DATE_KEYS = ["start", "end", "freeze"];
  function fmtSetting(key, v) {
    if (v === null || v === undefined) return DATE_KEYS.includes(key) ? "unset" : "–";
    if (typeof v === "boolean") return v ? "on" : "off";
    if (DATE_KEYS.includes(key)) return fullDate(v);
    if (key === "invite") return v === "" ? "(open registration)" : String(v);
    if (v === "") return "(empty)";
    const s = String(v);
    return s.length > 80 ? s.slice(0, 77) + "…" : s;
  }
  const settings = {
    data: null, loaded: false,
    pollMs: () => 0,
    show() { return this.loaded && this.dirtyKeys().length ? null : this.load(); },
    async refresh() { return this.load(); },
    async load() {
      try {
        const d = await API.adminSettings();
        this.data = d; this.loaded = true;
        this.fill(null);
        this.renderReadonly();
        $("se-updated").textContent = "Loaded " + new Date().toLocaleTimeString();
      } catch (e) {
        if (authLost(e)) return;
        $("se-msg").innerHTML = `<div class="err">${esc(e.message)}</div>`;
        throw e;
      }
    },
    val(key) { const e = this.data && this.data.editable && this.data.editable[key]; return e ? e.value : undefined; },
    /** Put loaded values into the inputs (all keys, or only `keys`) and refresh every meta line. */
    fill(keys) {
      const want = (k) => !keys || keys.includes(k);
      if (want("name")) $("se-name").value = this.val("name") || "";
      DATE_KEYS.forEach((k) => { if (want(k)) $("se-" + k).value = toLocalInput(this.val(k)); });
      if (want("full_per_hour")) $("se-fph").value = this.val("full_per_hour") === undefined || this.val("full_per_hour") === null ? "" : String(this.val("full_per_hour"));
      if (want("invite")) {
        const inv = this.val("invite") || "";
        $("se-open").checked = inv === "";
        $("se-invite").value = inv;
      }
      if (want("announcement")) $("se-ann").value = this.val("announcement") || "";
      if (want("announcement_on")) $("se-ann-on").checked = !!this.val("announcement_on");
      this.renderMeta();
      this.sync();
    },
    renderMeta() {
      document.querySelectorAll("#se-form .set-field[data-key]").forEach((f) => {
        const key = f.dataset.key, e = this.data.editable[key];
        if (!e) return;
        const envName = ENV[key];
        const envText = key === "invite" && e.env ? "set (hidden)" : fmtSetting(key, e.env);
        f.querySelector(".set-meta").innerHTML = `<span class="ov-tag${e.overridden ? " on" : ""}">${e.overridden ? "overridden" : envName ? "from env" : "default"}</span>` +
          `<span class="muted">${envName ? `<span class="mono">${envName}</span>` : "default"}: ${esc(envText)}</span>` +
          (e.overridden ? `<button type="button" class="btn sm ghost" data-reset="${key}">Reset to ${envName ? "env value" : "default"}</button>` : "");
      });
    },
    renderReadonly() {
      const r = (this.data && this.data.readonly) || {};
      const items = [["Hidden tests", fmtInt(r.hidden_count)], ["Public seeds", seedsText(r.public_seeds)], ["Time limit", r.time_limit !== undefined ? `${r.time_limit} s / test` : "–"],
        ["Memory", r.memory_mb !== undefined ? `${fmtInt(r.memory_mb)} MB` : "–"], ["Max source", r.max_source_kb !== undefined ? `${fmtInt(r.max_source_kb)} KB` : "–"],
        ["Sandbox", r.sandbox || "–"], ["Judge workers", fmtInt(r.workers)]];
      $("se-readonly").innerHTML = items.map(([k, v]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("") +
        (r.sandbox === "local" ? `<p class="err" style="grid-column:1/-1;margin:0">The sandbox is <b>local</b>: fine for development, not a security boundary. Production should use <code>PR_SANDBOX=docker</code>.</p>` : "");
    },
    /** What the form says now (normalised like the API wants it). */
    current() {
      const c = {};
      c.name = $("se-name").value.trim();
      DATE_KEYS.forEach((k) => { c[k] = fromLocalInput($("se-" + k).value); });
      const fph = $("se-fph").value.trim();
      c.full_per_hour = fph === "" ? NaN : Number(fph);
      c.invite = $("se-open").checked ? "" : $("se-invite").value.trim();
      c.announcement = $("se-ann").value;
      c.announcement_on = $("se-ann-on").checked;
      return c;
    },
    dirtyKeys() {
      if (!this.data) return [];
      const c = this.current(), out = [];
      if (c.name !== (this.val("name") || "")) out.push("name");
      DATE_KEYS.forEach((k) => { if ($("se-" + k).value !== toLocalInput(this.val(k))) out.push(k); });
      if (String(c.full_per_hour) !== String(this.val("full_per_hour"))) out.push("full_per_hour");
      if (c.invite !== (this.val("invite") || "")) out.push("invite");
      if (c.announcement !== (this.val("announcement") || "")) out.push("announcement");
      if (c.announcement_on !== !!this.val("announcement_on")) out.push("announcement_on");
      return out;
    },
    validate(c) {
      const bad = (id, msg) => ({ id, msg });
      if (c.name.length < 1 || c.name.length > 80) return bad("se-name", "The name needs 1–80 characters.");
      for (const k of DATE_KEYS) if (c[k] === undefined) return bad("se-" + k, `The ${k} time isn't a valid date.`);
      const s = parseTime(c.start), e = parseTime(c.end), f = parseTime(c.freeze);
      if (s !== null && e !== null && !(s < e)) return bad("se-end", "The end must be after the start.");
      if (f !== null && s !== null && f < s) return bad("se-freeze", "The freeze can't be before the start.");
      if (f !== null && e !== null && !(f < e)) return bad("se-freeze", "The freeze must be before the end.");
      if (!Number.isInteger(c.full_per_hour) || c.full_per_hour < 0 || c.full_per_hour > 1000) return bad("se-fph", "Full runs per hour: a whole number from 0 to 1000.");
      if (c.invite !== "" && (c.invite.length < 4 || c.invite.length > 64 || /[\x00-\x1f\x7f]/.test(c.invite))) return bad("se-invite", "Invite codes are 4–64 printable characters (or switch on open registration).");
      if (c.announcement.length > 500) return bad("se-ann", "The announcement is limited to 500 characters.");
      return null;
    },
    sync() {
      const keys = this.dirtyKeys();
      $("se-save").disabled = !keys.length;
      $("se-revert").disabled = !keys.length;
      $("se-dirty").textContent = keys.length ? `${keys.length} unsaved change${keys.length === 1 ? "" : "s"}: ${keys.map((k) => LABELS[k]).join(", ")}` : "No unsaved changes";
      $("se-dirty").classList.toggle("dirty", !!keys.length);
      $("se-dirty").parentElement.classList.toggle("dirty", !!keys.length);
      document.querySelectorAll("#se-form .set-field[data-key]").forEach((f) => f.classList.toggle("changed", keys.includes(f.dataset.key)));
      const open = $("se-open").checked;
      $("se-invite").disabled = open;
      $("se-invite-copy").disabled = open || !$("se-invite").value;
      $("se-invite-show").disabled = open;
      const text = $("se-ann").value;
      $("se-ann-count").textContent = `${text.length} / 500`;
      $("se-ann-count").classList.toggle("over", text.length > 450);
      const prev = $("se-preview");
      if (text.trim()) { prev.innerHTML = UI.announcementHTML(text.trim(), true); prev.classList.remove("empty"); }
      else { prev.innerHTML = `<div class="wrap"><p class="announce-text muted">Write some text to see the banner.</p></div>`; prev.classList.add("empty"); }
      prev.classList.toggle("off", !$("se-ann-on").checked);
      $("se-preview-note").textContent = !$("se-ann-on").checked ? "The banner is off: nobody sees it until you switch it on and save."
        : !text.trim() ? "The banner is on but empty, so nobody sees it." : "Visitors see this at the top of every page (they can dismiss it; new text shows again).";
    },
    async save() {
      const msg = $("se-msg");
      msg.innerHTML = "";
      document.querySelectorAll("#se-form [aria-invalid]").forEach((el) => el.setAttribute("aria-invalid", "false"));
      const keys = this.dirtyKeys();
      if (!keys.length) return;
      const c = this.current();
      const v = this.validate(c);
      if (v) {
        msg.innerHTML = `<div class="err">${esc(v.msg)}</div>`;
        const el = $(v.id); el.setAttribute("aria-invalid", "true"); el.focus();
        if (sound()) sound().error();
        return;
      }
      const set = {};
      keys.forEach((k) => { set[k] = c[k]; });
      const btn = $("se-save");
      busy(btn, true);
      try {
        this.data = await API.adminSaveSettings(set, []);
        this.fill(null);
        toast("ok", `Saved: ${keys.map((k) => LABELS[k]).join(", ")}.`);
        this.afterChange();
      } catch (e) {
        if (authLost(e)) return;
        msg.innerHTML = `<div class="err">${esc(e.message)}</div>`;
        if (sound()) sound().error();
      } finally { busy(btn, false); this.sync(); }
    },
    afterChange() {
      API.forgetContest();
      UI.refreshAnnouncement();
      API.contest(true).then((c) => { st.contest = c; overview.renderContest(c); }, () => {});
      activity.dirty = true;
      $("se-updated").textContent = "Saved " + new Date().toLocaleTimeString();
    },
    async reset(key, btn) {
      busy(btn, true);
      $("se-msg").innerHTML = "";
      try {
        this.data = await API.adminSaveSettings({}, [key]);
        this.fill([key]);
        toast("ok", `${LABELS[key][0].toUpperCase() + LABELS[key].slice(1)} is back to the ${ENV[key] ? "environment value" : "default"}.`);
        this.afterChange();
      } catch (e) {
        if (authLost(e)) return;
        $("se-msg").innerHTML = `<div class="err">${esc(e.message)}</div>`;
        busy(btn, false);
      }
    },
    wire() {
      $("se-tz").textContent = tzLabel();
      const form = $("se-form");
      form.addEventListener("input", () => this.sync());
      form.addEventListener("change", () => this.sync());
      form.addEventListener("submit", (ev) => { ev.preventDefault(); this.save(); });
      $("se-revert").addEventListener("click", () => { this.fill(null); $("se-msg").innerHTML = ""; toast("info", "Changes discarded.", { silent: true }); });
      form.addEventListener("click", async (ev) => {
        const r = ev.target.closest("[data-reset]");
        if (r) { this.reset(r.dataset.reset, r); return; }
        const cl = ev.target.closest("[data-clear-input]");
        if (cl) { const i = $(cl.dataset.clearInput); i.value = ""; i.focus(); this.sync(); }
      });
      $("se-invite-show").addEventListener("click", (ev) => {
        const b = ev.currentTarget, show = $("se-invite").type === "password";
        $("se-invite").type = show ? "text" : "password";
        b.textContent = show ? "Hide" : "Show";
        b.setAttribute("aria-pressed", show ? "true" : "false");
      });
      $("se-invite-copy").addEventListener("click", async () => {
        const ok = await copyText($("se-invite").value);
        toast(ok ? "ok" : "bad", ok ? "Invite code copied." : "Couldn't copy: select it and copy by hand.", { silent: !ok });
      });
      $("se-invite-regen").addEventListener("click", async (ev) => {
        const b = ev.currentTarget;
        const r = await UI.dialog({
          title: "Regenerate the invite code?", confirmLabel: "Regenerate",
          html: `<p>A fresh random code replaces the current one${$("se-open").checked ? " and closes open registration" : ""}. The old code stops working right away; existing accounts aren't affected.</p>`,
          onConfirm: () => API.adminRegenerateInvite(),
          returnFocus: () => b.focus(),
        });
        if (!r) return;
        this.data = r;
        this.fill(["invite"]);
        $("se-invite").type = "text"; $("se-invite-show").textContent = "Hide"; $("se-invite-show").setAttribute("aria-pressed", "true");
        toast("ok", "New invite code ready. Copy it and share it with the students.");
        this.afterChange();
      });
    },
  };

  // ================================================================== LEADERBOARD
  const leaderboard = {
    rows: [], loaded: false, rowCache: new Map(),
    pollMs: () => 10000,
    show() { return this.refresh(true); },
    async refresh() {
      try {
        const [d, c] = await Promise.all([API.leaderboard(), st.contest ? Promise.resolve(st.contest) : API.contest().catch(() => null)]);
        if (c) st.contest = c;
        this.rows = Array.isArray(d.rows) ? d.rows : [];
        this.loaded = true;
        this.render();
        const c2 = st.contest;
        const fz = c2 && c2.freeze ? parseTime(c2.freeze) : null;
        const frozen = fz !== null && fz <= Date.now();
        const note = $("lbx-frozen");
        note.classList.toggle("hidden", !(frozen || d.frozen));
        note.innerHTML = `<span aria-hidden="true">❄︎</span><span><b>Students see a frozen board</b> (submissions before ${esc(fullDate(c2 && c2.freeze))}). This view is live; “public view” exports what they see.</span>`;
        const ranked = this.rows.filter((r) => r.rank !== null && r.rank !== undefined && !r.disabled).length;
        $("lb-updated").textContent = `${ranked} ranked${this.rows.length > ranked ? ` · ${this.rows.length - ranked} disabled` : ""} · updated ${new Date().toLocaleTimeString()}`;
      } catch (e) {
        if (authLost(e)) return;
        $("lb-updated").textContent = "Couldn't refresh: " + e.message;
        throw e;
      }
    },
    render() {
      const tbody = document.querySelector("#lbx-table tbody");
      const q = $("lbx-q").value.trim().toLowerCase();
      const list = q ? this.rows.filter((r) => String(r.username).toLowerCase().includes(q)) : this.rows;
      if (!list.length) {
        tbody.innerHTML = emptyRow(7, this.rows.length ? `No player matches “${esc(q)}”.` : `<span class="big" aria-hidden="true">♣︎</span>No finished full submissions yet.`);
        this.rowCache.clear();
        return;
      }
      const c = st.contest;
      const max = c && c.hidden_count ? c.hidden_count * 1e6 : Math.max(1, ...this.rows.map((r) => Number(r.relative_total) || 0));
      patchRows(tbody, list.map((r) => {
        const off = r.rank === null || r.rank === undefined || r.disabled;
        const pct = Math.max(0, Math.min(100, (100 * (Number(r.relative_total) || 0)) / max));
        return {
          key: "lbx-" + r.username, cls: "row" + (off ? " is-disabled" : ""),
          html: `<td class="rank">${off ? `<span class="badge-disabled">disabled</span>` : r.rank <= 3 ? `<span class="medal m${r.rank}" aria-label="rank ${r.rank}">${r.rank}</span>` : `<span class="rk">${esc(r.rank)}</span>`}</td>` +
            `<td class="user">${esc(r.username)}</td>` +
            `<td class="r scorecell"><div class="scorebar"><b class="num">${fmtInt(r.relative_total)}</b><span class="track" aria-hidden="true"><i style="width:${pct}%"></i></span></div></td>` +
            `<td class="r opt">${fmtInt(r.raw_total)}</td><td class="r opt">${fmtInt(r.tests_ac)}${c && c.hidden_count ? `<span class="muted">/${c.hidden_count}</span>` : ""}</td>` +
            `<td class="r opt">${fmtInt(r.submissions)}</td><td class="r opt">${when(r.submitted)}</td>`,
        };
      }), this.rowCache);
    },
    wire() {
      $("lbx-q").addEventListener("input", () => this.render());
      $("lbx-refresh").addEventListener("click", async (ev) => {
        const b = ev.currentTarget; busy(b, true);
        try { await this.refresh(); toast("ok", "Leaderboard refreshed.", { silent: true }); } catch (e) { toast("bad", e.message); } finally { busy(b, false); }
      });
    },
  };

  // ================================================================== SIMILARITY
  const TRIVIAL = new Set(["else:", "try:", "pass", "return", "break", "continue", "finally:", "import sys", "main()", "if __name__ == \"__main__\":",
    "if __name__ == '__main__':", "return out", "return moves", "\"\"\"", "'''"]);
  const normLine = (l) => l.replace(/\s+/g, " ").trim();
  const trivialLine = (n) => n.length < 6 || !/[A-Za-z0-9]/.test(n) || TRIVIAL.has(n) || /^#\s*[-=#*~]*$/.test(n);
  const similarity = {
    data: null, src: new Map(), starter: null, current: null,
    pollMs: () => 0,
    show() { return null; },
    refresh() { return null; },
    async run() {
      const b = $("si-run");
      busy(b, true);
      $("si-pairs").innerHTML = `<div class="panel"><span class="skel" style="width:70%"></span><br><span class="skel" style="width:50%"></span></div>`;
      try {
        const d = await API.adminSimilarity(Number($("si-limit").value) || 50);
        this.data = d;
        this.render();
        $("si-updated").textContent = "Checked " + new Date().toLocaleTimeString();
        toast("ok", `Compared ${fmtInt(d.compared)} students: ${fmtInt((d.pairs || []).length)} pair${(d.pairs || []).length === 1 ? "" : "s"} at 20% or more.`, { silent: true });
      } catch (e) {
        if (authLost(e)) return;
        $("si-pairs").innerHTML = `<div class="err">${esc(e.message)}</div>`;
      } finally { busy(b, false); }
    },
    render() {
      const d = this.data;
      const pairs = Array.isArray(d.pairs) ? d.pairs : [];
      $("si-meta").innerHTML = `<div class="adm-chips"><span class="chip">Compared <b class="num">${fmtInt(d.compared)}</b></span><span class="chip">Skipped <b class="num">${fmtInt(d.skipped)}</b> <span class="muted">too little own code</span></span>` +
        (d.truncated ? `<span class="chip warn">Only the 600 most recent submitters were compared</span>` : "") + `</div>` +
        `<details class="src method"><summary>How this works</summary><p class="muted">${esc(d.method || "")}</p>
          <p class="muted">Each student's latest full submission is split into tokens (names, numbers, operators; comments, whitespace and string contents ignored), then into overlapping runs of 5 tokens.
          Runs that also appear in the official starter.py are removed. The score is shared runs ÷ all distinct runs of the two files. Renaming variables lowers it; reordering whole functions doesn't. Pairs under 20% aren't listed.</p></details>`;
      if (!pairs.length) {
        $("si-pairs").innerHTML = `<div class="panel empty"><span class="big" aria-hidden="true">✓</span>No pair is 20% similar or more.</div>`;
        return;
      }
      $("si-pairs").innerHTML = pairs.map((p, i) => {
        const pct = Math.round(100 * p.similarity);
        const lvl = pct >= 70 ? "high" : pct >= 45 ? "mid" : "low";
        const on = this.current === i;
        return `<div role="listitem"><button type="button" class="sim-pair ${lvl}${on ? " on" : ""}" data-pair="${i}" aria-pressed="${on}" aria-label="${esc(p.a.username)} and ${esc(p.b.username)}: ${pct}% similar. Compare side by side.">
          <span class="sim-names"><b>${esc(p.a.username)}</b> <span class="muted" aria-hidden="true">↔</span> <b>${esc(p.b.username)}</b></span>
          <span class="sim-bar" aria-hidden="true"><i style="width:${pct}%"></i></span>
          <span class="sim-pct num">${pct}%</span>
          <span class="sim-sub muted">${fmtInt(p.shared)} shared · #${esc(p.a.submission_id)} (${fmtInt(p.a_size)}) / #${esc(p.b.submission_id)} (${fmtInt(p.b_size)})</span></button></div>`;
      }).join("");
    },
    async getSrc(id) {
      if (this.src.has(id)) return this.src.get(id);
      const d = await API.submission(id);
      this.src.set(id, d);
      return d;
    },
    async starterLines() {
      if (this.starter) return this.starter;
      try {
        const r = await fetch("/downloads/starter.py", { credentials: "same-origin" });
        const t = r.ok ? await r.text() : "";
        this.starter = new Set(t.split("\n").map(normLine).filter((n) => !trivialLine(n)));
      } catch (e) { this.starter = new Set(); }
      return this.starter;
    },
    async compare(i) {
      const p = this.data.pairs[i];
      this.current = i;
      document.querySelectorAll("#si-pairs .sim-pair").forEach((b) => { const on = Number(b.dataset.pair) === i; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false"); });
      const box = $("si-compare");
      box.classList.remove("hidden");
      box.innerHTML = `<div class="panel"><span class="skel" style="width:60%"></span><br><span class="skel" style="width:80%"></span></div>`;
      try {
        const [a, b, starter] = await Promise.all([this.getSrc(p.a.submission_id), this.getSrc(p.b.submission_id), this.starterLines()]);
        if (this.current !== i) return;
        const lines = (s) => String(s || "").replace(/\r\n?/g, "\n").split("\n");
        const la = lines(a.source), lb = lines(b.source);
        const key = (l) => { const n = normLine(l); return trivialLine(n) || starter.has(n) ? null : n; };
        const setA = new Set(la.map(key).filter(Boolean)), setB = new Set(lb.map(key).filter(Boolean));
        const hitsA = la.map((l) => { const k = key(l); return !!k && setB.has(k); });
        const hitsB = lb.map((l) => { const k = key(l); return !!k && setA.has(k); });
        const side = (who, d, hits) => {
          const n = hits.filter(Boolean).length, tot = d.source ? d.source.split("\n").filter((l) => l.trim()).length : 0;
          return `<div class="sim-side"><div class="sim-side-head"><span>${avatar(who.username, "sm")} <b>${esc(who.username)}</b> <span class="muted">#${esc(who.submission_id)} · ${esc(relTime(d.created))}</span></span>
            <span class="muted st-note"><b class="num">${n}</b> of ${tot} lines also in the other file</span></div>
            ${window.Code.view(d.source || "", { label: `Source of ${esc(who.username)}, submission ${who.submission_id}` })}</div>`;
        };
        const pct = Math.round(100 * p.similarity);
        box.innerHTML = `<div class="panel"><div class="adm-section-head sm"><h3 class="section-title" style="margin:0">${esc(p.a.username)} ↔ ${esc(p.b.username)} · ${pct}%</h3>
            <button type="button" class="btn sm ghost" id="si-close">Close comparison</button></div>
          <p class="muted st-note" style="margin:0 0 10px"><span class="hit-swatch" aria-hidden="true"></span> Highlighted lines appear in both files (whitespace ignored; blank, trivial and starter.py lines skipped).</p>
          <div class="sim-sides">${side(p.a, a, hitsA)}${side(p.b, b, hitsB)}</div></div>`;
        const views = box.querySelectorAll(".codeview");
        [hitsA, hitsB].forEach((hits, k) => {
          const lns = views[k] ? views[k].querySelectorAll(".ln") : [];
          lns.forEach((ln, idx) => { if (hits[idx]) ln.classList.add("hit"); });
        });
        box.scrollIntoView({ behavior: UI.reduced() ? "auto" : "smooth", block: "start" });
        box.focus({ preventScroll: true });
      } catch (e) {
        if (authLost(e)) return;
        box.innerHTML = `<div class="err">${esc(e.message)}</div>`;
      }
    },
    close() {
      const i = this.current;
      this.current = null;
      $("si-compare").classList.add("hidden");
      $("si-compare").innerHTML = "";
      const b = document.querySelector(`#si-pairs [data-pair="${i}"]`);
      document.querySelectorAll("#si-pairs .sim-pair").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
      if (b) b.focus();
    },
    wire() {
      $("si-run").addEventListener("click", () => { this.current = null; $("si-compare").classList.add("hidden"); this.src.clear(); this.run(); });
      $("si-pairs").addEventListener("click", (ev) => { const b = ev.target.closest("[data-pair]"); if (b) this.compare(Number(b.dataset.pair)); });
      $("si-compare").addEventListener("click", (ev) => { if (ev.target.closest("#si-close")) this.close(); });
      $("si-compare").addEventListener("keydown", (ev) => { if (ev.key === "Escape" && this.current !== null) { ev.preventDefault(); this.close(); } });
    },
  };

  // ================================================================== ACTIVITY
  function auditDetails(a) {
    const d = a.details && typeof a.details === "object" ? a.details : {};
    const keys = Object.keys(d);
    if (!keys.length) return `<span class="muted">–</span>`;
    const isPair = (v) => Array.isArray(v) && v.length === 2;
    if (a.action === "user.email" && ("old" in d || "new" in d)) {
      return `<span class="chg"><b>email</b> ${esc(d.old || "none")} <span class="arr" aria-label="changed to">→</span> ${esc(d.new || "none")}</span>`;
    }
    return keys.map((k) => {
      const v = d[k];
      if (isPair(v) && (a.action.startsWith("settings.") || a.action === "user.admin")) {
        return `<span class="chg"><b>${esc(LABELS[k] || k)}</b> ${esc(fmtSetting(k, v[0]))} <span class="arr" aria-label="changed to">→</span> ${esc(fmtSetting(k, v[1]))}</span>`;
      }
      if (Array.isArray(v)) return `<span class="chg"><b>${esc(k)}</b> ${v.length > 6 ? `${fmtInt(v.length)} items` : esc(v.join(", "))}</span>`;
      if (v === null || v === undefined || v === "") return `<span class="chg"><b>${esc(k)}</b> <span class="muted">none</span></span>`;
      if (typeof v === "number") return `<span class="chg"><b>${esc(k)}</b> ${k === "id" ? "#" + v : fmtInt(v)}</span>`;
      if (typeof v === "object") return `<span class="chg"><b>${esc(k)}</b> <span class="mono">${esc(JSON.stringify(v).slice(0, 120))}</span></span>`;
      return `<span class="chg"><b>${esc(k)}</b> ${esc(String(v).slice(0, 160))}</span>`;
    }).join("");
  }
  const activity = {
    rows: [], loaded: false, dirty: false, rowCache: new Map(),
    pollMs: () => 0,
    show(changed) { return changed || !this.loaded || this.dirty ? this.refresh(true) : null; },
    async refresh() {
      const p = { actor: $("au-actor").value.trim(), action: $("au-action").value, target: $("au-target").value.trim(), limit: 200 };
      try {
        const list = await API.adminAudit(p);
        this.rows = Array.isArray(list) ? list : [];
        this.loaded = true; this.dirty = false;
        this.render();
        $("au-updated").textContent = `${fmtInt(this.rows.length)} entr${this.rows.length === 1 ? "y" : "ies"}${this.rows.length >= 200 ? " (newest 200)" : ""} · updated ${new Date().toLocaleTimeString()}`;
      } catch (e) {
        if (authLost(e)) return;
        $("au-updated").textContent = "Couldn't load: " + e.message;
        document.querySelector("#au-table tbody").innerHTML = emptyRow(5, `<div class="err">${esc(e.message)}</div>`);
        throw e;
      }
    },
    render() {
      const tbody = document.querySelector("#au-table tbody");
      if (!this.rows.length) {
        tbody.innerHTML = emptyRow(5, "No admin actions match.");
        this.rowCache.clear();
        return;
      }
      patchRows(tbody, this.rows.map((a) => {
        const group = String(a.action || "").split(".")[0];
        return {
          key: "au-" + a.id, cls: "row",
          html: `<td class="nw">${when(a.created)}</td><td class="nw"><button type="button" class="linkish" data-f-actor="${esc(a.actor || "")}" title="Only actions by ${esc(a.actor || "")}">${esc(a.actor || "–")}</button></td>` +
            `<td class="nw"><span class="act-tag ${esc(group)}">${esc(a.action)}</span></td>` +
            `<td class="nw">${a.target ? `<button type="button" class="linkish" data-f-target="${esc(a.target)}" title="Only actions on ${esc(a.target)}">${esc(a.target)}</button>` : `<span class="muted">–</span>`}</td>` +
            `<td class="au-details">${auditDetails(a)}</td>`,
        };
      }), this.rowCache);
    },
    wire() {
      $("au-filters").addEventListener("submit", (ev) => { ev.preventDefault(); this.refresh().catch(() => {}); });
      $("au-action").addEventListener("change", () => $("au-filters").requestSubmit());
      $("au-reset").addEventListener("click", () => { $("au-actor").value = ""; $("au-action").value = ""; $("au-target").value = ""; this.refresh().catch(() => {}); });
      $("au-table").addEventListener("click", (ev) => {
        const a = ev.target.closest("[data-f-actor]"), t = ev.target.closest("[data-f-target]");
        if (a) { $("au-actor").value = a.dataset.fActor; this.refresh().catch(() => {}); }
        if (t) { $("au-target").value = t.dataset.fTarget; this.refresh().catch(() => {}); }
      });
    },
  };

  const sections = { overview, students, submissions: submissionsSec, settings, leaderboard, similarity, activity };

  // ------------------------------------------------------------------ boot / auth
  function showDenied(me) {
    clearTimeout(st.timer);
    st.booted = false;
    $("adm-loading").classList.add("hidden");
    $("adm-app").classList.add("hidden");
    $("adm-denied").classList.remove("hidden");
    const d = $("drawer"); if (d.open) d.close();
    setLive("off", me ? "Not an admin account" : "Signed out");
    if (me) {
      $("adm-denied-text").innerHTML = `You're signed in as <b>${esc(me.username)}</b>, which isn't an admin account. If you're an organiser, ask an admin to make you one.`;
      $("adm-denied-actions").innerHTML = `<a class="btn primary" href="/contest/">Go to the contest hub</a><a class="btn" href="/contest/leaderboard/">Leaderboard</a>`;
    } else {
      $("adm-denied-text").textContent = "This page is for the contest organisers. Sign in with an admin account to use it.";
      $("adm-denied-actions").innerHTML = `<a class="btn primary" href="/contest/#signin">Sign in</a><a class="btn" href="/">Back to the home page</a>`;
    }
  }
  function showApp() {
    $("adm-loading").classList.add("hidden");
    $("adm-denied").classList.add("hidden");
    $("adm-app").classList.remove("hidden");
    st.booted = true;
    const h = location.hash.slice(1);
    st.tab = null;
    selectTab(SECTIONS.includes(h) ? h : "overview", { hash: SECTIONS.includes(h), scrollTab: true });
  }
  async function boot(me) {
    if (me === undefined) { try { me = await API.me(); } catch (e) { me = null; } }
    st.me = me;
    if (!me || !me.is_admin) { showDenied(me); return; }
    showApp();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!window.Code) return;
    wireTabs();
    students.wire(); drawer.wire(); submissionsSec.wire(); settings.wire(); leaderboard.wire(); similarity.wire(); activity.wire();
    wireChartTips($("ov-chart-reg")); wireChartTips($("ov-chart-sub"));
    document.addEventListener("auth-change", (ev) => { students.loaded = false; submissionsSec.loaded = false; settings.loaded = false; boot(ev.detail || null); });
    window.addEventListener("beforeunload", (ev) => {
      if (st.booted && settings.loaded && settings.dirtyKeys().length) { ev.preventDefault(); ev.returnValue = ""; }
    });
    boot();
  });
})();

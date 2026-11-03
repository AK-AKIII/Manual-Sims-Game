/* Contest hub: status + countdown, sign in / register, submit (drag-and-drop or
 * paste, with a highlighted editor), my submissions with live polling and
 * inline details, and the admin "all submissions" table with rejudge and delete
 * (the full admin panel is /admin/). */
(function () {
  "use strict";
  const { esc, fmtInt, fmtScore, fmtSecs, relTime, fmtDate, toast, countUp, parseTime } = window.UI;
  const API = window.API;
  const $ = (id) => document.getElementById(id);
  const POLL_MS = 3000;
  const DEFAULT_MAX_KB = 256;

  const st = {
    contest: null, me: null,
    mine: [], all: [],
    open: { mine: new Set(), all: new Set() },
    srcOpen: new Set(),
    details: new Map(),       // id -> detail response
    detailErr: new Map(),     // id -> message
    rowHTML: new Map(),       // dom id -> last html (keyed updates)
    prevStatus: new Map(),    // id -> status at the last poll (for toasts)
    pollTimer: null, lastPoll: 0, tab: "submit",
  };

  // ------------------------------------------------------------------ contest status
  function seedsText(ps) {
    if (!Array.isArray(ps) || !ps.length) return "–";
    const n = ps.map(Number);
    const contiguous = n.every((x, i) => i === 0 || x === n[i - 1] + 1);
    return contiguous && n.length > 2 ? `${n[0]}–${n[n.length - 1]}` : n.join(", ");
  }
  const maxKB = () => (st.contest && Number(st.contest.max_source_kb)) || DEFAULT_MAX_KB;

  function renderContest() {
    const c = st.contest;
    if (c.name) { $("c-name").textContent = c.name; }
    const fills = {
      public_seeds: seedsText(c.public_seeds),
      hidden_count: c.hidden_count !== undefined ? `${c.hidden_count} hidden` : "the hidden",
      full_per_hour: c.full_per_hour !== undefined ? `${c.full_per_hour}` : "a few",
    };
    document.querySelectorAll("[data-c]").forEach((el) => {
      const k = el.dataset.c;
      if (k === "full_per_hour" && c.full_per_hour !== undefined) el.textContent = `${c.full_per_hour}`;
      else if (fills[k] !== undefined) el.textContent = fills[k];
    });
    const chips = [
      ["Time", `${c.time_limit} s / test`],
      ["Public", `seeds ${seedsText(c.public_seeds)}`],
      ["Hidden", `${c.hidden_count} tests`],
      ["Full runs", `${c.full_per_hour} / hour`],
    ];
    $("st-limits").innerHTML = chips.map(([k, v]) => `<span class="chip">${esc(k)} <b>${esc(v)}</b></span>`).join("");
    $("limits-list").innerHTML = [
      ["Time per test", `${c.time_limit} s`],
      ["Public seeds", seedsText(c.public_seeds)],
      ["Hidden tests", fmtInt(c.hidden_count)],
      ["Full runs", `${c.full_per_hour} per hour`],
      ["Max file size", `${maxKB()} KB`],
      ["In the queue", "one at a time"],
    ].map(([k, v]) => `<li><span>${esc(k)}</span><b>${esc(v)}</b></li>`).join("");
    tickClock();
  }

  let lastSr = "";
  function tickClock() {
    const c = st.contest;
    if (!c) return;
    const p = window.UI.contestPhase(c);
    const cd = $("st-countdown");
    let label, dot;
    if (p.phase === "upcoming") { label = "Starts soon"; dot = "warn"; }
    else if (p.phase === "open") { label = c.open === false ? "Submissions paused" : "Contest open"; dot = c.open === false ? "warn" : ""; }
    else if (p.phase === "ended") { label = "Contest over"; dot = "off"; }
    else { label = c.open === false ? "Submissions closed" : "Contest open"; dot = c.open === false ? "off" : ""; }
    $("st-label").textContent = label;
    $("st-dot").className = "dot-live" + (dot ? " " + dot : "");
    if (p.until) {
      const left = p.until - Date.now() - p.offset;
      const x = window.UI.splitDuration(left);
      const units = [["d", x.d], ["h", x.h], ["m", x.m], ["s", x.s]].filter((u, i) => i > 0 || u[1] > 0);
      const head = `<span class="muted" style="align-self:center;margin-right:4px;font-size:14px">${p.phase === "upcoming" ? "starts in" : "ends in"}</span>`;
      const html = head + units.map(([u, v]) => `<span class="u"><b>${String(v).padStart(2, "0")}</b><span>${u === "d" ? "days" : u === "h" ? "hrs" : u === "m" ? "min" : "sec"}</span></span>`).join("");
      cd.innerHTML = html;
      cd.classList.remove("hidden");
      const sr = `${label}. ${p.phase === "upcoming" ? "Starts" : "Ends"} ${fmtDate(p.until)}.`;
      if (sr !== lastSr) { $("st-sr").textContent = sr; lastSr = sr; }
      if (left <= 0) setTimeout(loadContest, 1500);
    } else {
      cd.innerHTML = p.phase === "ended" ? `<span class="muted">Ended ${esc(fmtDate(c.end))}</span>` : "";
    }
    const closed = c.open === false;
    const fullOpt = document.querySelector('input[name=kind][value=full]');
    if (fullOpt) {
      const box = fullOpt.parentElement.querySelector(".box span");
      fullOpt.parentElement.title = closed ? "The contest is closed for full submissions; public runs still work." : "";
      if (closed && box && !box.dataset.closed) { box.dataset.closed = "1"; box.insertAdjacentHTML("beforeend", ' <b style="display:inline;color:var(--amber);font-size:13px">Closed right now.</b>'); }
    }
  }

  async function loadContest() {
    try {
      st.contest = await API.contest(st.contest !== null); // the first read shares site.js's request
      renderContest();
      $("rg-invite-field").classList.toggle("hidden", !st.contest.invite_required);
      $("rg-invite").required = !!st.contest.invite_required;
    } catch (e) {
      $("st-label").textContent = "Contest status unavailable";
      $("st-limits").innerHTML = `<span class="err" style="padding:4px 10px">${esc(e.message)}</span>`;
    }
  }

  // ------------------------------------------------------------------ views
  function show(view) {
    $("view-loading").classList.add("hidden");
    $("view-auth").classList.toggle("hidden", view !== "auth");
    $("view-app").classList.toggle("hidden", view !== "app");
  }

  async function boot() {
    await loadContest();
    setInterval(tickClock, 1000);
    try { st.me = await API.me(); } catch (e) { st.me = null; }
    route();
  }

  function route() {
    if (!st.me) {
      stopPolling();
      show("auth");
      if (location.hash === "#register") selectAuth("register");
      else if (location.hash === "#signin") { selectAuth("login"); $("li-user").focus(); }
      return;
    }
    show("app");
    $("t-all").classList.toggle("hidden", !st.me.is_admin);
    renderAccount();
    const h = location.hash.replace("#", "");
    const m = /^sub-(\d+)$/.exec(h);
    if (m) { st.open.mine.add(Number(m[1])); selectTab("mine", false); }
    else selectTab(h === "mine" || h === "account" || (h === "all" && st.me.is_admin) ? h : "submit", false);
    refreshMine();
    if (st.me.is_admin) refreshAll();
  }

  document.addEventListener("auth-change", (ev) => {
    st.me = ev.detail;
    st.mine = []; st.all = []; st.details.clear(); st.rowHTML.clear(); st.prevStatus.clear();
    document.querySelector("#mine-table tbody").innerHTML = "";
    document.querySelector("#all-table tbody").innerHTML = "";
    route();
  });

  // ------------------------------------------------------------------ tabs (keyboard: arrows, Home, End)
  function wireTabs(list, onSelect) {
    list.addEventListener("click", (ev) => {
      const b = ev.target.closest("[role=tab]");
      if (b) onSelect(b);
    });
    list.addEventListener("keydown", (ev) => {
      const tabs = Array.from(list.querySelectorAll("[role=tab]:not(.hidden)"));
      const i = tabs.indexOf(document.activeElement);
      if (i < 0) return;
      let j = null;
      if (ev.key === "ArrowRight") j = (i + 1) % tabs.length;
      else if (ev.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      else if (ev.key === "Home") j = 0;
      else if (ev.key === "End") j = tabs.length - 1;
      if (j === null) return;
      ev.preventDefault();
      tabs[j].focus();
      onSelect(tabs[j]);
    });
  }

  function setSelected(list, btn) {
    list.querySelectorAll("[role=tab]").forEach((t) => {
      const on = t === btn;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
    });
  }

  function selectAuth(which) {
    const btn = $(which === "register" ? "tab-register" : "tab-login");
    setSelected(btn.parentElement, btn);
    $("form-login").classList.toggle("hidden", which === "register");
    $("form-register").classList.toggle("hidden", which !== "register");
  }

  function selectTab(name, updateHash) {
    st.tab = name;
    const btn = $("t-" + name);
    setSelected(btn.parentElement, btn);
    ["submit", "mine", "account", "all"].forEach((n) => $("p-" + n).classList.toggle("hidden", n !== name));
    if (updateHash !== false) { try { history.replaceState(null, "", "#" + name); } catch (e) { /* ignore */ } }
    if (name === "mine") refreshMine();
    if (name === "all") refreshAll();
  }

  // ------------------------------------------------------------------ auth forms
  function fieldErr(input, msgEl, text) {
    input.setAttribute("aria-invalid", text ? "true" : "false");
    msgEl.innerHTML = text ? `<div class="err">${esc(text)}</div>` : "";
    if (text) input.focus();
    return !text;
  }

  function busyBtn(form, on) {
    const b = form.querySelector("button[type=submit]");
    b.disabled = on; b.classList.toggle("busy", on);
    b.setAttribute("aria-busy", on ? "true" : "false");
  }

  async function onLogin(ev) {
    ev.preventDefault();
    const f = ev.target, msg = $("li-msg");
    const u = $("li-user").value.trim(), p = $("li-pass").value;
    $("li-user").setAttribute("aria-invalid", "false"); $("li-pass").setAttribute("aria-invalid", "false");
    if (!u) return fieldErr($("li-user"), msg, "Enter your username or email.");
    if (!p) return fieldErr($("li-pass"), msg, "Enter your password.");
    msg.innerHTML = "";
    busyBtn(f, true);
    try {
      const me = await API.login(u, p);
      toast("ok", `Welcome back, ${me.username}.`);
      $("li-pass").value = "";
      document.dispatchEvent(new CustomEvent("auth-change", { detail: me }));
    } catch (e) {
      msg.innerHTML = `<div class="err">${esc(e.message)}</div>`;
      if (window.Sound) window.Sound.error();
      if (e.status === 401) $("li-pass").select();
    } finally { busyBtn(f, false); }
  }

  const USER_RE = /^[A-Za-z0-9_-]{3,20}$/;
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const validEmail = (v) => v.length <= 254 && EMAIL_RE.test(v);
  async function onRegister(ev) {
    ev.preventDefault();
    const f = ev.target, msg = $("rg-msg");
    const u = $("rg-user").value.trim(), p = $("rg-pass").value, inv = $("rg-invite").value.trim(), em = $("rg-email").value.trim();
    ["rg-user", "rg-email", "rg-pass", "rg-invite"].forEach((id) => $(id).setAttribute("aria-invalid", "false"));
    if (!USER_RE.test(u)) return fieldErr($("rg-user"), msg, "Usernames are 3–20 characters: letters, digits, _ or -.");
    if (!validEmail(em)) return fieldErr($("rg-email"), msg, "Enter a valid email address, like name@example.com.");
    if (p.length < 8) return fieldErr($("rg-pass"), msg, "Passwords need at least 8 characters.");
    if (st.contest && st.contest.invite_required && !inv) return fieldErr($("rg-invite"), msg, "This contest needs an invite code.");
    msg.innerHTML = "";
    busyBtn(f, true);
    try {
      const me = await API.register(u, p, inv, em);
      toast("ok", `Account created. Welcome, ${me.username}!`);
      $("rg-pass").value = "";
      document.dispatchEvent(new CustomEvent("auth-change", { detail: me }));
    } catch (e) {
      msg.innerHTML = `<div class="err">${esc(e.message)}</div>`;
      if (window.Sound) window.Sound.error();
      if (e.status === 409) $(/email/i.test(e.message) ? "rg-email" : "rg-user").setAttribute("aria-invalid", "true");
      if (e.status === 400 && /email/i.test(e.message)) $("rg-email").setAttribute("aria-invalid", "true");
      if (e.status === 403) $("rg-invite").setAttribute("aria-invalid", "true");
    } finally { busyBtn(f, false); }
  }

  // ------------------------------------------------------------------ account (email)
  function renderAccount() {
    if (!st.me) return;
    const em = st.me.email || null;
    $("acct-facts").innerHTML = [["Username", st.me.username], ["Email", em || "no email yet — add one"], ["Role", st.me.is_admin ? "admin" : "player"]]
      .map(([k, v]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v acct-v${k === "Email" && !em ? " muted" : ""}">${esc(v)}</div></div>`).join("");
    $("acct-nudge").hidden = !!em;
    $("account-dot").classList.toggle("hidden", !!em);
    $("account-dot-sr").classList.toggle("hidden", !!em);
    $("email-form-title").textContent = em ? "Change your email" : "Add an email";
    if (document.activeElement !== $("ac-email")) $("ac-email").value = em || "";
  }

  async function onEmail(ev) {
    ev.preventDefault();
    const f = ev.target, msg = $("ac-msg");
    const em = $("ac-email").value.trim(), pw = $("ac-pass").value;
    ["ac-email", "ac-pass"].forEach((id) => $(id).setAttribute("aria-invalid", "false"));
    if (!validEmail(em)) return fieldErr($("ac-email"), msg, "Enter a valid email address, like name@example.com.");
    if (!pw) return fieldErr($("ac-pass"), msg, "Enter your current password.");
    msg.innerHTML = "";
    busyBtn(f, true);
    try {
      const me = await API.changeEmail(em, pw);
      st.me = Object.assign({}, st.me, me);
      $("ac-pass").value = "";
      renderAccount();
      toast("ok", `Email saved: ${me.email || em}.`);
    } catch (e) {
      msg.innerHTML = `<div class="err">${esc(e.message)}</div>`;
      if (window.Sound) window.Sound.error();
      if (e.status === 401) { $("ac-pass").setAttribute("aria-invalid", "true"); $("ac-pass").select(); }
      else $("ac-email").setAttribute("aria-invalid", "true");
    } finally { busyBtn(f, false); }
  }

  // ------------------------------------------------------------------ submit
  let ed = null;
  function srcStats() {
    const v = ed ? ed.get() : "";
    const bytes = new Blob([v]).size;
    const lines = v ? v.split("\n").length : 0;
    const over = bytes > maxKB() * 1024;
    $("src-stats").innerHTML = v
      ? `${fmtInt(lines)} line${lines === 1 ? "" : "s"} · <span style="color:${over ? "var(--amber)" : "inherit"}">${(bytes / 1024).toFixed(1)} KB</span> / ${maxKB()} KB`
      : "empty";
  }

  function readFile(file) {
    if (!file) return;
    if (file.size > maxKB() * 1024) {
      $("submit-msg").innerHTML = `<div class="err">${esc(file.name)} is ${(file.size / 1024).toFixed(1)} KB; the limit is ${maxKB()} KB.</div>`;
      if (window.Sound) window.Sound.error();
      return;
    }
    const r = new FileReader();
    r.onload = () => {
      ed.set(String(r.result));
      $("dz-title").textContent = file.name;
      $("dz-sub").textContent = `${(file.size / 1024).toFixed(1)} KB loaded. Check it below, then submit. Drop another file to replace it.`;
      $("submit-msg").innerHTML = "";
      toast("ok", `Loaded ${file.name}`, { silent: true });
      if (window.Sound) window.Sound.select();
    };
    r.onerror = () => { $("submit-msg").innerHTML = `<div class="err">Couldn't read ${esc(file.name)}.</div>`; };
    r.readAsText(file);
  }

  function wireDropzone() {
    const dz = $("dropzone"), input = $("file-input");
    input.addEventListener("change", () => { readFile(input.files[0]); input.value = ""; });
    let depth = 0;
    dz.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; dz.classList.add("over"); });
    dz.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
    dz.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) dz.classList.remove("over"); });
    dz.addEventListener("drop", (e) => {
      e.preventDefault(); depth = 0; dz.classList.remove("over");
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      readFile(f);
    });
    // dropping anywhere else on the page shouldn't navigate away
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => e.preventDefault());
  }

  async function onSubmit(ev) {
    ev.preventDefault();
    const msg = $("submit-msg");
    const kind = document.querySelector("input[name=kind]:checked").value;
    const src = ed.get();
    if (!src.trim()) { msg.innerHTML = `<div class="err">Paste your code or drop a .py file first.</div>`; if (window.Sound) window.Sound.error(); return; }
    if (new Blob([src]).size > maxKB() * 1024) { msg.innerHTML = `<div class="err">Your file is over the ${maxKB()} KB limit.</div>`; if (window.Sound) window.Sound.error(); return; }
    msg.innerHTML = "";
    const btn = $("submit-btn");
    btn.disabled = true; btn.classList.add("busy");
    try {
      const r = await API.submit(kind, src);
      toast("ok", `Submission #${r.id} is in the queue (${kind === "full" ? "full run" : "public seeds"}).`);
      st.open.mine.add(r.id);
      st.fresh = r.id;
      selectTab("mine");
    } catch (e) {
      msg.innerHTML = `<div class="err">${esc(e.message)}</div>`;
      if (window.Sound) window.Sound.error();
      if (e.status === 401) { API.forgetMe(); document.dispatchEvent(new CustomEvent("auth-change", { detail: null })); }
    } finally { btn.disabled = false; btn.classList.remove("busy"); }
  }

  // ------------------------------------------------------------------ submissions
  const ACTIVE = (s) => s.status === "queued" || s.status === "running";

  function badge(s) {
    const label = { queued: "queued", running: "judging", done: "done", error: "error" }[s.status] || s.status;
    return `<span class="badge ${esc(s.status)}">${esc(label)}</span>`;
  }

  function scoreCell(s) {
    if (s.status !== "done") return `<span class="muted">–</span>`;
    if (s.kind === "full" && s.score !== null && s.score !== undefined) {
      return `<b>${fmtScore(s.score)}</b><br><span class="muted" style="font-size:12px">raw ${fmtInt(s.raw_total)}</span>`;
    }
    return `<b>${fmtInt(s.raw_total)}</b>${s.kind === "public" ? `<br><span class="muted" style="font-size:12px">raw total</span>` : ""}`;
  }

  function testsCell(s) {
    const tot = s.tests_total;
    if (s.status === "running" && tot) {
      const done = s.tests_done || 0;
      return `${done}/${tot}<span class="mini-progress" aria-hidden="true"><i style="width:${(100 * done) / tot}%"></i></span>`;
    }
    if (s.status === "queued") return `<span class="muted">${s.position ? "#" + s.position + " in line" : "waiting"}</span>`;
    if (tot === null || tot === undefined) return "–";
    const all = s.tests_ac === tot;
    return `<span style="color:${all ? "var(--ok)" : "inherit"}">${fmtInt(s.tests_ac)}</span><span class="muted">/${fmtInt(tot)}</span>`;
  }

  function rowHTML(s, which) {
    const open = st.open[which].has(s.id);
    const ctl = `${which}-d-${s.id}`;
    const exp = `<button type="button" class="expander" aria-expanded="${open}" aria-controls="${ctl}" data-exp="${s.id}"><span class="chev" aria-hidden="true">▶</span>#${s.id}</button>`;
    const kind = `<span class="kind ${s.kind === "full" ? "full" : ""}">${s.kind === "full" ? "full" : "public"}</span>`;
    const when = `<span title="${esc(fmtDate(s.created))}">${esc(relTime(s.created))}</span>`;
    if (which === "all") {
      return `<td>${exp}</td><td class="user">${esc(s.username || "?")}</td><td class="opt">${kind}</td><td>${badge(s)}</td><td class="r">${scoreCell(s)}</td>` +
        `<td class="r opt">${testsCell(s)}</td><td class="r opt">${when}</td>` +
        `<td class="r nw"><button type="button" class="btn sm" data-rejudge="${s.id}" ${ACTIVE(s) ? "disabled" : ""} aria-label="Rejudge submission ${s.id}">Rejudge</button> ` +
        `<button type="button" class="btn sm danger" data-delete="${s.id}" ${s.status === "running" ? 'disabled title="Running submissions can\'t be deleted; wait for it to finish."' : ""} aria-label="Delete submission ${s.id}">Delete</button></td>`;
    }
    return `<td>${exp}</td><td>${kind}</td><td>${badge(s)}</td><td class="r">${scoreCell(s)}</td>` +
      `<td class="r opt nw">${testsCell(s)}</td><td class="r opt nw">${fmtSecs(s.max_time)}</td><td class="r opt">${when}</td>`;
  }

  function judgeHTML(s, d) {
    const tot = (d && d.tests_total) || s.tests_total;
    if (s.status === "queued") {
      const pos = (d && d.position) || s.position;
      return `<div class="judge"><div class="top"><span><b>Waiting in the queue</b>${pos ? ` · position ${pos}` : ""}</span><span class="muted">${tot ? `${tot} tests` : ""}</span></div>
        <div class="progress indet" role="progressbar" aria-label="Waiting in the queue" aria-valuetext="Queued${pos ? ", position " + pos : ""}"><div class="bar"></div></div></div>`;
    }
    if (s.status === "running") {
      const done = (d && d.tests_done !== undefined ? d.tests_done : s.tests_done);
      if (tot && done !== undefined && done !== null) {
        const pct = Math.min(100, (100 * done) / tot);
        return `<div class="judge"><div class="top"><span><b>Judging test ${Math.min(done + 1, tot)} of ${tot}</b></span><span class="muted num">${Math.round(pct)}%</span></div>
          <div class="progress" role="progressbar" aria-label="Judging progress" aria-valuemin="0" aria-valuemax="${tot}" aria-valuenow="${done}"><div class="bar" style="width:${pct}%"></div></div></div>`;
      }
      return `<div class="judge"><div class="top"><b>Judging…</b></div><div class="progress indet"><div class="bar"></div></div></div>`;
    }
    return "";
  }

  function chipTip(r, kind) {
    const bits = [kind === "public" ? `Seed ${r.seed}` : `Test ${r.test}`, r.verdict];
    if (kind === "public" && r.verdict === "AC") bits.push(`${fmtInt(r.score)} pts`, `cleared ${r.cleared}/8`);
    if (r.time !== null && r.time !== undefined) bits.push(fmtSecs(r.time));
    return bits.join(" · ");
  }

  function pips(n) {
    let h = "";
    for (let i = 0; i < 8; i++) h += `<i class="${i < n ? "on" : ""}"></i>`;
    return `<span class="cleared-pips" aria-label="${n} of 8 rounds cleared">${h}</span>`;
  }

  function detailHTML(s, which) {
    const d = st.details.get(s.id);
    const err = st.detailErr.get(s.id);
    if (err) return `<div class="err">${esc(err)}</div>`;
    if (!d) return `<div><span class="skel" style="width:60%"></span><br><span class="skel" style="width:40%"></span></div>`;
    const kind = d.kind;
    const results = Array.isArray(d.results) ? d.results.slice() : [];
    results.sort((a, b) => (kind === "public" ? a.seed - b.seed : a.test - b.test));
    const tot = d.tests_total || s.tests_total || results.length;
    let out = judgeHTML(s, d);
    if (d.status === "error" || d.error) out += `<div class="err"><b>Judge error.</b> ${esc(d.error || "The submission couldn't be judged.")}</div>`;

    const stats = [];
    if (d.status === "done") {
      if (kind === "full") stats.push(["Score (0–100)", d.score, "score"]);
      stats.push(["Raw total", d.raw_total, true]);
      stats.push(["Tests passed", `${fmtInt(d.tests_ac)} / ${fmtInt(tot)}`]);
      stats.push(["Slowest test", fmtSecs(d.max_time)]);
    }
    stats.push(["Submitted", fmtDate(d.created)]);
    if (d.finished) stats.push(["Finished", fmtDate(d.finished)]);
    if (which === "all" && d.username) stats.unshift(["User", d.username]);
    out += `<div class="detail-stats">${stats.map(([k, v, n]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v" style="font-size:18px"${n ? ` data-count="${esc(v)}"${n === "score" ? ` data-fmt="score"` : ""}` : ""}>${n === "score" ? fmtScore(v) : n ? fmtInt(v) : esc(v)}</div></div>`).join("")}</div>`;

    // verdict chips
    if (tot) {
      const chips = [];
      for (const r of results) {
        const label = kind === "public" ? r.seed : r.test;
        chips.push(`<span class="v ${esc(r.verdict)}" tabindex="0" role="listitem" data-tip="${esc(chipTip(r, kind))}" aria-label="${esc(chipTip(r, kind))}">${esc(label)}</span>`);
      }
      for (let i = results.length; i < tot; i++) chips.push(`<span class="v pending" role="listitem" aria-label="not judged yet">·</span>`);
      out += `<div><div class="section-title" style="margin-bottom:8px">${kind === "public" ? "Seeds" : "Tests"} <span class="legend" style="display:inline-flex;margin-left:10px;text-transform:none;letter-spacing:0;font-weight:500">
        <span><span class="v AC">AC</span> valid</span><span><span class="v WA">WA</span> invalid output</span><span><span class="v TLE">TLE</span> too slow</span><span><span class="v RE">RE</span> crashed</span></span></div>
        <div class="vgrid" role="list" aria-label="Per-test verdicts">${chips.join("")}</div>
        ${kind === "full" ? `<p class="muted" style="font-size:13px;margin:8px 0 0">Hidden tests are anonymised: no seeds or per-test scores.</p>` : ""}</div>`;
    }

    // per-seed table for public runs
    if (kind === "public" && results.length) {
      out += `<div class="table-scroll" style="background:transparent"><table class="data">
        <thead><tr><th scope="col">Seed</th><th scope="col">Verdict</th><th scope="col" class="r">Score</th><th scope="col">Cleared</th><th scope="col" class="r">Time</th><th scope="col" class="opt">Message</th></tr></thead><tbody>` +
        results.map((r) => `<tr><td class="mono">${esc(r.seed)}</td><td><span class="v ${esc(r.verdict)}" style="height:24px">${esc(r.verdict)}</span></td>` +
          `<td class="r">${fmtInt(r.score)}</td><td class="nw">${pips(Number(r.cleared) || 0)} <span class="muted" style="font-size:12px">${esc(r.cleared)}/8</span></td>` +
          `<td class="r nw">${fmtSecs(r.time)}</td><td class="opt muted" style="font-size:13px;max-width:320px;overflow-wrap:anywhere">${esc(r.message || "")}</td></tr>`).join("") +
        `</tbody></table></div>`;
    }

    if (typeof d.source === "string") {
      const lines = d.source.split("\n").length;
      out += `<details class="src" data-src="${s.id}"${st.srcOpen.has(s.id) ? " open" : ""}><summary>Source code · ${fmtInt(lines)} lines</summary>
        ${st.srcOpen.has(s.id) ? window.Code.view(d.source, { label: `Source of submission ${s.id}` }) : ""}
        <div class="btn-row" style="margin-top:8px"><button type="button" class="btn sm" data-reuse="${s.id}">Open in editor</button></div></details>`;
    }
    return out;
  }

  function patchTable(which) {
    const list = which === "mine" ? st.mine : st.all;
    const tbody = document.querySelector(`#${which}-table tbody`);
    const cols = which === "mine" ? 7 : 8;
    if (!list.length) {
      tbody.innerHTML = `<tr><td colspan="${cols}"><div class="empty"><span class="big" aria-hidden="true">♠︎</span>${which === "mine"
        ? `No submissions yet. <a href="#submit" data-goto="submit">Submit your first solver</a>.` : "Nobody has submitted yet."}</div></td></tr>`;
      st.rowHTML.forEach((_, k) => { if (k.startsWith(which + "-")) st.rowHTML.delete(k); });
      return;
    }
    const active = document.activeElement;
    const focusKey = active && active.closest && tbody.contains(active) ? (active.dataset.exp ? "exp" + active.dataset.exp : active.dataset.rejudge ? "rj" + active.dataset.rejudge : active.dataset.delete ? "dl" + active.dataset.delete : null) : null;
    const want = [];
    for (const s of list) {
      want.push({ key: `${which}-r-${s.id}`, html: rowHTML(s, which), cls: "row" + (st.open[which].has(s.id) ? " open" : ""), s, detail: false });
      if (st.open[which].has(s.id)) want.push({ key: `${which}-d-${s.id}`, html: `<td colspan="${cols}"><div class="detail-inner">${detailHTML(s, which)}</div></td>`, cls: "detail", s, detail: true });
    }
    const existing = new Map(Array.from(tbody.children).filter((tr) => tr.id).map((tr) => [tr.id, tr]));
    let prev = null;
    for (const w of want) {
      let tr = existing.get(w.key);
      if (!tr) {
        tr = document.createElement("tr");
        tr.id = w.key;
        st.rowHTML.delete(w.key);
        if (!w.detail && st.fresh === w.s.id) { tr.classList.add("fresh"); st.fresh = null; }
      }
      existing.delete(w.key);
      const keepFresh = tr.classList.contains("fresh");
      tr.className = w.cls + (keepFresh ? " fresh" : "");
      if (st.rowHTML.get(w.key) !== w.html) {
        if (w.detail && tr.firstChild) {
          // keep the open source view's scroll position across refreshes
          const cv = tr.querySelector(".codeview");
          const scroll = cv ? cv.scrollTop : 0;
          tr.innerHTML = w.html;
          const cv2 = tr.querySelector(".codeview");
          if (cv2) cv2.scrollTop = scroll;
          const inner = tr.querySelector(".detail-inner");
          if (inner) inner.style.animation = "none";
        } else tr.innerHTML = w.html;
        st.rowHTML.set(w.key, w.html);
        tr.querySelectorAll("[data-count]").forEach((el) => { el.dataset.v = "0"; countUp(el, Number(el.dataset.count), el.dataset.fmt === "score" ? { fmt: fmtScore } : undefined); });
      }
      const after = prev ? prev.nextSibling : tbody.firstChild;
      if (after !== tr) tbody.insertBefore(tr, after);
      prev = tr;
    }
    existing.forEach((tr) => { tr.remove(); st.rowHTML.delete(tr.id); });
    Array.from(tbody.children).forEach((tr) => { if (!tr.id) tr.remove(); });
    if (focusKey) {
      const el = focusKey.startsWith("exp") ? tbody.querySelector(`[data-exp="${focusKey.slice(3)}"]`)
        : focusKey.startsWith("dl") ? tbody.querySelector(`[data-delete="${focusKey.slice(2)}"]`) : tbody.querySelector(`[data-rejudge="${focusKey.slice(2)}"]`);
      if (el && el !== document.activeElement) el.focus({ preventScroll: true });
    }
  }

  async function loadDetail(id, which) {
    try {
      const d = await API.submission(id);
      st.details.set(id, d);
      st.detailErr.delete(id);
    } catch (e) {
      st.detailErr.set(id, e.message);
    }
    patchTable(which);
  }

  function noteTransitions(list) {
    for (const s of list) {
      const prev = st.prevStatus.get(s.id);
      if (prev && prev !== s.status && (prev === "queued" || prev === "running")) {
        if (s.status === "done") {
          const all = s.tests_ac === s.tests_total;
          const score = s.kind === "full" && s.score != null ? `score ${fmtScore(s.score)}` : `${fmtInt(s.raw_total)} points`;
          toast(all ? "ok" : "info", `Submission #${s.id} judged: ${fmtInt(s.tests_ac)}/${fmtInt(s.tests_total)} valid · ${score}.`);
          if (!all && window.Sound) window.Sound.tick();
        } else if (s.status === "error") toast("bad", `Submission #${s.id} failed: ${s.error || "judge error"}.`);
      }
      st.prevStatus.set(s.id, s.status);
    }
  }

  let mineBusy = false;
  async function refreshMine() {
    if (!st.me || mineBusy) return;
    mineBusy = true;
    try {
      const list = await API.submissions();
      st.mine = Array.isArray(list) ? list : [];
      noteTransitions(st.mine);
      $("mine-count").textContent = String(st.mine.length);
      const jobs = [];
      for (const s of st.mine) {
        if (!st.open.mine.has(s.id)) continue;
        const d = st.details.get(s.id);
        if (!d || d.status !== s.status || ACTIVE(s) || d.tests_done !== s.tests_done) jobs.push(loadDetail(s.id, "mine"));
      }
      patchTable("mine");
      await Promise.all(jobs);
      st.lastPoll = Date.now();
      schedulePoll();
    } catch (e) {
      if (e.status === 401) { API.forgetMe(); document.dispatchEvent(new CustomEvent("auth-change", { detail: null })); toast("info", "Your session ended. Please sign in again."); }
      else { $("mine-poll").textContent = "Couldn't refresh: " + e.message; schedulePoll(true); }
    } finally { mineBusy = false; }
  }

  let allBusy = false;
  async function refreshAll() {
    if (!st.me || !st.me.is_admin || allBusy) return;
    allBusy = true;
    try {
      const list = await API.adminSubmissions();
      st.all = Array.isArray(list) ? list : (list && Array.isArray(list.submissions) ? list.submissions : []);
      $("all-count").textContent = String(st.all.length);
      const jobs = [];
      for (const s of st.all) {
        if (!st.open.all.has(s.id)) continue;
        const d = st.details.get(s.id);
        if (!d || d.status !== s.status || ACTIVE(s)) jobs.push(loadDetail(s.id, "all"));
      }
      patchTable("all");
      await Promise.all(jobs);
      $("all-poll").textContent = `${st.all.length} submissions · updated ${new Date().toLocaleTimeString()}`;
      schedulePoll();
    } catch (e) {
      $("all-poll").textContent = "Couldn't load: " + e.message;
    } finally { allBusy = false; }
  }

  function schedulePoll(retry) {
    clearTimeout(st.pollTimer);
    const activeMine = st.mine.some(ACTIVE);
    const activeAll = st.tab === "all" && st.all.some(ACTIVE);
    const label = $("mine-poll");
    if (activeMine) label.innerHTML = `<span class="dot-live" aria-hidden="true" style="display:inline-block;margin-right:6px;vertical-align:1px"></span>Live: updating every ${POLL_MS / 1000} s while the judge works`;
    else if (!retry) label.textContent = st.mine.length ? `Up to date · ${new Date().toLocaleTimeString()}` : "";
    if (activeMine || activeAll || retry) {
      st.pollTimer = setTimeout(() => {
        if (document.hidden) { schedulePoll(true); return; }
        if (activeAll) refreshAll();
        refreshMine();
      }, retry ? POLL_MS * 2 : POLL_MS);
    }
  }
  function stopPolling() { clearTimeout(st.pollTimer); }

  function onTableClick(which) {
    return async (ev) => {
      const exp = ev.target.closest("[data-exp]");
      if (exp) {
        const id = Number(exp.dataset.exp);
        const set = st.open[which];
        if (set.has(id)) set.delete(id); else { set.add(id); st.details.delete(id); loadDetail(id, which); }
        patchTable(which);
        return;
      }
      const rj = ev.target.closest("[data-rejudge]");
      if (rj) {
        const id = Number(rj.dataset.rejudge);
        rj.disabled = true; rj.classList.add("busy");
        try {
          await API.rejudge(id);
          toast("ok", `Submission #${id} is queued for rejudging.`);
          st.details.delete(id);
          await refreshAll();
          refreshMine();
        } catch (e) {
          toast("bad", e.message);
          rj.disabled = false; rj.classList.remove("busy");
        }
        return;
      }
      const del = ev.target.closest("[data-delete]");
      if (del) {
        const id = Number(del.dataset.delete);
        const s = st.all.find((x) => x.id === id);
        const ok = await window.UI.dialog({
          title: `Delete submission #${id}?`, danger: true, confirmLabel: "Delete submission",
          html: `<p>This permanently removes the source and results of <b>#${id}</b>${s && s.username ? ` by <b>${esc(s.username)}</b>` : ""}. Scores are recomputed on the next read. This can't be undone.</p>`,
          onConfirm: () => API.adminDeleteSubmission(id),
          returnFocus: () => { const b = $("all-refresh"); if (b) b.focus(); },
        });
        if (ok) {
          toast("ok", `Submission #${id} deleted.`);
          st.open.all.delete(id); st.open.mine.delete(id); st.details.delete(id);
          await refreshAll();
          refreshMine();
        }
        return;
      }
      const reuse = ev.target.closest("[data-reuse]");
      if (reuse) {
        const d = st.details.get(Number(reuse.dataset.reuse));
        if (d && typeof d.source === "string") {
          ed.set(d.source);
          $("dz-title").textContent = `Source of #${d.id}`;
          $("dz-sub").textContent = "Loaded from a past submission. Edit it below, then submit again.";
          selectTab("submit");
          $("source").focus();
          toast("info", `Loaded submission #${d.id} into the editor.`, { silent: true });
        }
        return;
      }
      const go = ev.target.closest("[data-goto]");
      if (go) { ev.preventDefault(); selectTab(go.dataset.goto); }
    };
  }

  function onSrcToggle(which) {
    return (ev) => {
      const det = ev.target;
      if (!det.matches || !det.matches("details[data-src]")) return;
      const id = Number(det.dataset.src);
      if (det.open) st.srcOpen.add(id); else st.srcOpen.delete(id);
      patchTable(which);
    };
  }

  // ------------------------------------------------------------------ wire up
  document.addEventListener("DOMContentLoaded", () => {
    if (!API || !window.Code) return;
    $("form-login").addEventListener("submit", onLogin);
    $("form-register").addEventListener("submit", onRegister);
    $("email-form").addEventListener("submit", onEmail);
    wireTabs(document.querySelector("#view-auth [role=tablist]"), (b) => selectAuth(b.id === "tab-register" ? "register" : "login"));
    wireTabs(document.querySelector("#view-app [role=tablist]"), (b) => selectTab(b.id.slice(2)));
    $("rg-user").addEventListener("input", () => {
      const v = $("rg-user").value.trim();
      $("rg-user-note").textContent = v && !USER_RE.test(v) ? "Use 3–20 letters, digits, _ or -." : "";
    });

    ed = window.Code.editor($("editor"));
    $("source").addEventListener("input", srcStats);
    srcStats();
    wireDropzone();
    $("submit-form").addEventListener("submit", onSubmit);
    $("clear-src").addEventListener("click", () => {
      ed.set(""); $("dz-title").textContent = "Drop your .py file here";
      $("dz-sub").textContent = "or click to choose a file. It's read in your browser and shown below.";
      $("source").focus();
    });
    $("load-starter").addEventListener("click", async (ev) => {
      const b = ev.currentTarget;
      b.classList.add("busy"); b.disabled = true;
      try {
        const r = await fetch("/downloads/starter.py", { credentials: "same-origin" });
        if (!r.ok) throw new Error("starter.py is not available (" + r.status + ")");
        ed.set(await r.text());
        $("dz-title").textContent = "starter.py";
        $("dz-sub").textContent = "The starter is loaded. Fill in solve(), or submit it as-is to see the pipeline work.";
      } catch (e) { $("submit-msg").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
      finally { b.classList.remove("busy"); b.disabled = false; }
    });
    $("mine-refresh").addEventListener("click", refreshMine);
    $("all-refresh").addEventListener("click", refreshAll);
    for (const which of ["mine", "all"]) {
      const t = document.querySelector(`#${which}-table`);
      t.addEventListener("click", onTableClick(which));
      t.addEventListener("toggle", onSrcToggle(which), true);
    }
    document.addEventListener("visibilitychange", () => { if (!document.hidden && st.me && st.mine.some(ACTIVE)) refreshMine(); });
    window.addEventListener("hashchange", () => {
      if (!st.me) { if (location.hash === "#register") selectAuth("register"); else if (location.hash === "#signin") selectAuth("login"); return; }
      const h = location.hash.slice(1);
      if (h === "submit" || h === "mine" || h === "account" || (h === "all" && st.me.is_admin)) selectTab(h, false);
    });
    boot();
  });
})();

/* Coming from the game's Online screen (/contest/?next=/play/#room=CODE): once signed in (or if
 * already signed in), go back to the game. Only the game's own /play/ path is followed. */
(function () {
  "use strict";
  let next = null;
  try { next = new URLSearchParams(location.search).get("next"); } catch (e) { next = null; }
  if (!next || !/^\/play\/(#[A-Za-z0-9=&_-]*)?$/.test(next)) return;
  const go = () => location.assign(next);
  document.addEventListener("auth-change", (ev) => { if (ev.detail) setTimeout(go, 700); });
  if (window.API) window.API.me().then((me) => { if (me) go(); }, () => {});
})();

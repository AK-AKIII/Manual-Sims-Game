/* Leaderboard: animated scores, rank movement since the last refresh, the
 * signed-in player highlighted (and pinned when scrolled out of view), a filter
 * box and an auto-refresh toggle. Admins also get disabled players after the
 * ranked rows (rank null, disabled true); they're marked and never ranked. */
(function () {
  "use strict";
  const { esc, fmtInt, fmtDate, relTime, countUp, toast } = window.UI;
  const API = window.API;
  const $ = (id) => document.getElementById(id);
  const REFRESH_MS = 15000;
  const AUTO_KEY = "pr-lb-auto";

  let rows = [], me = null, contest = null, prevRanks = null, timer = null, firstLoad = true, scrolledToMe = false;
  const shownScore = new Map(); // username -> last displayed relative total (for count-up)

  const unranked = (r) => r.rank === null || r.rank === undefined || r.disabled === true;

  function medal(r) {
    if (unranked(r)) return `<span class="badge-disabled" title="Disabled account: not ranked, shown to admins only">${r.disabled ? "disabled" : "unranked"}</span>`;
    const rank = r.rank;
    return rank <= 3 ? `<span class="medal m${rank}" aria-label="rank ${rank}">${rank}</span>` : `<span class="rk" aria-label="rank ${rank}">${rank}</span>`;
  }

  function moveHTML(r) {
    if (!prevRanks || unranked(r)) return `<span class="move"></span>`;
    const p = prevRanks.get(r.username);
    if (p === undefined) return `<span class="move new" title="New on the board">NEW</span>`;
    if (p > r.rank) return `<span class="move up" title="Up ${p - r.rank} since the last refresh" aria-label="up ${p - r.rank}">▲${p - r.rank}</span>`;
    if (p < r.rank) return `<span class="move down" title="Down ${r.rank - p} since the last refresh" aria-label="down ${r.rank - p}">▼${r.rank - p}</span>`;
    return `<span class="move muted" aria-hidden="true">·</span>`;
  }

  function render() {
    const tbody = document.querySelector("#lb-table tbody");
    const q = $("lb-filter").value.trim().toLowerCase();
    const max = contest && contest.hidden_count ? contest.hidden_count * 1e6 : Math.max(1, ...rows.map((r) => Number(r.relative_total) || 0));
    const list = q ? rows.filter((r) => String(r.username).toLowerCase().includes(q)) : rows;
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="7"><div class="empty"><span class="big" aria-hidden="true">♣︎</span>No full submissions yet. <a href="/contest/">Be the first on the board</a>.</div></td></tr>`;
      return;
    }
    if (!list.length) {
      tbody.innerHTML = `<tr><td colspan="7"><div class="empty">No player matches “${esc(q)}”.</div></td></tr>`;
      return;
    }
    const entrance = firstLoad && !(window.Motion && window.Motion.reduced());
    tbody.innerHTML = list.map((r, idx) => {
      const isMe = me && r.username === me.username;
      const pct = Math.max(0, Math.min(100, (100 * (Number(r.relative_total) || 0)) / max));
      return `<tr class="row${isMe ? " me" : ""}${unranked(r) ? " is-disabled" : ""}${entrance ? " row-in" : ""}" style="--i:${Math.min(idx, 14)}" data-user="${esc(r.username)}">
        <td class="rank">${medal(r)} ${moveHTML(r)}</td>
        <td class="user">${esc(r.username)}</td>
        <td class="r scorecell"><div class="scorebar"><b class="num" data-score="${esc(r.relative_total)}">${fmtInt(shownScore.has(r.username) ? shownScore.get(r.username) : 0)}</b>
          <span class="track" aria-hidden="true"><i class="${entrance ? "grow" : ""}" style="width:${pct}%"></i></span></div></td>
        <td class="r opt">${fmtInt(r.raw_total)}</td>
        <td class="r opt">${fmtInt(r.tests_ac)}${contest && contest.hidden_count ? `<span class="muted">/${contest.hidden_count}</span>` : ""}</td>
        <td class="r opt">${fmtInt(r.submissions)}</td>
        <td class="r opt" title="${esc(fmtDate(r.submitted))}">${esc(relTime(r.submitted))}</td>
      </tr>`;
    }).join("");
    tbody.querySelectorAll("[data-score]").forEach((el) => {
      const tr = el.closest("tr"), u = tr.dataset.user;
      const to = Number(el.dataset.score);
      el.dataset.v = String(shownScore.has(u) ? shownScore.get(u) : 0);
      countUp(el, to, { dur: firstLoad ? 900 : 600 });
      shownScore.set(u, to);
    });
    updatePinned();
  }

  function myRow() { return me ? rows.find((r) => r.username === me.username) : null; }

  function updatePinned() {
    const pin = $("pinned");
    const r = myRow();
    const tr = me ? document.querySelector(`#lb-table tr.me`) : null;
    if (!r || !tr || unranked(r)) { pin.classList.add("hidden"); return; }
    const rect = tr.getBoundingClientRect();
    const out = rect.bottom < 70 || rect.top > window.innerHeight - 10;
    if (out) {
      pin.innerHTML = `<span>You're <b>#${r.rank}</b> with <b>${fmtInt(r.relative_total)}</b></span><button type="button" class="btn sm" id="pin-go">Show me</button>`;
      pin.classList.remove("hidden");
      $("pin-go").addEventListener("click", () => {
        tr.scrollIntoView({ behavior: window.UI.reduced() ? "auto" : "smooth", block: "center" });
        tr.setAttribute("tabindex", "-1");
        tr.focus({ preventScroll: true });
      });
    } else pin.classList.add("hidden");
  }

  async function load(manual) {
    const btn = $("lb-refresh");
    if (manual) { btn.classList.add("busy"); btn.disabled = true; }
    try {
      const data = await API.leaderboard();
      const newRows = Array.isArray(data.rows) ? data.rows : [];
      if (rows.length) prevRanks = new Map(rows.filter((r) => !unranked(r)).map((r) => [r.username, r.rank]));
      else if (firstLoad) {
        // compare with what this browser saw last time, if anything
        try {
          const saved = JSON.parse(localStorage.getItem("pr-lb-ranks") || "null");
          if (saved && typeof saved === "object") prevRanks = new Map(Object.entries(saved));
        } catch (e) { prevRanks = null; }
      }
      rows = newRows;
      try { localStorage.setItem("pr-lb-ranks", JSON.stringify(Object.fromEntries(rows.filter((r) => !unranked(r)).map((r) => [r.username, r.rank])))); } catch (e) { /* ignore */ }
      $("frozen").classList.toggle("hidden", !data.frozen);
      render();
      const ranked = rows.filter((r) => !unranked(r)).length, off = rows.length - ranked;
      $("lb-updated").textContent = `${ranked} player${ranked === 1 ? "" : "s"}${off ? ` (+${off} disabled)` : ""} · updated ${new Date().toLocaleTimeString()}`;
      if (firstLoad && !scrolledToMe) {
        const tr = document.querySelector("#lb-table tr.me");
        if (tr) {
          scrolledToMe = true;
          const rect = tr.getBoundingClientRect();
          if (rect.top > window.innerHeight - 40) tr.scrollIntoView({ behavior: "auto", block: "center" });
        }
      }
      firstLoad = false;
      if (manual) toast("ok", "Leaderboard refreshed.", { silent: true });
    } catch (e) {
      $("lb-updated").textContent = "Couldn't refresh: " + e.message;
      if (firstLoad) document.querySelector("#lb-table tbody").innerHTML = `<tr><td colspan="7"><div class="err">${esc(e.message)}</div></td></tr>`;
    } finally {
      if (manual) { btn.classList.remove("busy"); btn.disabled = false; }
      schedule();
    }
  }

  function schedule() {
    clearTimeout(timer);
    if ($("lb-auto").checked) timer = setTimeout(() => { if (!document.hidden) load(false); else schedule(); }, REFRESH_MS);
  }

  document.addEventListener("DOMContentLoaded", async () => {
    if (!API) return;
    try { const v = localStorage.getItem(AUTO_KEY); if (v === "off") $("lb-auto").checked = false; } catch (e) { /* ignore */ }
    $("lb-auto").addEventListener("change", () => {
      try { localStorage.setItem(AUTO_KEY, $("lb-auto").checked ? "on" : "off"); } catch (e) { /* ignore */ }
      $("lb-updated").textContent = $("lb-auto").checked ? `Auto-refresh every ${REFRESH_MS / 1000} s` : "Auto-refresh off";
      schedule();
    });
    $("lb-refresh").addEventListener("click", () => load(true));
    $("lb-filter").addEventListener("input", render);
    $("lb-filter").addEventListener("keydown", (e) => { if (e.key === "Escape") { $("lb-filter").value = ""; render(); } });
    window.addEventListener("scroll", updatePinned, { passive: true });
    window.addEventListener("resize", updatePinned);
    document.addEventListener("auth-change", (ev) => { me = ev.detail; render(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && document.activeElement === document.body) { e.preventDefault(); $("lb-filter").focus(); }
    });
    const [c, m] = await Promise.all([API.contest().catch(() => null), API.me().catch(() => null)]);
    contest = c; me = m;
    load(false);
  });
})();

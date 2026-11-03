/* Poker Run API client (see platform/API.md).
 * Every request is same-origin with cookies; state-changing requests (POST,
 * DELETE) also send
 * X-Requested-With: fetch (the server's CSRF guard). Errors come back as
 * {"error": "..."} and are thrown as ApiError with that message and the status. */
(function () {
  "use strict";

  class ApiError extends Error {
    constructor(message, status, data) { super(message); this.status = status; this.data = data; }
  }

  async function request(method, path, body) {
    const opts = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (method !== "GET" && method !== "HEAD") opts.headers["X-Requested-With"] = "fetch";
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw new ApiError("Can't reach the server. Check your connection and try again.", 0, null);
    }
    if (res.status === 204) return null;
    let data = null;
    const text = await res.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }
    if (!res.ok) {
      let msg = data && typeof data.error === "string" ? data.error : null;
      if (!msg) {
        msg = {
          401: "Please sign in first.", 403: "Not allowed.", 404: "Not found.", 413: "That file is too large.",
          429: "Too many requests. Please wait a moment.",
        }[res.status] || `The server answered ${res.status}. Try again in a moment.`;
      }
      throw new ApiError(msg, res.status, data);
    }
    if (data === null && text) throw new ApiError("The server sent an unexpected response.", res.status, null);
    return data;
  }

  let meCache = null;
  // /api/contest is read by site.js (announcement banner) and the page scripts at
  // load; share one request for a few seconds instead of asking twice.
  let contestCache = null, contestAt = 0;
  const CONTEST_TTL = 3000;
  const qs = (params) => {
    const p = new URLSearchParams();
    Object.keys(params || {}).forEach((k) => {
      const v = params[k];
      if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
    });
    const s = p.toString();
    return s ? "?" + s : "";
  };
  const id = (x) => encodeURIComponent(x);

  const API = {
    ApiError,
    get: (p) => request("GET", p),
    post: (p, b) => request("POST", p, b === undefined ? {} : b),
    del: (p, b) => request("DELETE", p, b),
    qs,

    /** Contest info; one shared request for a few seconds unless `force`. */
    contest(force) {
      if (!force && contestCache && Date.now() - contestAt < CONTEST_TTL) return contestCache;
      contestAt = Date.now();
      contestCache = request("GET", "/api/contest");
      contestCache.catch(() => { contestCache = null; });
      return contestCache;
    },
    forgetContest() { contestCache = null; },
    /** Resolves to {username, is_admin} or null when signed out. */
    async me(force) {
      if (meCache && !force) return meCache;
      meCache = (async () => {
        try {
          const m = await request("GET", "/api/me");
          return m && m.username ? m : null;
        } catch (e) {
          if (e.status === 401) return null;
          throw e;
        }
      })();
      try { return await meCache; } catch (e) { meCache = null; throw e; }
    },
    forgetMe() { meCache = null; },
    async login(username, password) {
      const r = await request("POST", "/api/login", { username, password });
      meCache = Promise.resolve(r);
      return r;
    },
    async register(username, password, invite, email) {
      const r = await request("POST", "/api/register", { username, password, invite: invite || "", email: email || "" });
      meCache = Promise.resolve(r);
      return r;
    },
    async logout() {
      await request("POST", "/api/logout");
      meCache = Promise.resolve(null);
    },
    /** Add or change the signed-in account's email (current password required). */
    async changeEmail(email, password) {
      const r = await request("POST", "/api/account/email", { email, password });
      meCache = Promise.resolve(r);
      return r;
    },
    submit: (kind, source) => request("POST", "/api/submissions", { kind, source }),
    submissions: () => request("GET", "/api/submissions"),
    submission: (id) => request("GET", "/api/submissions/" + encodeURIComponent(id)),
    leaderboard: () => request("GET", "/api/leaderboard"),
    adminSubmissions: (params) => request("GET", "/api/admin/submissions" + qs(params)),
    rejudge: (sid) => request("POST", "/api/admin/rejudge/" + id(sid)),

    // ---- admin panel (API.md "Admin API")
    adminOverview: () => request("GET", "/api/admin/overview"),
    adminUsers: () => request("GET", "/api/admin/users"),
    adminUser: (uid) => request("GET", "/api/admin/users/" + id(uid)),
    adminDisable: (uid, reason) => request("POST", `/api/admin/users/${id(uid)}/disable`, reason ? { reason } : {}),
    adminSetEmail: (uid, email) => request("POST", `/api/admin/users/${id(uid)}/email`, { email: email || null }),
    adminEnable: (uid) => request("POST", `/api/admin/users/${id(uid)}/enable`, {}),
    adminPassword: (uid, password) => request("POST", `/api/admin/users/${id(uid)}/password`, { password }),
    adminSetAdmin: (uid, isAdmin) => request("POST", `/api/admin/users/${id(uid)}/admin`, { is_admin: !!isAdmin }),
    adminDeleteUser: (uid, confirm) => request("DELETE", "/api/admin/users/" + id(uid), { confirm }),
    adminDeleteSubmission: (sid) => request("DELETE", "/api/admin/submissions/" + id(sid)),
    adminCancel: (sid) => request("POST", `/api/admin/submissions/${id(sid)}/cancel`, {}),
    adminRejudgeBulk: (which) => request("POST", "/api/admin/rejudge-bulk", { which }),
    adminRunFull: (sid) => request("POST", `/api/admin/submissions/${id(sid)}/run-full`, {}),
    adminRunFullBulk: (which, dryRun) => request("POST", "/api/admin/run-full-bulk" + (dryRun ? "?dry_run=1" : ""), { which }),
    adminSimilarity: (limit) => request("GET", "/api/admin/similarity" + qs({ limit })),
    adminSettings: () => request("GET", "/api/admin/settings"),
    adminSaveSettings: (set, clear) => request("POST", "/api/admin/settings", { set: set || {}, clear: clear || [] }),
    adminRegenerateInvite: () => request("POST", "/api/admin/settings/invite/regenerate", {}),
    adminAudit: (params) => request("GET", "/api/admin/audit" + qs(params)),
  };

  window.API = API;
})();

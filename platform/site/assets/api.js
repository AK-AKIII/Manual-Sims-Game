/* Poker Run API client (see platform/API.md).
 * Every request is same-origin with cookies; state-changing requests also send
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

  const API = {
    ApiError,
    get: (p) => request("GET", p),
    post: (p, b) => request("POST", p, b === undefined ? {} : b),

    contest: () => request("GET", "/api/contest"),
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
    async register(username, password, invite) {
      const r = await request("POST", "/api/register", { username, password, invite: invite || "" });
      meCache = Promise.resolve(r);
      return r;
    },
    async logout() {
      await request("POST", "/api/logout");
      meCache = Promise.resolve(null);
    },
    submit: (kind, source) => request("POST", "/api/submissions", { kind, source }),
    submissions: () => request("GET", "/api/submissions"),
    submission: (id) => request("GET", "/api/submissions/" + encodeURIComponent(id)),
    leaderboard: () => request("GET", "/api/leaderboard"),
    adminSubmissions: () => request("GET", "/api/admin/submissions"),
    rejudge: (id) => request("POST", "/api/admin/rejudge/" + encodeURIComponent(id)),
  };

  window.API = API;
})();

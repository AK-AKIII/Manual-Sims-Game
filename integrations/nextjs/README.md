# Poker Run page for a Next.js site

A drop-in `/poker-run` page for an existing Next.js site: title, short description,
the explainer video, the playable browser game in an iframe (with a link to open it in
a new tab if framing fails), and buttons to the contest, the problem and the
leaderboard. No dependencies, no client JavaScript, inline styles only; colours
inherit from your site so it fits light and dark themes.

The big files (the 18 MB video, the game) are served by the **contest domain**
(`platform/deploy/`, e.g. `https://pokerrun.example.com`), not by your repository.
Option B at the end hosts the game inside your Next.js app instead.

| File | For |
| --- | --- |
| `app/poker-run/page.tsx` | **App Router** (`app/` directory, Next 13.4+, 14, 15) |
| `pages/poker-run.tsx` | **Pages Router** (`pages/` directory, Next 12+) |

Both files were type-checked (TypeScript strict) and built with `next build` on Next 15
(React 19); they use nothing version-specific beyond `metadata` (App Router) and
`next/head` (Pages Router).

## Install (option A: game and video from the contest domain)

1. **Copy one file.** Which router does your site use?
   - There is an `app/` (or `src/app/`) folder with `layout.tsx` → copy
     `app/poker-run/page.tsx` to `app/poker-run/page.tsx` (or `src/app/poker-run/page.tsx`).
   - There is a `pages/` (or `src/pages/`) folder with `_app.tsx` → copy
     `pages/poker-run.tsx` to `pages/poker-run.tsx` (or `src/pages/poker-run.tsx`).
   - Both folders exist? Use one of them only: Next refuses two pages for the same route.
   - JavaScript site (no TypeScript)? Rename to `.jsx` and delete the `import type ...`
     line(s) and the `: Record<string, CSSProperties>` / `: Metadata` annotations.
2. **Set the environment variable.**
   - Vercel: Project → Settings → Environment Variables → add
     `NEXT_PUBLIC_POKER_RUN_URL` = `https://pokerrun.example.com` for Production (and
     Preview if you want) → **redeploy** (values starting with `NEXT_PUBLIC_` are
     baked in at build time).
   - Locally: add `NEXT_PUBLIC_POKER_RUN_URL=https://pokerrun.example.com` to `.env.local`.
3. **Allow your site to frame the game** on the contest server (next section).
4. **Link to it** from your navigation: `<Link href="/poker-run">Poker Run</Link>`.

`next.config.js` needs **no changes**: the page uses plain `<video>`, `<iframe>` and
`<a>` elements, not `next/image`, so no `images.remotePatterns` entry is needed.
The exception is a Content-Security-Policy on **your** site (see below).

Optional variables (both pages read them):

| Variable | Default | Use |
| --- | --- | --- |
| `NEXT_PUBLIC_POKER_RUN_URL` | none | contest site; without it the contest/problem buttons are hidden |
| `NEXT_PUBLIC_POKER_RUN_GAME_SRC` | `${URL}/play/` | game URL, e.g. `/poker-run/play/index.html` for option B |
| `NEXT_PUBLIC_POKER_RUN_VIDEO_SRC` | `${URL}/media/poker_run_explainer.mp4` | video URL, e.g. a file in `public/` |

## Settings on the contest domain (Caddy / CSP)

Browsers only show the game in your iframe if the contest server allows your origin
in its `Content-Security-Policy: frame-ancestors`. The Caddyfile in
`platform/deploy/` reads the allowed origins from `FRAME_ANCESTORS`, and applies them
**only to `/play/`** (every other page of the contest site stays unframeable):

```caddy
@play path /play /play/*
header @play Content-Security-Policy "... frame-ancestors 'self' {$FRAME_ANCESTORS}"
```

On the server, edit `/opt/pokerrun/platform/deploy/.env`:

```sh
FRAME_ANCESTORS="https://yourname.com https://www.yourname.com"
```

then `make -C /opt/pokerrun/platform/deploy up` (Caddy restarts with the new value).
List every origin that serves the page, exactly (scheme + host, no path, no trailing
slash). Vercel preview deployments have their own hosts; you can add
`https://*.vercel.app`, but that lets **any** Vercel site frame the game, so prefer
listing just your production domains (the game is harmless when framed, so this is a
matter of taste, not safety).

Check: `curl -sI https://pokerrun.example.com/play/ | grep -i content-security` shows
your origins after `frame-ancestors`.

The video needs nothing: `<video>` may play files from other origins without CORS
(the page does not set `crossorigin`).

### If your site sends its own CSP

Sites that set a Content-Security-Policy (in `next.config.js` `headers()`, in
middleware, or on Vercel) must allow the contest domain for frames and media:

```js
// next.config.js (merge into your existing policy)
const POKER_RUN = "https://pokerrun.example.com";
module.exports = {
  async headers() {
    return [{
      source: "/:path*",
      headers: [{
        key: "Content-Security-Policy",
        value: [
          "default-src 'self'",
          `frame-src 'self' ${POKER_RUN}`,
          `media-src 'self' ${POKER_RUN}`,
          `img-src 'self' data: ${POKER_RUN}`,          // the video poster
          // ...your existing script-src, style-src etc.
        ].join("; "),
      }],
    }];
  },
};
```

No CSP header on your site means nothing to do here.

## Tailwind

The page does not need Tailwind and is not affected by it (Tailwind's preflight
resets margins on `h1`/`p`, which the inline styles set explicitly). To match your
site's look, replace the `style={s.xxx}` props with classes, for example:

```tsx
<main className="mx-auto max-w-5xl px-4 pb-16 pt-8">
<h1 className="mb-3 text-4xl font-bold">Poker Run</h1>
<a className="rounded-lg border px-4 py-2 font-semibold hover:bg-neutral-100 dark:hover:bg-neutral-800" ...>
<iframe className="block h-[min(1100px,92vh)] min-h-[640px] w-full border-0" ...>
```

and delete the `s` object at the bottom of the file.

## Option B: host the game inside your Next.js site (no contest domain)

The browser game is two static files with no server and no network calls: it stores
its state in `localStorage` only. You can serve it from your own site:

1. Copy from this repository into your Next.js project:

   ```sh
   mkdir -p public/poker-run/play
   cp poker_run/web/index.html poker_run/web/engine.js public/poker-run/play/
   ```

   (`parity_driver.js` is a test helper; leave it out.) Optional: the video too,
   `cp video/poker_run_explainer.mp4 public/poker-run/` (18 MB in your repository;
   Vercel serves it fine).
2. Set, instead of (or in addition to) `NEXT_PUBLIC_POKER_RUN_URL`:

   ```sh
   NEXT_PUBLIC_POKER_RUN_GAME_SRC=/poker-run/play/index.html
   NEXT_PUBLIC_POKER_RUN_VIDEO_SRC=/poker-run/poker_run_explainer.mp4   # only if you copied it
   ```

   Use the full `/poker-run/play/index.html` path: Next.js serves files from `public/`
   but does not map a folder URL like `/poker-run/play/` to its `index.html`. The game
   lives under `/poker-run/play/...`, so it does not clash with the `/poker-run` page.
3. Same-origin framing needs no `frame-ancestors` setting. If your site sends a CSP,
   the game needs `script-src 'unsafe-inline'` and `style-src 'unsafe-inline'` for
   that path (it is one self-contained HTML file with inline script and styles), and
   its `X-Frame-Options`, if you set one, must be `SAMEORIGIN` rather than `DENY`.
4. Without `NEXT_PUBLIC_POKER_RUN_URL` the contest, problem and leaderboard buttons are
   hidden (there is no contest server to link to).

Trade-off: the copy does not update itself. When the game changes (rules or engine
fixes in `poker_run/web/`), copy the two files again. With option A the contest
server always serves the current version.

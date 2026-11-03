// Poker Run page for a Next.js App Router site (Next 13.4+; works with 14 and 15).
// Copy to app/poker-run/page.tsx (or src/app/poker-run/page.tsx). Route: /poker-run
//
// Configuration (build-time env vars, set on Vercel -> Project -> Settings -> Environment Variables):
//   NEXT_PUBLIC_POKER_RUN_URL        contest site, e.g. https://pokerrun.example.com (no trailing slash needed)
//   NEXT_PUBLIC_POKER_RUN_GAME_SRC   optional: game URL override, e.g. /poker-run/play/index.html (option B in README)
//   NEXT_PUBLIC_POKER_RUN_VIDEO_SRC  optional: video URL override
// NEXT_PUBLIC_* values are inlined at build time: redeploy after changing them.
//
// No client-side JavaScript, no dependencies, styles inline (works with or without Tailwind).
// This is a Server Component; nothing here needs "use client".

import type { Metadata } from "next";
import type { CSSProperties } from "react";

const BASE = (process.env.NEXT_PUBLIC_POKER_RUN_URL || "").replace(/\/+$/, "");
const GAME_SRC = process.env.NEXT_PUBLIC_POKER_RUN_GAME_SRC || (BASE ? `${BASE}/play/` : "");
const VIDEO_SRC =
  process.env.NEXT_PUBLIC_POKER_RUN_VIDEO_SRC || (BASE ? `${BASE}/media/poker_run_explainer.mp4` : "");
const POSTER_SRC = BASE ? `${BASE}/assets/poster.jpg` : undefined;

const TITLE = "Poker Run";
const DESCRIPTION =
  "A heuristic optimisation contest built on poker hands: plan a whole eight-round run with " +
  "modifiers, hand levels and deck editing. Play it by hand in the browser, then write a solver " +
  "and climb the leaderboard.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
};

export default function PokerRunPage() {
  return (
    <main style={s.main}>
      <header style={s.header}>
        <h1 style={s.h1}>{TITLE}</h1>
        <p style={s.lead}>{DESCRIPTION}</p>
        {BASE && (
          <nav style={s.actions} aria-label="Poker Run links">
            <a style={{ ...s.button, ...s.primary }} href={`${BASE}/contest/`}>
              Enter the contest
            </a>
            <a style={s.button} href={`${BASE}/problem/`}>
              Read the problem
            </a>
            <a style={s.button} href={`${BASE}/contest/leaderboard/`}>
              Leaderboard
            </a>
          </nav>
        )}
      </header>

      {VIDEO_SRC && (
        <section style={s.section} aria-labelledby="pr-video">
          <h2 id="pr-video" style={s.h2}>
            How it works
          </h2>
          <div style={s.frame}>
            <video
              style={s.video}
              controls
              playsInline
              preload="metadata"
              poster={POSTER_SRC}
            >
              <source src={VIDEO_SRC} type="video/mp4" />
              Your browser cannot play this video. <a href={VIDEO_SRC}>Download it</a>.
            </video>
          </div>
        </section>
      )}

      {GAME_SRC && (
        <section style={s.section} aria-labelledby="pr-game">
          <h2 id="pr-game" style={s.h2}>
            Play in the browser
          </h2>
          <div style={s.frame}>
            <iframe
              src={GAME_SRC}
              title="Poker Run: the browser game"
              style={s.iframe}
              loading="lazy"
              referrerPolicy="strict-origin-when-cross-origin"
              allow="clipboard-write; fullscreen"
              sandbox="allow-scripts allow-same-origin allow-downloads allow-popups allow-forms"
            />
          </div>
          <p style={s.note}>
            Game not showing?{" "}
            <a href={GAME_SRC} target="_blank" rel="noopener noreferrer">
              Open it in a new tab
            </a>
            .
          </p>
        </section>
      )}
    </main>
  );
}

// Colours inherit from the site (currentColor), so it fits light and dark themes.
const s: Record<string, CSSProperties> = {
  main: { maxWidth: 1100, margin: "0 auto", padding: "32px 16px 64px", lineHeight: 1.6 },
  header: { marginBottom: 32 },
  h1: { fontSize: "clamp(2rem, 5vw, 3rem)", lineHeight: 1.1, margin: "0 0 12px" },
  h2: { fontSize: "1.35rem", margin: "0 0 12px" },
  lead: { fontSize: "1.1rem", maxWidth: 720, margin: "0 0 20px", opacity: 0.85 },
  actions: { display: "flex", flexWrap: "wrap", gap: 12 },
  button: {
    display: "inline-block",
    padding: "10px 18px",
    borderRadius: 8,
    border: "1px solid currentColor",
    color: "inherit",
    textDecoration: "none",
    fontWeight: 600,
  },
  primary: { background: "#c9a227", borderColor: "#c9a227", color: "#111" },
  section: { margin: "40px 0" },
  frame: {
    borderRadius: 12,
    overflow: "hidden",
    border: "1px solid rgba(127, 127, 127, 0.35)",
    background: "#0f1115",
  },
  video: { display: "block", width: "100%", height: "auto", aspectRatio: "16 / 9" },
  iframe: { display: "block", width: "100%", height: "min(1100px, 92vh)", minHeight: 640, border: 0 },
  note: { fontSize: "0.9rem", opacity: 0.75, marginTop: 8 },
};

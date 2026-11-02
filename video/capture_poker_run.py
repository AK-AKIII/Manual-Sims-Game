"""Capture the Poker Run browser game being played, for the explainer video.

Drives Chrome (via Playwright) through a real output file, clicking the same
buttons a person would, and saves a screenshot before every click plus where
the click lands. render_poker_run.py animates a cursor over these.

    python video/capture_poker_run.py SEED OUTPUT_FILE OUT_DIR [--slow N] [--no-fast] [--no-replay]

The first N moves (default 6) are captured click by click; later moves get
one screenshot each, for a fast-forward (skipped with --no-fast). Then the
replay view is captured, unless --no-replay.
Needs: pip install playwright (uses the installed Google Chrome).
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGE = Path(__file__).resolve().parent.parent / "poker_run" / "web" / "index.html"
SCALE = 1.2  # 1600x900 CSS pixels -> 1920x1080 screenshots
SUITS = "SHDC"


def clicks_for(move):
    """CSS selectors to click, in order, to make one move in the UI."""
    tok = move.split()
    cmd, args = tok[0], tok[1:]
    if cmd in ("play", "discard"):
        return [f'[data-act="card"][data-id="{c}"]' for c in args] + [f'[data-act="{cmd}"]']
    if cmd in ("buy", "sell", "level"):
        return [f'[data-act="{cmd}"][data-v="{args[0]}"]']
    if cmd in ("remove", "promote"):
        return [f'[data-act="deck-pick"][data-id="{args[0]}"]', f'[data-act="{cmd}"]']
    if cmd == "paint":
        return [f'[data-act="deck-pick"][data-id="{args[0]}"]', f'[data-act="paint"][data-v="{SUITS.index(args[1])}"]']
    return [f'[data-act="{cmd}"]']


class Recorder:
    def __init__(self, page, out):
        self.page, self.out, self.steps = page, out, []

    def shot(self, **info):
        path = self.out / f"{len(self.steps):04d}.png"
        self.page.screenshot(path=str(path))
        self.steps.append({"img": path.name, **info})

    def click(self, selector, label=None, settle=250):
        el = self.page.locator(selector).first
        el.scroll_into_view_if_needed()
        self.page.wait_for_timeout(120)
        box = el.bounding_box()
        center = [(box["x"] + box["width"] / 2) * SCALE, (box["y"] + box["height"] / 2) * SCALE]
        self.shot(cursor=center, click=True, label=label)
        el.click()
        self.page.wait_for_timeout(settle)


def main():
    seed, output_file, out = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    slow = int(sys.argv[sys.argv.index("--slow") + 1]) if "--slow" in sys.argv else 6
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    output_text = output_file.read_text()
    moves = [l.strip() for l in output_text.strip().splitlines()[1:] if l.strip()]

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=SCALE)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{PAGE.as_uri()}#seed={seed}")
        page.wait_for_timeout(400)
        rec = Recorder(page, out)
        rec.shot(section="play", hold=1.0)

        for k, move in enumerate(moves):
            if k < slow:
                for sel in clicks_for(move):
                    rec.click(sel)
                if move.split()[0] in ("play", "discard"):
                    page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(150)
                rec.shot(move=move, hold=1.2)
            elif "--no-fast" in sys.argv:
                break
            else:
                for sel in clicks_for(move):
                    page.locator(sel).first.click()
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(60)
                rec.shot(move=move, fast=True, hold=0.35)

        page.evaluate("window.scrollTo(0, 0)")
        if "--no-replay" not in sys.argv:
            replay(rec, page, seed, output_text)
        browser.close()
    (out / "timeline.json").write_text(json.dumps({"seed": seed, "moves": moves, "steps": rec.steps}, indent=1))
    print(f"{len(rec.steps)} screenshots in {out}; page errors: {errors or 'none'}")


def replay(rec, page, seed, output_text):
    """Load the same output in Replay mode and step through it."""
    rec.click('[data-act="mode"][data-v="replay"]')
    page.fill("#rp-seed", str(seed))
    rec.click('[data-act="rp-seed"]')
    page.fill("#rp-output", output_text)
    rec.click('[data-act="rp-run"]', settle=400)
    rec.shot(section="replay", hold=1.5)
    for _ in range(4):
        rec.click('[data-act="rp-step"][data-v="1"]')
    rec.shot(hold=1.2)
    rec.click('[data-act="rp-step"][data-v="last"]')
    rec.shot(hold=1.5)


if __name__ == "__main__":
    main()

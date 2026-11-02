"""Render the Poker Run explainer: rules, playing by hand, solvers, and 1v1.

    python video/collect_poker_run.py                          # solver numbers -> poker_run_data.json
    python video/capture_poker_run.py 7 manual.txt CAP_MANUAL --slow 99 --no-replay
    python video/capture_poker_run.py 7 beam7.txt CAP_RUN --slow 0
    python video/render_poker_run.py CAP_MANUAL CAP_RUN        # writes poker_run_explainer.mp4 and .webm
    python video/render_poker_run.py CAP_MANUAL CAP_RUN stills # a few PNGs per scene instead

Every number on screen comes from the simulator or from poker_run_data.json.
Needs Pillow, numpy, imageio-ffmpeg (and Playwright for the capture step).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import music  # noqa: E402
from render_poker_hands import (  # noqa: E402
    BG, CREAM, FPS, GOLD, H, MUTED, RED, W, caption, clamp, draw_card, ease, ease_out, font, glow, header,
    lerp, mono, paste, pop_text, prog, symbols, text, window, wrap,
)

from poker_run import Game, generate  # noqa: E402
from poker_run.rules import HANDS, MODIFIERS, RANKS, SUITS, chips  # noqa: E402

DATA = json.loads((HERE / "poker_run_data.json").read_text())
BLUE = (130, 190, 255)
PANEL = (6, 18, 14)


def code(card, game=None):
    """Face code like "QH" of a card id (its current face if a game is given)."""
    r, s = (game.rank[card], game.suit[card]) if game else (card % 13, card // 13)
    return RANKS[r] + SUITS[s]


def card_with_id(img, d, card, cx, cy, w=150, game=None, alpha=1.0, glow_amt=0.0, glow_color=GOLD, id_below=True):
    draw_card(img, code(card, game), cx, cy, w=w, alpha=alpha, glow_amt=glow_amt, glow_color=glow_color)
    if id_below:
        text(d, (cx, cy + w * 0.7 + 26), f"id {card}", mono(max(16, int(w * 0.15))), MUTED, alpha)


def panel(d, box, alpha=1.0, fill=PANEL, outline=None, r=24):
    d.rounded_rectangle(box, r, fill=fill + (int(225 * alpha),),
                        outline=(outline + (int(255 * alpha),)) if outline else None, width=3 if outline else 0)


def mono_block(d, x, y, lines, size=26, alpha=1.0, colors=None, line_h=None):
    f = mono(size)
    lh = line_h or int(size * 1.55)
    for i, line in enumerate(lines):
        text(d, (x, y + i * lh), line, f, (colors[i] if colors else CREAM), alpha, anchor="lm")


# ---- the demo game (seed 7) -------------------------------------------------

DEMO = DATA["demo"]
G7 = Game(generate(7))
HAND0 = list(G7.hand)                 # QH JS KC 6H 8C 10S 2D 5H
DISCARD = [26, 16, 17, 45]
G7b = G7.clone()
G7b.apply("discard " + " ".join(map(str, DISCARD)))
HAND1 = list(G7b.hand)
STRAIGHT = [33, 8, 9, 23, 50]
STRAIGHT_RES = G7b.evaluate(STRAIGHT)
NEXT0 = G7.pile[:6]


def slot(i, n=8, y=700, gap=170):
    return 960 + (i - (n - 1) / 2) * gap, y


# ---- scenes -----------------------------------------------------------------


def s_title(img, d, t):
    five = ["7H"] * 5
    for i, c in enumerate(five):
        k = i - 2
        x = prog(t, 0.2 + 0.12 * i, 1.1 + 0.12 * i, ease_out)
        if x <= 0:
            continue
        from render_poker_hands import fanned_card
        im = fanned_card(c, 170, k * 9)
        cx, cy = 960 + k * 115, 790 + abs(k) ** 1.6 * 14 + (1 - x) * 520
        paste(img, im, cx - im.width / 2, cy - im.height / 2, clamp(x * 3))
    a = prog(t, 0.6, 1.4)
    text(d, (960, 250 - 20 * a), "POKER RUN", font(160, "heavy"), CREAM, a)
    text(d, (960, 380), "Eight rounds. Every card known in advance.", font(48, "medium"), MUTED, prog(t, 1.1, 1.8))
    text(d, (960, 450), "Play it by hand, or write a bot to play it for you.", font(48, "medium"), GOLD, prog(t, 1.6, 2.3))
    pop_text(img, "FLUSH FIVE", 960, 560, 40, clamp((t - 2.6) / 0.5), weight="heavy", color=GOLD)


def s_idea(img, d, t):
    header(d, "THE IDEA", "A heuristic contest problem, like AtCoder Heuristic Contests")
    inp = DEMO["input"].splitlines()
    out = DEMO["output"].splitlines()
    v = DEMO["verdict"]
    cols = [
        (90, "INPUT", ["8 8 4 3 5 12", inp[1][:30] + "...", inp[2][:30] + "...", inp[3][:30] + "...", "...",
                       inp[-1][:30] + "..."]),
        (700, "YOUR MOVES", out[:6] + ["..."]),
        (1310, "CHECKER", ["valid", f"score {v['score']}", f"cleared {v['cleared']}/{v['rounds']}", f"moves {v['moves']}"]),
    ]
    for k, (x, title, lines) in enumerate(cols):
        a = prog(t, 0.4 + 1.6 * k, 1.0 + 1.6 * k)
        panel(d, [x, 240, x + 520, 760], a)
        text(d, (x + 30, 285), title, font(30, "heavy"), GOLD, a, anchor="lm")
        mono_block(d, x + 30, 350, lines, 24, a, line_h=52)
        if k:
            text(d, (x - 45, 500), "→", symbols(64), GOLD, a)
    text(d, (350, 800), "draw orders + shop offers", font(26, "medium"), MUTED, prog(t, 0.8, 1.4))
    text(d, (960, 800), "play / discard / buy / ...", font(26, "medium"), MUTED, prog(t, 2.4, 3.0))
    text(d, (1570, 800), "same simulator as the browser", font(26, "medium"), MUTED, prog(t, 4.0, 4.6))
    caption(img, d, t, 0.3, 5.3, "The input shows the whole run: every draw order, every shop offer.")
    caption(img, d, t, 5.6, 10.7, "You output a list of moves. The checker replays them and scores the run.")


def s_cards(img, d, t):
    header(d, "CARDS", "52 cards with ids 0 to 51")
    for c in range(52):
        row, col = divmod(c, 13)
        a = prog(t, 0.2 + 0.03 * c, 0.6 + 0.03 * c)
        x, y = 210 + col * 125, 290 + row * 165
        draw_card(img, code(c), x, y, w=92, alpha=a)
        text(d, (x, y + 82), str(c), mono(22, bold=True), GOLD if c in (0, 12, 13, 51) else MUTED, a)
    caption(img, d, t, 0.4, 4.6, "Card i starts as rank i mod 13 and suit i div 13. Moves name cards by id.")
    caption(img, d, t, 4.9, 9.2, "The shop can repaint or promote a card later. Its id never changes.")


def hud(d, items, alpha=1.0, x0=1050):
    for i, (label, value) in enumerate(items):
        x = x0 + i * 200
        d.rounded_rectangle([x, 44, x + 180, 128], 20, fill=(0, 0, 0, int(95 * alpha)))
        text(d, (x + 90, 68), label, font(20, "demi"), MUTED, alpha)
        text(d, (x + 90, 102), str(value), font(38, "bold"), CREAM, alpha)


def s_round(img, d, t):
    header(d, "A ROUND", "Seed 7, round 1")
    toss = prog(t, 5.6, 6.4)
    deal = prog(t, 6.6, 7.6)
    pick = prog(t, 8.4, 9.0)
    to_row = prog(t, 9.6, 10.4)
    score = prog(t, 10.6, 11.4)
    hud(d, [("TARGET", 300), ("ROUND PTS", int(round(STRAIGHT_RES["points"] * score))),
            ("PLAYS", 4 if t < 10.4 else 3), ("DISCARDS", 3 if t < 6.0 else 2)])
    # the draw order is visible: next cards
    a = prog(t, 2.4, 3.0) * (1 - prog(t, 6.6, 7.0))
    text(d, (90, 255), "NEXT IN THIS ROUND'S DRAW ORDER", font(24, "demi"), MUTED, a, anchor="lm")
    for i, c in enumerate(NEXT0[:4]):
        draw_card(img, code(c), 150 + i * 110, 360, w=86, alpha=a, glow_amt=0.8 * a)
        text(d, (150 + i * 110, 445), str(c), mono(20), MUTED, a)
    lift = prog(t, 4.6, 5.2)
    for i, c in enumerate(HAND0):
        x, y = slot(i)
        if c in DISCARD:
            x, y = lerp(x, 2050, toss), y - 50 * lift
            card_with_id(img, d, c, x, y, w=140, alpha=1 - toss, glow_amt=lift * (1 - toss), glow_color=RED)
    slide = prog(t, 6.2, 6.8)
    for i, c in enumerate(HAND1):
        x, y = slot(i)
        if c in HAND0:      # a kept card slides left into its new place
            ox, _ = slot(HAND0.index(c))
            x = lerp(ox, x, slide)
        else:               # a new card flies in from the draw-order strip
            if deal <= 0:
                continue
            sx = 150 + NEXT0.index(c) * 110
            x, y = lerp(sx, x, ease_out(deal)), lerp(360, y, ease_out(deal))
        if c in STRAIGHT:
            j = STRAIGHT.index(c)
            y -= 50 * pick
            x, y = lerp(x, 960 + (j - 2) * 165, to_row), lerp(y, 380, to_row)
        card_with_id(img, d, c, x, y, w=140, glow_amt=pick if c in STRAIGHT else 0, id_below=c not in STRAIGHT or to_row < 0.5)
    pop_text(img, "STRAIGHT", 960, 205, 90, clamp((t - 10.5) / 0.5))
    text(d, (960, 560), f"{STRAIGHT_RES['base']} base  ×  {STRAIGHT_RES['mult']} mult  =  {STRAIGHT_RES['points']} points",
         font(44, "bold"), GOLD, prog(t, 10.8, 11.3))
    caption(img, d, t, 0.3, 2.3, "Each round: 8 cards in hand, 4 plays, 3 discards, and a target.")
    caption(img, d, t, 2.5, 5.4, "Nothing is hidden: you can see the draw order, so you know what a discard brings.")
    caption(img, d, t, 5.6, 8.4, "Discard four cards and draw the next four...")
    caption(img, d, t, 8.6, 14.8, "...and the hand holds a straight: 316 points. Target met with 3 plays to spare.")


def s_scoring(img, d, t):
    header(d, "SCORING", "points = base × mult × multipliers")
    ex = [("PR", [12, 25, 18, 41, 0])]  # AS AH 7H 4C 2S
    g = Game(generate(7))
    res = g.evaluate(ex[0][1])
    a1 = prog(t, 0.3, 0.9) * (1 - prog(t, 6.6, 7.0))
    for j, c in enumerate(STRAIGHT):
        x = 960 + (j - 2) * 165
        draw_card(img, code(c), x, 330, w=130, alpha=a1)
        text(d, (x, 450), f"+{chips(c % 13)}", font(36, "bold"), GOLD, a1 * prog(t, 1.0 + 0.2 * j, 1.4 + 0.2 * j))
    eq = f"base 30 + 49 chips = 79     ×     mult 4     =     316"
    text(d, (960, 560), eq, font(48, "bold"), CREAM, a1 * prog(t, 2.4, 3.0))
    text(d, (960, 625), "Straight: base 30, mult 4. All five cards score.", font(30, "medium"), MUTED, a1 * prog(t, 2.8, 3.4))
    a2 = prog(t, 7.0, 7.6)
    for j, c in enumerate(ex[0][1]):
        x = 960 + (j - 2) * 165
        scored = c in res["scored"]
        draw_card(img, code(c), x, 330, w=130, alpha=a2 * (1 if scored else 0.45), glow_amt=a2 if scored else 0)
        label = f"+{chips(c % 13)}" if scored else "kicker"
        text(d, (x, 450), label, font(36 if scored else 28, "bold" if scored else "demi"), GOLD if scored else MUTED, a2)
    text(d, (960, 560), f"base 10 + 22 chips = {res['base']}     ×     mult {res['mult']}     =     {res['points']}",
         font(48, "bold"), CREAM, a2 * prog(t, 8.0, 8.6))
    text(d, (960, 625), "Pair: only the two aces score. The other three are kickers.", font(30, "medium"), MUTED,
         a2 * prog(t, 8.4, 9.0))
    caption(img, d, t, 0.3, 6.6, "Base is the hand's base plus the chips of the scored cards. Then multiply by mult.")
    caption(img, d, t, 7.0, 12.7, "Only the cards that make the hand score. Kickers are spent, but add nothing.")


def s_hands(img, d, t):
    header(d, "HAND TYPES", "Weakest to strongest; a play counts as the strongest it makes")
    for c, rows in enumerate((HANDS[:6], HANDS[6:])):
        x0 = 120 + c * 860
        for r, (cd, name, base, mult, gb, gm) in enumerate(rows):
            k = c * 6 + r
            a = prog(t, 0.3 + 0.08 * k, 0.8 + 0.08 * k)
            y = 270 + r * 100
            star = k >= 9
            d.rounded_rectangle([x0, y - 40, x0 + 800, y + 40], 18, fill=(0, 0, 0, int(80 * a)),
                                outline=GOLD + (int(255 * a * prog(t, 5.0, 5.5)),) if star else None, width=3)
            text(d, (x0 + 24, y), cd, mono(28, bold=True), GOLD, a, anchor="lm")
            text(d, (x0 + 110, y), name, font(36, "demi"), CREAM, a, anchor="lm")
            text(d, (x0 + 560, y), f"{base} × {mult}", font(38, "bold"), CREAM, a, anchor="rm")
            text(d, (x0 + 780, y), f"+{gb} / +{gm}", font(28, "medium"), MUTED, a, anchor="rm")
    text(d, (1760, 215), "base × mult       per level", font(22, "demi"), MUTED, prog(t, 0.3, 0.8), anchor="rm")
    caption(img, d, t, 0.4, 4.7, "Twelve hand types. Levelling one up in the shop raises its base and mult.")
    caption(img, d, t, 5.0, 9.3, "The last three need duplicate cards, which you make by painting or promoting.")


def s_run(img, d, t):
    header(d, "THE RUN", "Eight rounds, and every target doubles")
    targets = generate(1).targets
    for i, tg in enumerate(targets):
        a = prog(t, 0.3 + 0.25 * i, 0.8 + 0.25 * i)
        h = 40 + 380 * (i + 1) / 8
        x = 170 + i * 205
        d.rounded_rectangle([x, 700 - h, x + 150, 700], 16, fill=(GOLD if i == 7 else (52, 120, 92)) + (int(255 * a),))
        text(d, (x + 75, 730), f"Round {i + 1}", font(26, "demi"), MUTED, a)
        text(d, (x + 75, 690 - h), f"{tg:,}", font(38, "bold"), CREAM, a, anchor="ms")
        if i:
            text(d, (x - 28, 640), "×2", font(24, "bold"), GOLD, a * 0.8)
    a = prog(t, 4.8, 5.4)
    text(d, (960, 820), "Clear a round: +$4,  +$1 per unused play,  +$1 per $5 you hold (up to $5)",
         font(36, "demi"), GOLD, a)
    text(d, (960, 875), "Miss a target and the run is over.", font(32, "medium"), CREAM, a)
    caption(img, d, t, 0.3, 4.6, "The targets go from 300 to 38,400. Bar heights here aren't to scale.")
    caption(img, d, t, 4.9, 9.3, "You can end a round early once its target is met, to bank the unused plays.")


SHORT = {
    "rose_tint": "+3 mult per scored heart or diamond",
    "coal_seam": "+3 mult per scored spade or club",
    "low_road": "+12 base per scored 2-6",
    "high_court": "+3 mult per scored J, Q, K",
    "ace_dividend": "+$2 per scored ace",
    "twin_engine": "+4 mult per pair of ranks scored",
    "short_hand": "x2 if you play 3 or fewer cards",
    "full_spread": "+40 base if you play 5 cards",
    "last_call": "x3 on a round's last play",
    "momentum": "+2 mult per same hand type in a row",
    "collector": "+10 base for good per straight",
    "thin_deck": "+1 mult per card below 52",
    "mirror": "scored chips count twice",
    "tricolor": "x2 if scored cards have 3+ suits",
    "savings": "interest cap +$5",
    "spare_hands": "+$2 per unused discard",
    "odd_job": "+2 mult per scored A, 3, 5, 7, 9",
    "even_split": "+15 base per scored 2, 4, 6, 8, 10",
}
TIMES = {"short_hand", "last_call", "tricolor"}


def s_mods(img, d, t):
    header(d, "MODIFIERS", "Hold up to 5. Each one changes how plays score.")
    for k, name in enumerate(MODIFIERS):
        row, col = divmod(k, 6)
        a = prog(t, 0.3 + 0.06 * k, 0.8 + 0.06 * k)
        x, y = 95 + col * 292, 235 + row * 215
        hot = prog(t, 5.0, 5.6) if name in TIMES else 0
        if hot:
            gl, gp = glow(272, 190, 22, GOLD)
            paste(img, gl, x - gp, y - gp, hot * 0.8)
        d.rounded_rectangle([x, y, x + 272, y + 190], 22, fill=(70, 22, 30, int(235 * a)),
                            outline=(GOLD if hot else (255, 255, 255)) + (int((255 if hot else 40) * a),), width=3)
        text(d, (x + 20, y + 36), name.replace("_", " "), font(30, "heavy"), CREAM, a, anchor="lm")
        f = font(23, "medium")
        for i, line in enumerate(wrap(SHORT[name], f, 235, d)):
            text(d, (x + 20, y + 82 + i * 32), line, f, MUTED, a, anchor="lm")
        text(d, (x + 20, y + 160), f"${MODIFIERS[name][0]}", font(32, "bold"), GOLD, a, anchor="lm")
    caption(img, d, t, 0.4, 4.7, "Eighteen modifiers: extra base, extra mult, money, and multipliers.")
    caption(img, d, t, 5.0, 12.3, "Multipliers like x2 and x3 stack, and the late targets need them.")


def s_shop(img, d, t):
    header(d, "THE SHOP", "After every cleared round")
    queue = generate(7).offers[0]
    a = prog(t, 0.3, 0.9)
    text(d, (90, 245), "OFFERS  (3 at a time from a queue you can see)", font(24, "demi"), MUTED, a, anchor="lm")
    for i, name in enumerate(queue[:3]):
        x = 90 + i * 300
        d.rounded_rectangle([x, 275, x + 280, 455], 20, fill=(70, 22, 30, int(235 * a)))
        text(d, (x + 20, 310), name.replace("_", " "), font(30, "heavy"), CREAM, a, anchor="lm")
        for k, line in enumerate(wrap(SHORT[name], font(22, "medium"), 240, d)):
            text(d, (x + 20, 352 + k * 30), line, font(22, "medium"), MUTED, a, anchor="lm")
        text(d, (x + 20, 425), f"${MODIFIERS[name][0]}", font(30, "bold"), GOLD, a, anchor="lm")
    a2 = prog(t, 1.2, 1.8)
    for i, name in enumerate(queue[3:9]):
        x = 90 + i * 150
        d.rounded_rectangle([x, 480, x + 140, 520], 20, fill=(0, 0, 0, int(90 * a2)))
        text(d, (x + 70, 500), name.replace("_", " "), font(18, "demi"), MUTED, a2)
    items = [("reroll", "next three offers", "$2, +1 each"), ("level CODE", "level up a hand type", "$2 + 2 x level"),
             ("discards", "+1 discard every round", "$8, $16..."), ("handsize", "+1 hand size (max 10)", "$10, $20..."),
             ("sell NAME", "half the price back", "")]
    for i, (cmd, what, price) in enumerate(items):
        ai = prog(t, 2.0 + 0.3 * i, 2.5 + 0.3 * i)
        y = 600 + i * 62
        text(d, (90, y), cmd, mono(28, bold=True), GOLD, ai, anchor="lm")
        text(d, (360, y), what, font(28, "medium"), CREAM, ai, anchor="lm")
        text(d, (900, y), price, font(26, "demi"), MUTED, ai, anchor="rm")
    # deck editing demo on the right
    panel(d, [1040, 240, 1830, 900], prog(t, 4.6, 5.2))
    ab = prog(t, 4.8, 5.4)
    text(d, (1080, 285), "DECK EDITING  ($3 each)", font(26, "heavy"), GOLD, ab, anchor="lm")
    paint, promote, remove = prog(t, 6.4, 7.0), prog(t, 8.2, 8.8), prog(t, 10.0, 10.8)
    face = "7S" if paint < 0.5 else ("7H" if promote < 0.5 else "8H")
    label = "paint 5 H" if t < 8.2 else ("promote 5" if t < 10.0 else "remove 5")
    flip = abs(1 - 2 * paint) if 0 < paint < 1 else abs(1 - 2 * promote) if 0 < promote < 1 else 1
    draw_card(img, face, 1435, 540, w=200, alpha=ab * (1 - remove), flip=max(0.05, flip))
    text(d, (1435, 720), "id 5", mono(28, bold=True), MUTED, ab * (1 - remove))
    text(d, (1435, 800), label, mono(36, bold=True), CREAM, ab * prog(t, 6.0, 6.4))
    text(d, (1435, 850), "the card is gone from your deck" if t >= 10.0 else "its id stays 5",
         font(26, "medium"), MUTED, ab * prog(t, 6.2, 6.6))
    caption(img, d, t, 0.3, 4.4, "Buy modifiers from the offers. Reroll to see the next three in the queue.")
    caption(img, d, t, 4.6, 9.9, "Edit your deck: paint a card's suit, or promote its rank...")
    caption(img, d, t, 10.1, 13.2, "...or remove it, so the cards you want come up more often.")


def s_score(img, d, t):
    header(d, "YOUR SCORE", "per test case")
    rows = [("Score", "total points of all your plays across the run"),
            ("Missed target", "the run ends, but the points you scored still count"),
            ("Illegal move", "anywhere in the output: that test scores 0"),
            ("Time limit", "2 seconds per test")]
    for i, (k, v) in enumerate(rows):
        a = prog(t, 0.3 + 0.5 * i, 0.9 + 0.5 * i)
        y = 330 + i * 130
        text(d, (300, y), k, font(56, "heavy"), GOLD, a, anchor="lm")
        text(d, (820, y), v, font(40, "medium"), CREAM, a, anchor="lm")
    caption(img, d, t, 0.4, 7.2, "Go deep into the run, but every move has to be legal.")


# ---- screen capture scenes --------------------------------------------------


class Capture:
    """A captured timeline played back with a moving cursor."""

    CLICK = 0.55

    def __init__(self, folder, fast_hold=0.2):
        self.folder = Path(folder)
        tl = json.loads((self.folder / "timeline.json").read_text())
        self.steps = tl["steps"]
        self.starts = []
        t = 0.0
        for s in self.steps:
            self.starts.append(t)
            t += self.CLICK if s.get("click") else (fast_hold if s.get("fast") else s.get("hold", 1.0))
        self.duration = t + 0.6
        self.cache = {}

    def image(self, i):
        if i not in self.cache:
            self.cache = {i: Image.open(self.folder / self.steps[i]["img"]).convert("RGB")}
        return self.cache[i]

    def index(self, t):
        i = 0
        while i + 1 < len(self.starts) and self.starts[i + 1] <= t:
            i += 1
        return i

    def first(self, pred):
        return next((self.starts[i] for i, s in enumerate(self.steps) if pred(s)), None)

    def cursor_at(self, i):
        for k in range(i, -1, -1):
            if "cursor" in self.steps[k]:
                return self.steps[k]["cursor"]
        return [960, 540]

    def draw(self, img, t):
        i = self.index(t)
        img.paste(self.image(i), (0, 0))
        s = self.steps[i]
        if s.get("fast"):
            return
        d = ImageDraw.Draw(img, "RGBA")
        local = t - self.starts[i]
        target = self.cursor_at(i)
        prev = self.cursor_at(i - 1) if i else target
        if s.get("click"):
            f = ease(local / 0.32)
            x, y = lerp(prev[0], target[0], f), lerp(prev[1], target[1], f)
            ring = clamp((local - 0.32) / 0.2)
            if 0 < ring < 1:
                r = 14 + 26 * ring
                d.ellipse([x - r, y - r, x + r, y + r], outline=GOLD + (int(255 * (1 - ring)),), width=4)
        else:
            x, y = target
        pointer = [(x, y), (x, y + 34), (x + 9, y + 26), (x + 16, y + 41), (x + 22, y + 38), (x + 15, y + 24),
                   (x + 26, y + 23)]
        d.polygon([(px + 2, py + 3) for px, py in pointer], fill=(0, 0, 0, 90))
        d.polygon(pointer, fill=(255, 255, 255, 255), outline=(20, 20, 20, 255))


def solid_caption(img, d, t, a, b, s):
    """A caption with an opaque box, for use over screen captures."""
    al = window(t, a, b)
    if al <= 0:
        return
    f = font(42, "demi")
    w = d.textlength(s, font=f)
    d.rounded_rectangle([960 - w / 2 - 34, 952, 960 + w / 2 + 34, 1024], 36, fill=(8, 26, 20, int(235 * al)),
                        outline=GOLD + (int(160 * al),), width=2)
    text(d, (960, 988), s, f, CREAM, al)


def badge(d, label, alpha=1.0):
    w = d.textlength(label, font=font(26, "heavy")) + 44
    d.rounded_rectangle([1880 - w, 990 - 150, 1880, 990 - 100], 25, fill=GOLD + (int(240 * alpha),))
    text(d, (1880 - w / 2, 990 - 125), label, font(26, "heavy"), (20, 30, 24), alpha)


def capture_scene(cap, captions_fn, label):
    def scene(img, d, t):
        cap.draw(img, t)
        d2 = ImageDraw.Draw(img, "RGBA")
        badge(d2, label)
        for a, b, s in captions_fn(cap):
            solid_caption(img, d2, t, a, b, s)
    scene.duration = cap.duration
    return scene


def manual_captions(cap):
    first_click = cap.first(lambda s: s.get("click")) or 0.8
    play_shot = cap.first(lambda s: str(s.get("move", "")).startswith("play")) or 10
    shop = cap.first(lambda s: s.get("move") == "end") or 14
    paint = cap.first(lambda s: str(s.get("move", "")).startswith("paint")) or shop + 6
    end = cap.duration
    return [
        (0.1, first_click, "Open poker_run/web/index.html, pick a seed and deal."),
        (first_click, play_shot - 6.5, "Click cards to select them. The draw pile shows what you'll get next."),
        (play_shot - 6.3, play_shot + 0.6, "Before you commit, the preview shows the hand type and exact points."),
        (play_shot + 0.8, shop + 0.6, "Round 1 cleared. End it early to bank the unused plays as money."),
        (shop + 0.8, paint - 2.4, "In the shop: buy from the offers, or reroll for the next three."),
        (paint - 2.2, end, "Pick a card in the deck to paint, promote or remove it. Then on to round 2."),
    ]


def run_captions(cap):
    replay = cap.first(lambda s: s.get("section") == "replay") or cap.duration - 8
    fast_end = replay - 4
    v = DEMO["verdict"]
    return [
        (0.1, fast_end, f"The beam search solver's whole run of seed 7, sped up: {v['cleared']} of 8 rounds cleared."),
        (fast_end + 0.2, replay, "Replay mode loads any output file..."),
        (replay + 0.2, cap.duration, "...checks it and lets you step through it move by move."),
    ]


# ---- solvers ------------------------------------------------------------------


def s_solver(img, d, t):
    header(d, "WRITE A SOLVER", "poker_run/starters/starter.py: one file with the judge's simulator")
    code_lines = [
        ("def solve(inst, deadline):", BLUE),
        ("    game = Game(inst)", CREAM),
        ("    moves = []", CREAM),
        ("    while not game.over:", CREAM),
        ("        trial = game.clone()       # try ideas", MUTED),
        ("        trial.apply(\"play 3 17\")   # on a copy", MUTED),
        ("        move = pick_best(game)", CREAM),
        ("        game.apply(move)", CREAM),
        ("        moves.append(move)", CREAM),
        ("    return moves", CREAM),
    ]
    panel(d, [90, 220, 980, 820], prog(t, 0.2, 0.7))
    n = int(len(code_lines) * prog(t, 0.4, 3.2, lambda x: clamp(x))) + (1 if t > 0.4 else 0)
    for i, (line, color) in enumerate(code_lines[:n]):
        text(d, (130, 290 + i * 50), line, mono(27), color, anchor="lm")
    cmds = [("gen --seed 7 > in.txt", "make a test case"),
            ("check in.txt out.txt", "score an output: JSON verdict"),
            ("batch --seeds 1-10 --solver ...", "run your solver on many seeds"),
            ("versus --solvers A B ...", "head to head (next)")]
    panel(d, [1030, 220, 1830, 820], prog(t, 3.4, 3.9))
    text(d, (1070, 270), "python -m poker_run ...", mono(26), MUTED, prog(t, 3.5, 4.0), anchor="lm")
    for i, (c, note) in enumerate(cmds):
        a = prog(t, 3.8 + 0.5 * i, 4.3 + 0.5 * i)
        y = 360 + i * 115
        text(d, (1070, y), c, mono(28, bold=True), CREAM, a, anchor="lm")
        text(d, (1070, y + 42), note, font(26, "medium"), MUTED, a, anchor="lm")
    caption(img, d, t, 0.3, 4.3, "The starter has the judge's exact simulator. Clone the game to test ideas.")
    caption(img, d, t, 4.5, 10.3, "The command line makes test cases, scores outputs and runs whole batches.")


ALGOS = [
    ("Greedy", "Best play right now; discard junk when it's short of the target. The baseline."),
    ("Beam search", "Keep the best few move sequences at each step, and expand only those."),
    ("Monte Carlo", "Try each candidate move with many random playouts; keep the best."),
    ("MCTS", "Grow a search tree toward moves whose playouts score well (UCT)."),
    ("Tuned", "A heuristic with weights found by an evolution strategy on training seeds."),
]


def s_algos(img, d, t):
    header(d, "EXAMPLE SOLVERS", f"poker_run/solvers/   measured on seeds {DATA['seeds']}, 2 s per test")
    best = max(DATA["solvers"][n]["summary"]["mean"] for n, _ in ALGOS)
    for i, (name, idea) in enumerate(ALGOS):
        s = DATA["solvers"][name]["summary"]
        a = prog(t, 0.3 + 0.4 * i, 0.9 + 0.4 * i)
        y = 255 + i * 130
        text(d, (90, y), name, font(40, "heavy"), GOLD if i else CREAM, a, anchor="lm")
        f = font(25, "medium")
        for k, line in enumerate(wrap(idea, f, 560, d)[:2]):
            text(d, (90, y + 40 + k * 30), line, f, MUTED, a, anchor="lm")
        g = prog(t, 3.0 + 0.3 * i, 4.4 + 0.3 * i)
        bw = 760 * s["mean"] / best * g
        d.rounded_rectangle([720, y - 22, 720 + 760, y + 26], 12, fill=(0, 0, 0, int(70 * a)))
        if bw > 4:
            d.rounded_rectangle([720, y - 22, 720 + bw, y + 26], 12, fill=GOLD if s["mean"] == best else (110, 170, 140))
        text(d, (1500, y), f"{s['mean'] * g:,.0f}", font(36, "bold"), CREAM, a * clamp(g * 3), anchor="lm")
        text(d, (1500, y + 40), f"{s['mean_cleared']:.1f} rounds cleared", font(24, "medium"), MUTED,
             a * clamp(g * 3), anchor="lm")
    text(d, (720, 210), "MEAN SCORE", font(22, "demi"), MUTED, prog(t, 2.8, 3.3), anchor="lm")
    caption(img, d, t, 0.3, 5.0, "Five example solvers, from a one-step greedy to tree search.")
    caption(img, d, t, 5.2, 12.3, "Read them, run them, then beat them. Planning ahead is worth a lot.")


def s_versus(img, d, t):
    header(d, "1 V 1 AND MORE", "python -m poker_run versus --solvers A B C ...")
    ranking = DATA["ranking"]
    a_name, b_name = ranking[0]["name"], ranking[1]["name"]
    sa, sb = DATA["solvers"][a_name]["scores"], DATA["solvers"][b_name]["scores"]
    n = len(sa)
    duel = 1 - prog(t, 8.4, 8.9)
    top = max(max(sa[:n]), max(sb[:n]))
    tally = [0, 0]
    for i in range(n):
        ai = prog(t, 0.6 + 0.3 * i, 1.0 + 0.3 * i) * duel
        x = 130 + i * (1660 / n)
        ha, hb = 300 * sa[i] / top * ai, 300 * sb[i] / top * ai
        win_a, win_b = sa[i] > sb[i], sb[i] > sa[i]
        if ai >= 1:
            tally[0] += win_a
            tally[1] += win_b
        bw = 1660 / n * 0.36
        d.rounded_rectangle([x, 640 - ha, x + bw, 640], 6, fill=(GOLD if win_a else (110, 170, 140)) + (int(255 * ai),))
        d.rounded_rectangle([x + bw + 4, 640 - hb, x + 2 * bw + 4, 640], 6,
                            fill=(BLUE if win_b else (80, 110, 140)) + (int(255 * ai),))
        text(d, (x + bw + 2, 675), str(i + 1), font(22, "demi"), MUTED, ai)
    text(d, (200, 250), a_name, font(44, "heavy"), GOLD, duel, anchor="lm")
    text(d, (1720, 250), b_name, font(44, "heavy"), BLUE, duel, anchor="rm")
    text(d, (960, 250), f"{tally[0]}  -  {tally[1]}", font(72, "heavy"), CREAM, duel * prog(t, 0.6, 1.0))
    text(d, (40, 675), "seed", font(22, "demi"), MUTED, duel * prog(t, 0.6, 1.0), anchor="lm")
    text(d, (960, 780), "Same seeds for both. Higher score takes the seed; brighter bar = winner.", font(30, "medium"), MUTED,
         duel * prog(t, 1.0, 1.5))
    # the N-way table
    m = prog(t, 9.0, 9.6)
    if m > 0:
        names = [r["name"] for r in ranking]
        cw = 230
        x0 = 960 - (cw * (len(names) + 1)) / 2
        text(d, (x0, 245), f"Seeds won head to head, row vs column ({DATA['seeds']})", font(26, "demi"), MUTED, m,
             anchor="lm")
        for j, nm in enumerate(names):
            text(d, (x0 + cw * (j + 1.5), 310), nm, font(26, "heavy"), CREAM, m)
        for i, row in enumerate(names):
            y = 380 + i * 75
            text(d, (x0, y), f"{i + 1}. {row}", font(28, "heavy"), GOLD if i == 0 else CREAM, m, anchor="lm")
            for j, col in enumerate(names):
                w = DATA["wins"][row][col]
                cell = "-" if row == col else str(w)
                strong = row != col and w > DATA["wins"][col][row]
                text(d, (x0 + cw * (j + 1.5), y), cell, font(34, "bold"), (GOLD if strong else MUTED), m)
    caption(img, d, t, 0.3, 8.4, f"1v1: {a_name} against {b_name}, seed by seed.")
    caption(img, d, t, 8.8, 15.3, "With more solvers: a ranking by average place and every head-to-head result.")


def s_end(img, d, t):
    a = prog(t, 0.2, 0.9)
    text(d, (960, 230), "POKER RUN", font(130, "heavy"), CREAM, a)
    text(d, (960, 335), "Plan the run. Beat the solvers.", font(46, "medium"), GOLD, prog(t, 0.6, 1.2))
    panel(d, [360, 420, 1560, 850], a)
    cmds = [("open poker_run/web/index.html", "play by hand, replay outputs"),
            ("python -m poker_run.baseline < in.txt", "the greedy baseline"),
            ("python -m poker_run batch --solver \"python3 my.py\"", "score your solver"),
            ("python -m poker_run versus --solvers A B", "go head to head")]
    for k, (c, note) in enumerate(cmds):
        ak = prog(t, 1.0 + 0.3 * k, 1.5 + 0.3 * k)
        y = 485 + k * 92
        text(d, (410, y), "$", mono(30), MUTED, ak, anchor="lm")
        text(d, (450, y), c, mono(30), CREAM, ak, anchor="lm")
        text(d, (450, y + 36), note, font(22, "medium"), MUTED, ak, anchor="lm")
    text(d, (960, 900), "Rules: poker_run/problem/statement.md", font(28, "medium"), MUTED, prog(t, 2.4, 3.0))


def build_scenes(manual_dir, run_dir):
    manual = Capture(manual_dir)
    run = Capture(run_dir, fast_hold=0.22)
    return [
        (s_title, 6.0),
        (s_idea, 11.0),
        (s_cards, 9.5),
        (s_round, 15.0),
        (s_scoring, 13.0),
        (s_hands, 9.6),
        (s_run, 9.6),
        (s_mods, 12.6),
        (s_shop, 13.5),
        (s_score, 7.5),
        (capture_scene(manual, manual_captions, "PLAY BY HAND"), manual.duration),
        (capture_scene(run, run_captions, "REPLAY"), run.duration),
        (s_solver, 10.6),
        (s_algos, 12.6),
        (s_versus, 15.6),
        (s_end, 8.0),
    ]


BLACK = Image.new("RGB", (W, H), (0, 0, 0))


def render_frame(scene, dur, t, first, last):
    img = BG.copy()
    d = ImageDraw.Draw(img, "RGBA")
    scene(img, d, t)
    a = min(1.0, t / 0.4, (dur - t) / 0.4)
    if a < 1:
        base = BLACK if (first and t < dur / 2) or (last and t > dur / 2) else BG
        img = Image.blend(base, img, clamp(a))
    return img


def stills(scenes, out):
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    for n, (scene, dur) in enumerate(scenes):
        for frac in (0.3, 0.6, 0.9):
            t = dur * frac
            render_frame(scene, dur, t, False, False).save(out / f"{n:02d}_{scene.__name__}_{t:05.1f}.png")


def video(scenes, out):
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    frames = [int(round(dur * FPS)) for _, dur in scenes]
    total = sum(frames)
    track = Path(tempfile.mkdtemp()) / "music.wav"
    music.write_wav(track, music.render(total / FPS, variation=True))
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(track), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    done = 0
    for n, ((scene, dur), count) in enumerate(zip(scenes, frames)):
        for f in range(count):
            proc.stdin.write(render_frame(scene, dur, f / FPS, n == 0, n == len(scenes) - 1).tobytes())
            done += 1
            if done % 300 == 0:
                print(f"frame {done}/{total}", flush=True)
    proc.stdin.close()
    if proc.wait():
        sys.exit("ffmpeg failed")
    print(f"wrote {out}")
    webm = out.with_suffix(".webm")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(out), "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32",
                    "-row-mt", "1", "-cpu-used", "4", "-c:a", "libopus", "-b:a", "160k", str(webm)], check=True)
    print(f"wrote {webm}")


def main():
    manual_dir, run_dir = sys.argv[1], sys.argv[2]
    scenes = build_scenes(manual_dir, run_dir)
    for scene, _ in scenes:
        if not hasattr(scene, "__name__"):
            scene.__name__ = "capture"
    if sys.argv[3:4] == ["stills"]:
        stills(scenes, Path(sys.argv[4]) if len(sys.argv) > 4 else HERE / "stills_run")
    else:
        video(scenes, HERE / "poker_run_explainer.mp4")


if __name__ == "__main__":
    main()

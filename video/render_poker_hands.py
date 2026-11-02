"""Render the Poker Hands explainer video.

Needs Pillow, numpy and imageio-ffmpeg (pip install Pillow numpy imageio-ffmpeg).
The soundtrack is synthesized by music.py.

    python video/render_poker_hands.py              # writes video/poker_hands_explainer.mp4 and .webm
    python video/render_poker_hands.py stills       # a few PNG frames per scene, for checking

The game shown is real: seed 14 with the default config, played by the
"shopper" strategy in bots/greedy_bot.py.
"""

import math
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
HERE = Path(__file__).resolve().parent

# ---- look -------------------------------------------------------------------

FELT_IN = (24, 84, 62)
FELT_OUT = (8, 32, 24)
CREAM = (244, 239, 227)
MUTED = (160, 190, 176)
GOLD = (242, 193, 78)
RED = (226, 88, 76)
CARD_FACE = (250, 247, 240)
SUIT_RED = (200, 40, 48)
SUIT_BLACK = (28, 28, 30)
PANEL = (0, 0, 0, 90)

AVENIR = "/System/Library/Fonts/Avenir Next.ttc"
MENLO = "/System/Library/Fonts/Menlo.ttc"
SYMBOLS = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
WEIGHT = {"bold": 0, "demi": 2, "medium": 5, "regular": 7, "heavy": 8}


@lru_cache(None)
def font(size, weight="demi"):
    return ImageFont.truetype(AVENIR, int(size), index=WEIGHT[weight])


@lru_cache(None)
def mono(size, bold=False):
    return ImageFont.truetype(MENLO, int(size), index=1 if bold else 0)


@lru_cache(None)
def symbols(size):
    return ImageFont.truetype(SYMBOLS, int(size))


def make_background():
    grad = Image.radial_gradient("L").resize((W, H), Image.Resampling.BICUBIC)
    grad = grad.point(lambda v: min(255, int(v * 1.25)))
    return Image.composite(Image.new("RGB", (W, H), FELT_OUT), Image.new("RGB", (W, H), FELT_IN), grad)


BG = make_background()

# ---- timing -----------------------------------------------------------------


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(x):
    x = clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out(x):
    return 1 - (1 - clamp(x)) ** 3


def ease_back(x):
    x = clamp(x)
    c = 1.9
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def prog(t, a, b, fn=ease):
    return fn((t - a) / (b - a))


def window(t, a, b, fade=0.3):
    """1 inside [a, b], fading in and out at the edges."""
    return clamp((t - a) / fade) * clamp((b - t) / fade)


def lerp(a, b, x):
    return a + (b - a) * x


# ---- cards ------------------------------------------------------------------

SUIT_GLYPH = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}


def rank_label(code):
    return "10" if code[0] == "T" else code[0]


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    return m


@lru_cache(None)
def card_face(code, w):
    h = int(w * 1.4)
    s = w / 150
    im = Image.new("RGBA", (w, h), CARD_FACE + (255,))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], int(16 * s), outline=(205, 198, 186), width=max(1, int(2 * s)))
    color = SUIT_RED if code[1] in "HD" else SUIT_BLACK
    glyph = SUIT_GLYPH[code[1]]
    if w >= 90:
        d.text((int(16 * s), int(8 * s)), rank_label(code), font=font(46 * s, "bold"), fill=color, anchor="lt")
        d.text((int(31 * s), int(78 * s)), glyph, font=symbols(34 * s), fill=color, anchor="mm")
        d.text((w * 0.56, h * 0.58), glyph, font=symbols(100 * s), fill=color, anchor="mm")
    else:
        d.text((w / 2, h * 0.34), rank_label(code), font=font(w * 0.56, "bold"), fill=color, anchor="mm")
        d.text((w / 2, h * 0.74), glyph, font=symbols(w * 0.5), fill=color, anchor="mm")
    im.putalpha(rounded_mask(w, h, int(16 * s)))
    return im


@lru_cache(None)
def card_back(w):
    h = int(w * 1.4)
    s = w / 150
    im = Image.new("RGBA", (w, h), (122, 31, 43, 255))
    d = ImageDraw.Draw(im)
    step = max(6, int(16 * s))
    for k in range(-h, w + h, step):
        d.line([(k, 0), (k + h, h)], fill=(142, 44, 58), width=max(1, int(3 * s)))
        d.line([(k, h), (k + h, 0)], fill=(142, 44, 58), width=max(1, int(3 * s)))
    inset = int(10 * s)
    d.rounded_rectangle([inset, inset, w - 1 - inset, h - 1 - inset], int(10 * s), outline=GOLD, width=max(1, int(3 * s)))
    d.rounded_rectangle([0, 0, w - 1, h - 1], int(16 * s), outline=(70, 14, 22), width=max(1, int(2 * s)))
    im.putalpha(rounded_mask(w, h, int(16 * s)))
    return im


@lru_cache(None)
def shadow(w, h, r):
    pad = 24
    im = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(im).rounded_rectangle([pad, pad, pad + w, pad + h], r, fill=120)
    return im.filter(ImageFilter.GaussianBlur(10)), pad


@lru_cache(None)
def glow(w, h, r, color):
    pad = 30
    im = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), color + (0,))
    a = Image.new("L", im.size, 0)
    ImageDraw.Draw(a).rounded_rectangle([pad - 9, pad - 9, pad + w + 9, pad + h + 9], r + 8, fill=230)
    im.putalpha(a.filter(ImageFilter.GaussianBlur(9)))
    return im, pad


def paste(img, src, x, y, alpha=1.0):
    if alpha <= 0:
        return
    mask = src.getchannel("A")
    if alpha < 1:
        mask = mask.point(lambda v: int(v * alpha))
    img.paste(src, (int(round(x)), int(round(y))), mask)


def paste_mask(img, color, mask, x, y, alpha=1.0):
    if alpha < 1:
        mask = mask.point(lambda v: int(v * alpha))
    img.paste(color, (int(round(x)), int(round(y)), int(round(x)) + mask.width, int(round(y)) + mask.height), mask)


def draw_card(img, code, cx, cy, w=150, alpha=1.0, glow_amt=0.0, glow_color=GOLD, back=False, flip=1.0):
    src = card_back(w) if back else card_face(code, w)
    if flip < 1:
        src = src.resize((max(2, int(src.width * flip)), src.height), Image.Resampling.BILINEAR)
    cw, ch = src.size
    r = int(16 * w / 150)
    x, y = cx - cw / 2, cy - ch / 2
    sh, pad = shadow(cw, ch, r)
    paste_mask(img, (0, 0, 0), sh, x - pad + 4, y - pad + 10, alpha)
    if glow_amt > 0:
        g, gp = glow(cw, ch, r, glow_color)
        paste(img, g, x - gp, y - gp, alpha * glow_amt)
    paste(img, src, x, y, alpha)


@lru_cache(None)
def fanned_card(code, w, angle):
    face = card_face(code, w)
    pad = 30
    im = Image.new("RGBA", (face.width + 2 * pad, face.height + 2 * pad), (0, 0, 0, 0))
    sh, sp = shadow(face.width, face.height, int(16 * w / 150))
    dark = Image.new("RGBA", sh.size, (0, 0, 0, 0))
    dark.putalpha(sh)
    im.alpha_composite(dark, (pad - sp + 4, pad - sp + 12))
    im.alpha_composite(face, (pad, pad))
    return im.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)


# ---- text and chrome --------------------------------------------------------


def text(d, xy, s, f, fill=CREAM, alpha=1.0, anchor="mm"):
    if alpha <= 0:
        return
    d.text(xy, s, font=f, fill=tuple(fill[:3]) + (int(255 * alpha),), anchor=anchor)


@lru_cache(None)
def text_image(s, size, weight, color):
    f = font(size, weight)
    l, t, r, b = f.getbbox(s)
    im = Image.new("RGBA", (r - l + 20, b - t + 20), color + (0,))
    ImageDraw.Draw(im).text((10 - l, 10 - t), s, font=f, fill=color + (255,))
    return im


def pop_text(img, s, cx, cy, size, x, weight="heavy", color=GOLD, fade=1.0):
    """Text that scales up from 60% with a little overshoot as x goes 0 -> 1."""
    if x <= 0 or fade <= 0:
        return
    im = text_image(s, size, weight, color)
    k = lerp(0.6, 1.0, ease_back(x))
    if k != 1:
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.Resampling.BICUBIC)
    paste(img, im, cx - im.width / 2, cy - im.height / 2, clamp(x * 2) * fade)


def caption(img, d, t, a, b, s):
    al = window(t, a, b)
    if al <= 0:
        return
    f = font(44, "demi")
    w = d.textlength(s, font=f)
    d.rounded_rectangle([960 - w / 2 - 34, 950, 960 + w / 2 + 34, 1026], 38, fill=(0, 0, 0, int(120 * al)))
    text(d, (960, 988), s, f, CREAM, al)


def header(d, title, tag=None, alpha=1.0):
    text(d, (90, 92), title, font(52, "heavy"), CREAM, alpha, anchor="ls")
    d.rectangle([90, 108, 150, 114], fill=GOLD + (int(255 * alpha),))
    if tag:
        text(d, (90, 152), tag, font(28, "medium"), MUTED, alpha, anchor="ls")


def chip(d, x, y, label, value, alpha=1.0, w=180, hot=0.0):
    fill = (0, 0, 0, int(95 * alpha))
    d.rounded_rectangle([x, y, x + w, y + 84], 20, fill=fill)
    if hot > 0:
        d.rounded_rectangle([x, y, x + w, y + 84], 20, outline=GOLD + (int(255 * hot * alpha),), width=3)
    text(d, (x + w / 2, y + 24), label, font(20, "demi"), MUTED, alpha)
    text(d, (x + w / 2, y + 58), str(value), font(38, "bold"), CREAM, alpha)


def hud(d, alpha=1.0, score=0, money=0, plays=8, discards=3, hot=()):
    if alpha <= 0:
        return
    items = [("SCORE", score), ("MONEY", f"${money}"), ("PLAYS LEFT", plays), ("DISCARDS", discards)]
    for i, (label, value) in enumerate(items):
        chip(d, 1050 + i * 200, 44, label, value, alpha, hot=1.0 if label in hot else 0.0)


def wrap(s, f, width, d):
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=f) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


# ---- the table --------------------------------------------------------------

DECK = (210, 690)
USED = (1745, 690)
HAND_Y = 690


def slot(i, n=7):
    return 960 + (i - (n - 1) / 2) * 180, HAND_Y


def table_chrome(d, deck_count, alpha=1.0):
    for (x, y), label in ((DECK, f"DECK  {deck_count}"), (USED, "USED")):
        d.rounded_rectangle([x - 78, y - 108, x + 78, y + 108], 16, outline=(255, 255, 255, int(50 * alpha)), width=3)
        text(d, (x, y + 145), label, font(24, "demi"), MUTED, alpha)


def draw_deck(img, deck_count, alpha=1.0):
    layers = min(4, max(0, deck_count // 12 + (1 if deck_count else 0)))
    for k in range(layers):
        draw_card(img, None, DECK[0] - k * 2, DECK[1] - k * 3, back=True, alpha=alpha)


def dealt(img, code, start, t, dest, dur=0.55):
    """A card travelling from the deck to `dest`, flipping face up as it lands."""
    if t < start:
        return False
    f = clamp((t - start) / dur)
    x = lerp(DECK[0], dest[0], ease_out(f))
    y = lerp(DECK[1], dest[1], ease_out(f)) - math.sin(f * math.pi) * 60
    g = clamp((f - 0.5) / 0.5)
    face_up = g >= 0.5
    flip = abs(math.cos(math.pi * g)) if g < 1 else 1.0
    draw_card(img, code, x, y, back=not face_up, flip=max(0.04, flip))
    return True


# ---- the real game (seed 14, shopper) ---------------------------------------

HAND0 = ["8S", "2C", "7C", "JC", "4C", "9D", "4H"]
DISCARD0 = [0, 1, 2, 3, 5]
DRAW1 = ["6H", "5H", "7H", "6S", "4D"]
HAND1 = ["4C", "4H", "6H", "5H", "7H", "6S", "4D"]
PLAY1 = [0, 1, 6, 2, 5]  # shown as 4 4 4 6 6
DRAW2 = ["8H", "5D", "AH", "KD", "9S"]

# (label, cards, result, score after, money after, plays after, discards after)
LOG = [
    ("Discard", ["2C", "7C", "8S", "9D", "JC"], "", 0, 0, 8, 2),
    ("Full House", ["4C", "4H", "4D", "6H", "6S"], "+60  $6", 60, 6, 7, 2),
    ("Buy  level:Pair", [], "-$4", 60, 2, 7, 2),
    ("Discard", ["7H", "8H", "9S", "KD", "AH"], "", 60, 2, 7, 1),
    ("Discard", ["3H", "6C", "9H", "JH", "QD"], "", 60, 2, 7, 0),
    ("Pair", ["5H", "5D"], "+15  $1", 75, 3, 6, 0),
    ("Three of a Kind", ["TS", "TD", "TC"], "+30  $3", 105, 6, 5, 0),
    ("Buy  level:High Card", [], "-$4", 105, 2, 5, 0),
    ("High Card", ["KS"], "+8  $1", 113, 3, 4, 0),
    ("Flush", ["6D", "8D", "2D", "AD", "7D"], "+45  $4", 158, 7, 3, 0),
    ("Buy  level:Flush", [], "-$4", 158, 3, 3, 0),
    ("Flush", ["7S", "2S", "4S", "5S", "QS"], "+68  $4", 226, 7, 2, 0),
    ("Pair", ["QC", "QH"], "+15  $1", 241, 8, 1, 0),
    ("High Card", ["5C"], "+8  $1", 249, 9, 0, 0),
]

POINTS = [
    ("High Card", 5, 1), ("Pair", 10, 1), ("Two Pair", 20, 2), ("Three of a Kind", 30, 3),
    ("Straight", 40, 4), ("Flush", 45, 4), ("Full House", 60, 6), ("Four of a Kind", 90, 8),
    ("Straight Flush", 120, 10), ("Five of a Kind*", 150, 12), ("Flush House*", 160, 14), ("Flush Five*", 200, 16),
]

# ---- scenes -----------------------------------------------------------------


def s_title(img, d, t):
    fan = ["TH", "JH", "QH", "KH", "AH"]
    for i, code in enumerate(fan):
        k = i - 2
        x = prog(t, 0.2 + 0.12 * i, 1.1 + 0.12 * i, ease_out)
        if x <= 0:
            continue
        im = fanned_card(code, 180, k * 9)
        cx, cy = 960 + k * 120, 760 + abs(k) ** 1.6 * 14 + (1 - x) * 520
        paste(img, im, cx - im.width / 2, cy - im.height / 2, clamp(x * 3))
    a = prog(t, 0.6, 1.4)
    text(d, (960, 270 - 20 * a), "POKER HANDS", font(150, "heavy"), CREAM, a)
    a2 = prog(t, 1.1, 1.8)
    text(d, (960, 395), "A poker-hand scoring game you play by writing a bot", font(46, "medium"), MUTED, a2)


def s_setup(img, d, t):
    header(d, "THE SETUP", "A real game: seed 14, played by the example bot")
    starts = [0.7 + 0.28 * i for i in range(7)]
    deck_count = 52 - sum(1 for s in starts if t >= s)
    table_chrome(d, deck_count)
    draw_deck(img, deck_count)
    for i, code in enumerate(HAND0):
        dealt(img, code, starts[i], t, slot(i))
    hud(d, prog(t, 3.6, 4.2), 0, 0, 8, 3)
    caption(img, d, t, 0.3, 3.6, "Shuffle a 52-card deck and draw 7 cards.")
    caption(img, d, t, 3.9, 7.9, "You get 8 plays and 3 discards. Score as many points as you can.")


def s_discard(img, d, t):
    header(d, "EACH TURN", "Play 1 to 5 cards, or discard 1 to 5 cards")
    starts = [4.7 + 0.28 * i for i in range(5)]
    deck_count = 45 - sum(1 for s in starts if t >= s)
    table_chrome(d, deck_count)
    draw_deck(img, deck_count)
    lift = prog(t, 1.6, 2.2)
    keep = [i for i in range(7) if i not in DISCARD0]
    for i, code in enumerate(HAND0):
        x, y = slot(i)
        if i in DISCARD0:
            j = DISCARD0.index(i)
            f = prog(t, 3.2 + 0.07 * j, 3.9 + 0.07 * j)
            x, y = lerp(x, USED[0], f), lerp(y - 45 * lift, USED[1], f)
            fade = 1 - prog(t, 4.0, 4.4)
            draw_card(img, code, x, y, alpha=fade, glow_amt=lift * (1 - f), glow_color=RED)
        else:
            f = prog(t, 4.0, 4.6)
            nx, _ = slot(keep.index(i))
            draw_card(img, code, lerp(x, nx, f), y)
    for k, code in enumerate(DRAW1):
        dealt(img, code, starts[k], t, slot(2 + k))
    hud(d, 1, 0, 0, 8, 3 if t < 3.6 else 2, hot=("DISCARDS",) if 3.6 <= t < 5.0 else ())
    caption(img, d, t, 0.2, 2.8, "Each turn you either PLAY or DISCARD 1 to 5 cards.")
    caption(img, d, t, 3.0, 5.2, "This hand is weak, so the bot discards five cards...")
    caption(img, d, t, 5.4, 8.6, "...and draws back up to 7. That used 1 of its 3 discards.")


def s_play(img, d, t):
    header(d, "EACH TURN", "Play 1 to 5 cards, or discard 1 to 5 cards")
    starts = [6.5 + 0.28 * i for i in range(5)]
    deck_count = 40 - sum(1 for s in starts if t >= s)
    table_chrome(d, deck_count)
    draw_deck(img, deck_count)
    lift = prog(t, 0.6, 1.2)
    to_row = prog(t, 1.6, 2.4)
    away = prog(t, 5.6, 6.3)
    keep = [i for i in range(7) if i not in PLAY1]
    for i, code in enumerate(HAND1):
        x, y = slot(i)
        if i in PLAY1:
            j = PLAY1.index(i)
            rx, ry = 960 + (j - 2) * 170, 385
            x, y = lerp(x, rx, to_row), lerp(y - 45 * lift, ry, to_row)
            f = clamp(away * 1.0)
            x, y = lerp(x, USED[0], f), lerp(y, USED[1], f)
            draw_card(img, code, x, y, alpha=1 - prog(t, 6.0, 6.4), glow_amt=lift * (1 - away))
        else:
            f = prog(t, 6.0, 6.5)
            nx, _ = slot(keep.index(i))
            draw_card(img, code, lerp(x, nx, f), y)
    for k, code in enumerate(DRAW2):
        dealt(img, code, starts[k], t, slot(2 + k))
    pop_text(img, "FULL HOUSE", 960, 205, 100, clamp((t - 2.5) / 0.5), fade=1 - prog(t, 5.5, 5.9))
    sub = window(t, 2.9, 5.8)
    text(d, (960, 538), "+60 points    +$6", font(46, "bold"), GOLD, sub)
    gain = prog(t, 2.8, 3.6)
    hud(d, 1, int(round(60 * gain)), int(round(6 * gain)), 8 if t < 5.8 else 7, 2,
        hot=(("SCORE", "MONEY") if 2.8 <= t < 4.2 else ()) + (("PLAYS LEFT",) if 5.8 <= t < 7.0 else ()))
    caption(img, d, t, 0.2, 2.6, "Now there's a full house. The bot plays those five cards.")
    caption(img, d, t, 2.9, 5.6, "It scores as its best hand type: +60 points and $6.")
    caption(img, d, t, 5.9, 8.8, "Played cards are gone for good. Draw back up to 7.")


def s_points(img, d, t):
    header(d, "HAND TYPES", "Points and money for each play (default settings)")
    cols = [(150, POINTS[:6]), (1000, POINTS[6:])]
    for c, (x0, rows) in enumerate(cols):
        hy = 245
        text(d, (x0 + 30, hy), "HAND", font(24, "demi"), MUTED, anchor="lm")
        text(d, (x0 + 560, hy), "POINTS", font(24, "demi"), MUTED, anchor="rm")
        text(d, (x0 + 740, hy), "MONEY", font(24, "demi"), MUTED, anchor="rm")
        for r, (name, pts, money) in enumerate(rows):
            k = c * 6 + r
            a = prog(t, 0.3 + 0.08 * k, 0.8 + 0.08 * k)
            y = 320 + r * 98
            hot = prog(t, 2.6, 3.1) if name == "Full House" else 0
            d.rounded_rectangle([x0, y - 40, x0 + 770, y + 40], 18, fill=(0, 0, 0, int(80 * a)))
            if hot:
                d.rounded_rectangle([x0, y - 40, x0 + 770, y + 40], 18, outline=GOLD + (int(255 * hot),), width=4)
            text(d, (x0 + 30, y), name, font(40, "demi"), CREAM, a, anchor="lm")
            text(d, (x0 + 560, y), str(pts), font(44, "bold"), GOLD if hot else CREAM, a, anchor="rm")
            text(d, (x0 + 740, y), f"${money}", font(40, "demi"), GOLD, a, anchor="rm")
    caption(img, d, t, 0.4, 4.3, "A play scores as the best poker hand its cards make.")
    caption(img, d, t, 4.6, 8.4, "* These need duplicate cards, which only a random deck has.")


def s_shop(img, d, t):
    header(d, "THE SHOP", "Buying doesn't use up your turn")
    bought = t >= 4.4
    hud(d, 1, 60, 2 if bought else 6, 7, 2, hot=("MONEY",) if 4.4 <= t < 5.8 else ())
    tiles = [
        ("Level up a hand", "That hand pays +50% of its base points per level", "$4 × level"),
        ("Bigger hand", "See one more card at once, up to 10", "$12"),
        ("Extra discard", "One more discard for this game", "$6"),
        ("Extra play", "One more play for this game", "$15"),
    ]
    notes = ["$4, $8, $12... per hand type", "then $24, $36...", "then $12, $18...", "then $30, $45..."]
    for i, (title, desc, price) in enumerate(tiles):
        a = prog(t, 0.3 + 0.15 * i, 0.9 + 0.15 * i)
        x0, y0 = 120 + i * 430, 250
        x1, y1 = x0 + 390, y0 + 420
        lift = 0
        if i == 0:
            lift = prog(t, 3.8, 4.3)
            if lift:
                g, gp = glow(390, 420, 26, GOLD)
                paste(img, g, x0 - gp, y0 - gp, lift * 0.9)
        d.rounded_rectangle([x0, y0, x1, y1], 26, fill=(10, 34, 26, int(235 * a)),
                            outline=(GOLD if lift else (255, 255, 255)) + (int((255 if lift else 40) * a),), width=3)
        text(d, (x0 + 30, y0 + 60), title, font(40, "bold"), CREAM, a, anchor="lm")
        f = font(30, "regular")
        for k, line in enumerate(wrap(desc, f, 330, d)):
            text(d, (x0 + 30, y0 + 130 + k * 42), line, f, MUTED, a, anchor="lm")
        text(d, (x0 + 30, y0 + 320), price, font(54, "bold"), GOLD, a, anchor="lm")
        text(d, (x0 + 30, y0 + 375), notes[i], font(24, "medium"), MUTED, a, anchor="lm")
    pop_text(img, "BOUGHT  level:Pair", 315, 220, 34, clamp((t - 4.4) / 0.4), weight="heavy", color=GOLD)
    a = prog(t, 4.9, 5.5)
    # Avenir has no arrow glyph, so draw it with the symbols font between the two halves
    left, right, f = "Pair:  10", "15 points", font(56, "bold")
    lw, rw, gap = d.textlength(left, font=f), d.textlength(right, font=f), 110
    x0 = 960 - (lw + gap + rw) / 2
    text(d, (x0, 800), left, f, CREAM, a, anchor="lm")
    text(d, (x0 + lw + gap / 2, 800), "→", symbols(56), GOLD, a)
    text(d, (x0 + lw + gap, 800), right, f, CREAM, a, anchor="lm")
    caption(img, d, t, 0.3, 3.6, "Every play earns money. Spend it in the shop.")
    caption(img, d, t, 3.9, 9.3, "The bot pays $4 to level up Pair: it now scores 15 instead of 10.")


def s_rest(img, d, t):
    header(d, "THE REST OF THE GAME", "Same loop, until the plays run out")
    shown = 3 + sum(1 for i in range(len(LOG) - 3) if t >= 0.6 + 0.6 * i)
    for i, (label, cards, result, *_rest) in enumerate(LOG[:shown]):
        y = 205 + i * 53
        appear = 1.0 if i < 3 else prog(t, 0.6 + 0.6 * (i - 3), 0.9 + 0.6 * (i - 3))
        a = (0.5 if i < 3 else 1.0) * appear
        dx = (1 - appear) * -40
        is_buy = label.startswith("Buy")
        is_discard = label == "Discard"
        color = MUTED if is_discard else (GOLD if is_buy else CREAM)
        text(d, (100 + dx, y), f"{i + 1:>2}", mono(24), MUTED, a, anchor="lm")
        text(d, (160 + dx, y), label, font(32, "demi"), color, a, anchor="lm")
        for k, code in enumerate(cards):
            draw_card(img, code, 560 + dx + k * 42, y, w=36, alpha=a)
        text(d, (1150 + dx, y), result, font(32, "bold"), GOLD if result.startswith("+") else MUTED, a, anchor="rm")
    # running totals: count up toward the latest row's numbers
    cur, prev = LOG[shown - 1], LOG[max(0, shown - 2)]
    last_t = 0.6 + 0.6 * (shown - 4) if shown > 3 else 0
    f = prog(t, last_t, last_t + 0.45) if shown > 3 else 1
    score = int(round(lerp(prev[3], cur[3], f)))
    d.rounded_rectangle([1260, 190, 1830, 900], 30, fill=(0, 0, 0, 80))
    text(d, (1545, 260), "SCORE", font(30, "demi"), MUTED)
    text(d, (1545, 390), str(score), font(190, "heavy"), CREAM)
    for k, (name, idx, fmt) in enumerate((("MONEY", 4, "${}"), ("PLAYS LEFT", 5, "{}"), ("DISCARDS", 6, "{}"))):
        y = 560 + k * 85
        text(d, (1320, y), name, font(28, "demi"), MUTED, anchor="lm")
        text(d, (1770, y), fmt.format(cur[idx]), font(44, "bold"), CREAM, anchor="rm")
    end = prog(t, 7.4, 7.9)
    if end:
        d.rounded_rectangle([1290, 790, 1800, 870], 22, fill=GOLD + (int(255 * end),))
        text(d, (1545, 830), "OUT OF PLAYS", font(36, "heavy"), (20, 30, 24), end)
    caption(img, d, t, 0.3, 7.0, "The bot keeps playing, discarding and buying until it runs out of plays.")
    caption(img, d, t, 7.3, 10.7, "Final score: 249. A bot's result is its average over many seeds.")


CODE = [
    "# bots/my_bot.py",
    "from poker_hands import best_play",
    "",
    "def play(state):",
    '    """Called once per turn."""',
    "    cards, hand_type, points = best_play(",
    '        state["hand"], state["points"])',
    '    return {"action": "play", "cards": cards}',
]
KEYWORDS = {"from", "import", "def", "return"}


def code_colors(line):
    """Per-character colours for one line of the bot snippet."""
    colors = [CREAM] * len(line)
    if line.lstrip().startswith("#"):
        return [MUTED] * len(line)
    i = 0
    while i < len(line):
        if line[i] == '"':
            j = line.find('"', i + 1)
            if line.startswith('"""', i):
                j = line.find('"""', i + 3) + 2
            j = len(line) - 1 if j < 0 else j
            for k in range(i, j + 1):
                colors[k] = GOLD
            i = j + 1
            continue
        if line[i].isalpha() and (i == 0 or not line[i - 1].isalnum()):
            j = i
            while j < len(line) and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            if word in KEYWORDS:
                for k in range(i, j):
                    colors[k] = (232, 120, 150)
            elif word in ("play", "best_play") and line[j:j + 1] == "(":
                for k in range(i, j):
                    colors[k] = (130, 190, 255)
            i = j
            continue
        i += 1
    return colors


def s_bot(img, d, t):
    header(d, "WRITE A BOT", "One Python file in bots/")
    d.rounded_rectangle([90, 200, 1070, 800], 26, fill=(6, 18, 14, 230))
    for k, c in enumerate(((236, 95, 86), (240, 190, 70), (90, 200, 110))):
        d.ellipse([125 + k * 34, 228, 145 + k * 34, 248], fill=c)
    f = mono(29)
    cw = f.getlength("M")
    total = sum(len(l) for l in CODE)
    typed = int(total * prog(t, 0.4, 4.4, lambda x: clamp(x)))
    n = 0
    for row, line in enumerate(CODE):
        colors = code_colors(line)
        y = 300 + row * 52
        for col, ch in enumerate(line):
            if n >= typed:
                break
            d.text((130 + col * cw, y), ch, font=f, fill=colors[col], anchor="lm")
            n += 1
        else:
            continue
        if int(t * 2.5) % 2 == 0:
            d.rectangle([130 + col * cw, y - 18, 130 + col * cw + 3, y + 18], fill=CREAM)
        break
    a = prog(t, 4.6, 5.2)
    text(d, (1130, 245), "Return one action:", font(34, "demi"), MUTED, a, anchor="lm")
    actions = [
        ("play", '{"action": "play", "cards": [0, 3, 4]}', GOLD),
        ("discard", '{"action": "discard", "cards": [1, 2]}', RED),
        ("buy", '{"action": "buy", "item": "hand_size"}', (130, 190, 255)),
    ]
    for k, (name, snippet, color) in enumerate(actions):
        ak = prog(t, 4.9 + 0.25 * k, 5.5 + 0.25 * k)
        y = 340 + k * 120
        d.rounded_rectangle([1120, y - 46, 1830, y + 46], 22, fill=(0, 0, 0, int(90 * ak)))
        d.rounded_rectangle([1120, y - 46, 1132, y + 46], 6, fill=color + (int(255 * ak),))
        text(d, (1160, y), snippet, mono(25), CREAM, ak, anchor="lm")
    a = prog(t, 6.0, 6.6)
    f2 = font(28, "medium")
    text(d, (1130, 720), "state includes: hand, points, money, shop,", f2, MUTED, a, anchor="lm")
    text(d, (1130, 762), "plays_left, discards_left, unseen, history...", f2, MUTED, a, anchor="lm")
    caption(img, d, t, 0.3, 5.0, "A bot is a function: it gets the game state and returns an action.")
    caption(img, d, t, 5.3, 10.2, "Or give it STRATEGIES = [s1, s2, s3]. Its score is their average.")


def s_tournament(img, d, t):
    header(d, "TOURNAMENTS", "python -m poker_hands run bots/")
    tiles = [
        ("Solo", "Every bot plays its own copy of the same seeded deck, so they all see the same cards."),
        ("Shared", "Bots take turns drawing from one deck and can see each other's moves. Win rate counts too."),
    ]
    for i, (title, desc) in enumerate(tiles):
        a = prog(t, 0.3 + 0.2 * i, 0.9 + 0.2 * i)
        x0 = 100 + i * 880
        d.rounded_rectangle([x0, 200, x0 + 840, 450], 26, fill=(10, 34, 26, int(235 * a)),
                            outline=(255, 255, 255, int(40 * a)), width=3)
        text(d, (x0 + 36, 255), title, font(44, "bold"), GOLD, a, anchor="lm")
        f = font(31, "regular")
        for k, line in enumerate(wrap(desc, f, 760, d)):
            text(d, (x0 + 36, 325 + k * 44), line, f, CREAM, a, anchor="lm")
    a = prog(t, 1.8, 2.3)
    text(d, (100, 530), "LEADERBOARD  ·  200 seeds, solo mode, standard deck", font(26, "demi"), MUTED, a, anchor="lm")
    rows = [("greedy_bot", 149.6, "greedy 106  ·  fish 153  ·  shopper 189"), ("random_bot", 51.5, "")]
    for k, (name, score, sub) in enumerate(rows):
        y = 625 + k * 140
        g = prog(t, 2.4 + 0.3 * k, 3.6 + 0.3 * k)
        text(d, (100, y), f"{k + 1}  {name}", mono(34, bold=True), CREAM, a, anchor="lm")
        x0, wmax = 520, 1050
        d.rounded_rectangle([x0, y - 30, x0 + wmax, y + 30], 14, fill=(0, 0, 0, int(70 * a)))
        bw = wmax * score / 160 * g
        if bw > 4:
            d.rounded_rectangle([x0, y - 30, x0 + bw, y + 30], 14, fill=GOLD if k == 0 else MUTED)
        text(d, (x0 + bw + 24, y), f"{score * g:.1f}", font(40, "bold"), CREAM, a * clamp(g * 4), anchor="lm")
        if sub:
            text(d, (x0, y + 58), sub, font(26, "medium"), MUTED, prog(t, 3.8, 4.3), anchor="lm")
    caption(img, d, t, 0.3, 4.4, "Solo or shared, every bot plays hundreds of seeded games.")
    caption(img, d, t, 4.7, 9.3, "Ranked by average score. The example bot beats random play 150 to 52.")


def s_end(img, d, t):
    a = prog(t, 0.2, 0.9)
    text(d, (960, 250), "POKER HANDS", font(120, "heavy"), CREAM, a)
    text(d, (960, 350), "Write a bot. Top the leaderboard.", font(46, "medium"), GOLD, prog(t, 0.6, 1.2))
    d.rounded_rectangle([420, 450, 1500, 790], 26, fill=(6, 18, 14, int(230 * a)))
    cmds = [
        ("python -m poker_hands play", "play it yourself"),
        ("python -m poker_hands run bots/", "run a tournament"),
        ("python -m poker_hands run bots/ --mode shared", "share one deck"),
    ]
    for k, (cmd, note) in enumerate(cmds):
        ak = prog(t, 1.0 + 0.3 * k, 1.5 + 0.3 * k)
        y = 530 + k * 92
        text(d, (470, y), "$", mono(32), MUTED, ak, anchor="lm")
        text(d, (510, y), cmd, mono(32), CREAM, ak, anchor="lm")
        text(d, (510, y + 36), note, font(22, "medium"), MUTED, ak, anchor="lm")
    text(d, (960, 860), "Rules and bot API: README.md  ·  Example bot: bots/greedy_bot.py",
         font(28, "medium"), MUTED, prog(t, 2.2, 2.8))


# (scene, seconds, fade in, fade out)
SCENES = [
    (s_title, 5.0, True, True),
    (s_setup, 8.2, True, False),
    (s_discard, 8.8, False, False),
    (s_play, 9.2, False, True),
    (s_points, 8.6, True, True),
    (s_shop, 9.6, True, True),
    (s_rest, 11.0, True, True),
    (s_bot, 10.5, True, True),
    (s_tournament, 9.6, True, True),
    (s_end, 6.5, True, True),
]

BLACK = Image.new("RGB", (W, H), (0, 0, 0))


def render_frame(scene, dur, fade_in, fade_out, t, first, last):
    img = BG.copy()
    d = ImageDraw.Draw(img, "RGBA")
    scene(img, d, t)
    a = 1.0
    if fade_in:
        a = min(a, t / 0.4)
    if fade_out:
        a = min(a, (dur - t) / 0.4)
    if a < 1:
        base = BLACK if (first and t < dur / 2) or (last and t > dur / 2) else BG
        img = Image.blend(base, img, clamp(a))
    return img


def frames():
    for n, (scene, dur, fi, fo) in enumerate(SCENES):
        for f in range(int(round(dur * FPS))):
            yield n, f / FPS, render_frame(scene, dur, fi, fo, f / FPS, n == 0, n == len(SCENES) - 1)


def stills(out):
    out.mkdir(parents=True, exist_ok=True)
    for n, (scene, dur, fi, fo) in enumerate(SCENES):
        for frac in (0.3, 0.55, 0.85):
            t = dur * frac
            render_frame(scene, dur, fi, fo, t, False, False).save(out / f"{n:02d}_{scene.__name__}_{t:04.1f}.png")


def video(out):
    import tempfile

    import imageio_ffmpeg

    import music

    total = sum(int(round(d * FPS)) for _, d, _, _ in SCENES)
    track = Path(tempfile.mkdtemp()) / "music.wav"
    music.write_wav(track, music.render(total / FPS))
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(track),
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest",
           "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i, (n, t, img) in enumerate(frames()):
        proc.stdin.write(img.tobytes())
        if i % 150 == 0:
            print(f"frame {i}/{total}", flush=True)
    proc.stdin.close()
    if proc.wait():
        sys.exit("ffmpeg failed")
    print(f"wrote {out}")
    # VS Code's player can't decode AAC audio, so also write a VP9 + Opus copy that it can play
    webm = out.with_suffix(".webm")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(out),
                    "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-row-mt", "1", "-cpu-used", "4",
                    "-c:a", "libopus", "-b:a", "160k", str(webm)], check=True)
    print(f"wrote {webm}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["stills"]:
        stills(Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "stills")
    else:
        video(HERE / "poker_hands_explainer.mp4")

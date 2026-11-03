#!/usr/bin/env python3
"""Assemble the Poker Run contest site into a deployable static directory.

    python3 platform/site/build.py              # -> platform/site/dist/
    python3 platform/site/build.py --out /srv/site

Stdlib only. Re-runnable and deterministic: the output directory is rebuilt from
scratch, files are written in a fixed order, the zip has fixed timestamps, and
asset URLs carry a content hash (?v=...) so browsers never see stale files.

Layout of the output:
    /                     landing page            (pages/index.html)
    /problem/             statement + generator   (rendered from poker_run/problem/*.md)
    /contest/             contest hub             (pages/contest/index.html)
    /contest/leaderboard/ leaderboard             (pages/contest/leaderboard/index.html)
    /play/                the browser game        (copied from poker_run/web/)
    /assets/              css, js, images         (assets/)
    /media/               explainer video         (copied from video/)
    /downloads/           starter.py, statement.md, poker_run_public.zip
"""

import argparse
import hashlib
import html
import io
import os
import re
import shutil
import struct
import sys
import zipfile

SITE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(SITE))
sys.path.insert(0, ROOT)

PAGES = os.path.join(SITE, "pages")
ASSETS = os.path.join(SITE, "assets")
WEB = os.path.join(ROOT, "poker_run", "web")
PROBLEM = os.path.join(ROOT, "poker_run", "problem")
STARTER = os.path.join(ROOT, "poker_run", "starters", "starter.py")
VIDEO = os.path.join(ROOT, "video")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "poker_run", "public")
PUBLIC_SEEDS = range(1, 11)
ZIP_NAME = "poker_run_public.zip"
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


# ---------------------------------------------------------------------------- markdown

def slugify(text, used):
    s = re.sub(r"<[^>]+>", "", text)
    s = html.unescape(s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "section"
    base, n = s, 2
    while s in used:
        s = "%s-%d" % (base, n)
        n += 1
    used.add(s)
    return s


def inline(text, link_map):
    """Inline markdown: `code`, [links](url), **bold**, *italic*, _italic_. Input is raw text."""
    stash = []

    def keep(fragment):
        stash.append(fragment)
        return "\x00%d\x00" % (len(stash) - 1)

    text = re.sub(r"`([^`]+)`", lambda m: keep("<code>%s</code>" % html.escape(m.group(1), quote=False)), text)

    def link(m):
        label, href = m.group(1), m.group(2).strip()
        href = link_map(href)
        inner = inline(label, link_map)
        if href is None:
            return keep(inner)
        ext = href.startswith("http://") or href.startswith("https://")
        attrs = ' rel="noopener" target="_blank"' if ext else ""
        return keep('<a href="%s"%s>%s</a>' % (html.escape(href), attrs, inner))

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w])_(?=\S)(.+?)(?<=\S)_(?![\w])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)


def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells, cur, i = [], "", 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if line[i] == "|":
            cells.append(cur.strip())
            cur = ""
        else:
            cur += line[i]
        i += 1
    cells.append(cur.strip())
    return cells


SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")


def render_markdown(md, used_ids, link_map, shift=0, drop_h1=False, toc=None):
    """Small Markdown renderer: headings, paragraphs, emphasis, code spans,
    fenced code, tables (with alignment), ordered/unordered lists, links, rules.
    `shift` demotes headings; `drop_h1` skips the first level-1 heading;
    `toc` collects (level, id, text) for level 2-3 headings after shifting."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, n, dropped = [], 0, len(lines), False
    para = []

    def flush_para():
        if para:
            out.append("<p>%s</p>" % inline(" ".join(s.strip() for s in para), link_map))
            del para[:]

    while i < n:
        line = lines[i]
        stripped = line.strip()
        fence = re.match(r"^\s*(```+|~~~+)\s*([\w+-]*)\s*$", line)
        if fence:
            flush_para()
            mark, lang = fence.group(1), fence.group(2)
            i += 1
            body = []
            while i < n and not lines[i].strip().startswith(mark[:3]):
                body.append(lines[i])
                i += 1
            i += 1
            cls = ' class="language-%s"' % html.escape(lang) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, html.escape("\n".join(body), quote=False)))
            continue
        if not stripped:
            flush_para()
            i += 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if h:
            flush_para()
            level = len(h.group(1))
            if level == 1 and drop_h1 and not dropped:
                dropped = True
                i += 1
                continue
            level = min(6, level + shift)
            content = inline(h.group(2), link_map)
            hid = slugify(content, used_ids)
            if toc is not None and level in (2, 3):
                toc.append((level, hid, re.sub(r"<[^>]+>", "", content)))
            anchor = ' <a class="anchor" href="#%s" aria-label="Link to this section">#</a>' % hid if level >= 2 else ""
            out.append('<h%d id="%s">%s%s</h%d>' % (level, hid, content, anchor, level))
            i += 1
            continue
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            flush_para()
            out.append("<hr>")
            i += 1
            continue
        if "|" in line and i + 1 < n and SEP_RE.match(lines[i + 1]):
            flush_para()
            head = split_row(line)
            aligns = []
            for c in split_row(lines[i + 1]):
                c = c.strip()
                aligns.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left" if c.startswith(":") else None)
            i += 2
            body = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                body.append(split_row(lines[i]))
                i += 1

            def cell(tag, text, k):
                a = aligns[k] if k < len(aligns) else None
                style = ' style="text-align:%s"' % a if a else ""
                return "<%s%s>%s</%s>" % (tag, style, inline(text, link_map), tag)

            t = ["<div class=\"table-wrap\"><table>", "<thead><tr>"]
            t += [cell("th", c, k) for k, c in enumerate(head)]
            t.append("</tr></thead><tbody>")
            for row in body:
                row = (row + [""] * len(head))[:len(head)]
                t.append("<tr>" + "".join(cell("td", c, k) for k, c in enumerate(row)) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue
        m = LIST_RE.match(line)
        if m:
            flush_para()
            ordered = m.group(2)[0].isdigit()
            base_indent = len(m.group(1))
            items = []
            while i < n:
                m2 = LIST_RE.match(lines[i])
                if m2 and len(m2.group(1)) == base_indent and m2.group(2)[0].isdigit() == ordered:
                    items.append([m2.group(3)])
                    i += 1
                elif items and lines[i].strip() and (lines[i].startswith(" " * (base_indent + 2)) or not LIST_RE.match(lines[i])) \
                        and not re.match(r"^\s*(#|```|\|)", lines[i]):
                    items[-1].append(lines[i])
                    i += 1
                else:
                    break
            tag = "ol" if ordered else "ul"
            start = ""
            if ordered:
                first = int(re.match(r"\d+", m.group(2)).group(0))
                if first != 1:
                    start = ' start="%d"' % first
            lis = []
            for it in items:
                sub = [x for x in it[1:] if LIST_RE.match(x)]
                text = [it[0]] + [x for x in it[1:] if not LIST_RE.match(x)]
                inner = inline(" ".join(s.strip() for s in text), link_map)
                if sub:
                    inner += render_markdown("\n".join(s.strip() for s in sub), used_ids, link_map)
                lis.append("<li>%s</li>" % inner)
            out.append("<%s%s>%s</%s>" % (tag, start, "".join(lis), tag))
            continue
        para.append(line)
        i += 1
    flush_para()
    return "\n".join(out)


def link_map(href):
    """Rewrite links from the repository's markdown to what the site publishes.
    Returns None for links to files the site doesn't serve (rendered as text)."""
    if href.startswith(("http://", "https://", "mailto:", "#")):
        return href
    name = href.split("/")[-1]
    known = {
        "engine.js": "/play/engine.js",
        "statement.md": "/problem/",
        "generator.md": "/problem/#generator",
        "starter.py": "/downloads/starter.py",
    }
    return known.get(name)


# ---------------------------------------------------------------------------- helpers

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


def short_hash(data):
    return hashlib.sha256(data).hexdigest()[:10]


def human_size(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024) if n >= 10 * 1024 else "%.1f KB" % (n / 1024)
    return "%.1f MB" % (n / 1024 / 1024)


NAV_ITEMS = [("play", "/play/", "Play"), ("problem", "/problem/", "Problem"),
             ("contest", "/contest/", "Contest"), ("leaderboard", "/contest/leaderboard/", "Leaderboard")]

HEAD = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0b2a20">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/assets/site.css">
<script defer src="/assets/sound.js"></script>
<script defer src="/assets/api.js"></script>
<script defer src="/assets/site.js"></script>
<script defer src="/assets/motion.js"></script>"""

FOOTER = """<footer class="site-footer">
  <div class="wrap">
    <div><span class="brand" style="font-size:15px">POKER <b>RUN</b></span><br><span>An 8-round poker puzzle where the input reveals everything.</span></div>
    <nav aria-label="Footer">
      <a href="/play/">Play</a><a href="/problem/">Problem</a><a href="/contest/">Contest</a><a href="/contest/leaderboard/">Leaderboard</a><a href="/downloads/starter.py" download>starter.py</a><a href="/?intro=1" data-replay-intro>Replay intro</a>
    </nav>
  </div>
</footer>"""


def nav(current):
    links = "".join('<li><a href="%s"%s>%s</a></li>' % (href, ' aria-current="page"' if key == current else "", label)
                    for key, href, label in NAV_ITEMS)
    return """<a class="skip" href="#main">Skip to content</a>
<header class="site-nav">
  <div class="wrap">
    <a class="brand" href="/" aria-label="Poker Run home"><span class="pip" aria-hidden="true">♠︎</span><span>POKER <b>RUN</b></span></a>
    <ul class="nav-links">%s</ul>
    <div class="nav-user" id="nav-user"><button type="button" class="sound-toggle" data-sound-toggle aria-pressed="true" aria-label="Sound"></button></div>
  </div>
</header>""" % links


def fill_partials(text, asset_hashes):
    text = text.replace("<!--#head-->", HEAD).replace("<!--#footer-->", FOOTER)
    text = re.sub(r"<!--#nav:(\w+)-->", lambda m: nav(m.group(1)), text)

    def bust(m):
        attr, path = m.group(1), m.group(2)
        h = asset_hashes.get(path)
        return '%s="/assets/%s%s"' % (attr, path, "?v=" + h if h else "")
    return re.sub(r'(src|href)="/assets/([^"?#]+)"', bust, text)


# ---------------------------------------------------------------------------- build steps

def build_downloads(out):
    from poker_run import generate, format_instance

    dl = os.path.join(out, "downloads")
    copy(STARTER, os.path.join(dl, "starter.py"))
    copy(os.path.join(PROBLEM, "statement.md"), os.path.join(dl, "statement.md"))

    entries = []
    readme = (
        "Poker Run public tests\n"
        "======================\n\n"
        "inputs/NNNN.txt     the input for public seed NNNN (python -m poker_run gen --seed N writes the same file)\n"
        "baseline/           the reference greedy baseline's output for each seed, and the checker's verdict on it\n\n"
        "Run your solver:    python3 main.py < inputs/0001.txt > out.txt\n"
        "Check the output:   python -m poker_run check inputs/0001.txt out.txt   (from the repository)\n"
        "Or replay it in the browser: open /play/, Replay tab, paste the input and your output.\n"
    )
    entries.append(("poker_run_public/README.txt", readme.encode()))
    for seed in PUBLIC_SEEDS:
        entries.append(("poker_run_public/inputs/%04d.txt" % seed, format_instance(generate(seed)).encode()))
    if os.path.isdir(FIXTURES):
        for name in sorted(os.listdir(FIXTURES)):
            m = re.match(r"^(\d{4})\.baseline\.(out\.txt|expected\.json)$", name)
            if m:
                with open(os.path.join(FIXTURES, name), "rb") as f:
                    entries.append(("poker_run_public/baseline/%s.%s" % (m.group(1), m.group(2)), f.read()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            z.writestr(info, data, compresslevel=9)
    with open(os.path.join(dl, ZIP_NAME), "wb") as f:
        f.write(buf.getvalue())
    return {name: os.path.getsize(os.path.join(dl, name)) for name in sorted(os.listdir(dl))}


def build_problem(out, asset_hashes, sizes):
    used = set()
    toc = []
    statement = render_markdown(read(os.path.join(PROBLEM, "statement.md")), used, link_map, drop_h1=True, toc=toc)
    gen_path = os.path.join(PROBLEM, "generator.md")
    generator = ""
    if os.path.exists(gen_path):
        generator = render_markdown(read(gen_path), used, link_map, shift=1, toc=toc)
    items = []
    for level, hid, text in toc:
        items.append('<li class="%s"><a href="#%s">%s</a></li>' % ("sub" if level == 3 else "", hid, text))
    toc_html = "<ol>%s</ol>" % "".join(items)
    page = read(os.path.join(PAGES, "problem", "index.html"))
    m = re.search(r"time limit is \*\*(.+?)\*\*", read(os.path.join(PROBLEM, "statement.md")))
    page = page.replace("<!--#time_limit-->", m.group(1) if m else "2 seconds")
    page = page.replace("<!--#toc-->", toc_html).replace("<!--#statement-->", statement).replace("<!--#generator-->", generator)
    page = re.sub(r"<!--#size:([\w.]+)-->", lambda mm: human_size(sizes.get(mm.group(1), 0)), page)
    if not generator:
        page = re.sub(r'\s*<article class="prose doc-part" id="generator-doc">\s*</article>', "", page)
    write(os.path.join(out, "problem", "index.html"), fill_partials(page, asset_hashes))


def build_play(out):
    dst = os.path.join(out, "play")
    page = read(os.path.join(WEB, "index.html"))
    # The scripts the page loads (engine.js, bot.js, multiplayer.js), not the test drivers.
    scripts = re.findall(r'<script src="([\w.-]+\.js)"></script>', page)
    if "engine.js" not in scripts:
        raise SystemExit("poker_run/web/index.html no longer loads engine.js")
    for name in scripts:
        copy(os.path.join(WEB, name), os.path.join(dst, name))
    # Make the game's title a link back to the site, if the markup is the one we know.
    title = "<h1>POKER <span>RUN</span></h1>"
    if title in page:
        page = page.replace(title, '<h1><a href="/" title="Poker Run home" style="color:inherit;text-decoration:none">POKER <span>RUN</span></a></h1>', 1)
    write(os.path.join(dst, "index.html"), page)


def build_media(out):
    have = []
    for ext in ("mp4", "webm"):
        src = os.path.join(VIDEO, "poker_run_explainer." + ext)
        if os.path.exists(src):
            copy(src, os.path.join(out, "media", "poker_run_explainer." + ext))
            have.append(ext)
    return have


def build(out):
    if os.path.abspath(out) in (os.path.abspath(SITE), os.path.abspath(ROOT), "/"):
        raise SystemExit("refusing to build into %s" % out)
    tmp = out.rstrip("/") + ".tmp"
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)

    # assets (hash them first so pages can reference ?v=hash)
    asset_hashes = {}
    for dirpath, _, files in sorted(os.walk(ASSETS)):
        for name in sorted(files):
            if name.startswith("."):
                continue
            src = os.path.join(dirpath, name)
            rel = os.path.relpath(src, ASSETS).replace(os.sep, "/")
            with open(src, "rb") as f:
                asset_hashes[rel] = short_hash(f.read())
            copy(src, os.path.join(tmp, "assets", rel))

    sizes = build_downloads(tmp)
    media = build_media(tmp)
    build_play(tmp)
    build_problem(tmp, asset_hashes, sizes)

    for dirpath, _, files in sorted(os.walk(PAGES)):
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(dirpath, name), PAGES).replace(os.sep, "/")
            if rel == "problem/index.html":
                continue
            src = os.path.join(dirpath, name)
            if name.endswith(".html"):
                page = fill_partials(read(src), asset_hashes)
                if "webm" not in media:
                    page = re.sub(r'\s*<source src="/media/poker_run_explainer\.webm"[^>]*>', "", page)
                write(os.path.join(tmp, rel), page)
            else:
                copy(src, os.path.join(tmp, rel))

    # /favicon.ico for pages without an icon link (the game): an ICO wrapping the PNG
    png_path = os.path.join(ASSETS, "favicon.png")
    if os.path.exists(png_path):
        with open(png_path, "rb") as f:
            png = f.read()
        ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 64, 64, 0, 0, 1, 32, len(png), 22) + png
        with open(os.path.join(tmp, "favicon.ico"), "wb") as f:
            f.write(ico)

    if os.path.exists(out):
        shutil.rmtree(out)
    os.replace(tmp, out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(SITE, "dist"), help="output directory (default: platform/site/dist)")
    args = ap.parse_args(argv)
    out = build(os.path.abspath(args.out))
    count = sum(len(f) for _, _, f in os.walk(out))
    print("built %d files into %s" % (count, out))


if __name__ == "__main__":
    main()

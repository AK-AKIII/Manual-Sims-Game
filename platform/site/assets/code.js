/* Simple Python syntax highlighting, a read-only code view with line numbers,
 * and a lightweight editor (a transparent textarea over a highlighted <pre>). */
(function () {
  "use strict";
  const KW = new Set(("False None True and as assert async await break class continue def del elif else except finally " +
    "for from global if import in is lambda nonlocal not or pass raise return try while with yield match case").split(" "));
  const BI = new Set(("print len range int str float list dict set tuple min max sum sorted enumerate zip map filter abs " +
    "any all open input isinstance super object Exception ValueError IndexError KeyError TypeError RuntimeError " +
    "reversed round divmod bool bytes iter next hash id type repr format ord chr pow").split(" "));
  // order matters: comments, strings (triple first), numbers, identifiers, decorators, anything else
  const RE = new RegExp([
    "(#[^\\n]*)",
    "((?:[rRbBuUfF]{1,2})?(?:'''[\\s\\S]*?(?:'''|$)|\"\"\"[\\s\\S]*?(?:\"\"\"|$)|'(?:\\\\.|[^'\\\\\\n])*'?|\"(?:\\\\.|[^\"\\\\\\n])*\"?))",
    "(\\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\\d[\\d_]*(?:\\.\\d[\\d_]*)?(?:[eE][+-]?\\d+)?j?|\\.\\d[\\d_]*(?:[eE][+-]?\\d+)?)\\b)",
    "(@[A-Za-z_][\\w.]*)",
    "([A-Za-z_][A-Za-z0-9_]*)",
  ].join("|"), "g");

  function esc(s) { return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  /** Tokens: [{cls, text}], cls '' for plain text. */
  function tokenize(src) {
    const out = [];
    let last = 0, m, prevWord = "";
    RE.lastIndex = 0;
    while ((m = RE.exec(src))) {
      if (m.index > last) out.push({ cls: "", text: src.slice(last, m.index) });
      let cls = "";
      if (m[1]) cls = "com";
      else if (m[2]) cls = "str";
      else if (m[3]) cls = "num";
      else if (m[4]) cls = "dec";
      else if (m[5]) {
        const w = m[5];
        if (prevWord === "def" || prevWord === "class") cls = "def";
        else if (KW.has(w)) cls = "kw";
        else if (w === "self" || w === "cls") cls = "self";
        else if (BI.has(w)) cls = "bi";
        prevWord = w;
      }
      if (!m[5]) prevWord = "";
      out.push({ cls, text: m[0] });
      last = RE.lastIndex;
      if (m[0].length === 0) RE.lastIndex++;
    }
    if (last < src.length) out.push({ cls: "", text: src.slice(last) });
    return out;
  }

  /** Highlighted HTML split into lines (tokens spanning lines are split). */
  function highlightLines(src) {
    const lines = [[]];
    for (const t of tokenize(src)) {
      const parts = t.text.split("\n");
      parts.forEach((p, i) => {
        if (i > 0) lines.push([]);
        if (p) lines[lines.length - 1].push(t.cls ? `<span class="tok-${t.cls}">${esc(p)}</span>` : esc(p));
      });
    }
    return lines.map((l) => l.join(""));
  }

  function highlight(src) { return highlightLines(src).join("\n"); }

  /** Read-only code view with line numbers. */
  function view(src, opts) {
    opts = opts || {};
    const lines = highlightLines(String(src || "").replace(/\r\n?/g, "\n"));
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    const body = lines.map((l) => `<span class="ln"><span class="c">${l || " "}</span></span>`).join("");
    return `<div class="codeview" tabindex="0" role="region" aria-label="${opts.label || "Source code"}"><div class="lines">${body}</div></div>`;
  }

  /** Turns <div class="editor" data-editor> containing a textarea into a highlighted editor. */
  function editor(root) {
    const ta = root.querySelector("textarea");
    const area = document.createElement("div");
    area.className = "area";
    const gutter = document.createElement("div");
    gutter.className = "gutter";
    gutter.setAttribute("aria-hidden", "true");
    const gpre = document.createElement("pre");
    gutter.appendChild(gpre);
    const hl = document.createElement("pre");
    hl.className = "hl";
    hl.setAttribute("aria-hidden", "true");
    ta.parentNode.insertBefore(gutter, ta);
    ta.parentNode.insertBefore(area, ta);
    area.appendChild(hl);
    area.appendChild(ta);
    let lineCount = 0, raf = 0;
    const sync = () => {
      hl.style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
      gpre.style.transform = `translateY(${-ta.scrollTop}px)`;
    };
    const render = () => {
      raf = 0;
      const v = ta.value;
      hl.innerHTML = highlight(v) + "\n ";
      const n = (v.match(/\n/g) || []).length + 1;
      if (n !== lineCount) {
        lineCount = n;
        let s = "";
        for (let i = 1; i <= n; i++) s += i + "\n";
        gpre.textContent = s;
      }
      sync();
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(render); };
    ta.addEventListener("input", schedule);
    ta.addEventListener("scroll", sync, { passive: true });
    render();
    return { set(v) { ta.value = v; render(); ta.dispatchEvent(new Event("input", { bubbles: true })); }, get: () => ta.value, refresh: render };
  }

  window.Code = { tokenize, highlight, highlightLines, view, editor };
})();

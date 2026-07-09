// A small, safe Markdown renderer. The one rule the whole dashboard follows:
// text reaches the page through textContent only — never innerHTML — so a note's
// contents can never become live markup under the strict CSP. It covers the
// subset real notes use (headings, lists, quotes, fenced/inline code, bold,
// italic, [[wikilinks]]); anything else falls through as plain text.
//
// renderMarkdown(text, { resolve, onLink }) -> HTMLElement (.note-doc)
//   resolve(target) -> rel_path | null   maps a [[wikilink]] to a real note
//   onLink(rel_path)                      called when a resolved wikilink is clicked

import { el } from "./dom.js";

const WIKILINK = /\[\[([^\]]+)\]\]/g;

// Inline: split a run of text into nodes, honoring `code`, **bold**, *italic*,
// and [[wikilinks]]. Code spans win first so markers inside them stay literal.
function inline(parent, text, ctx) {
  // pull out `code` spans, emit the gaps through the emphasis/link pass
  let last = 0;
  const codeRe = /`([^`]+)`/g;
  let m;
  while ((m = codeRe.exec(text))) {
    emphasis(parent, text.slice(last, m.index), ctx);
    parent.appendChild(el("code", null, m[1]));
    last = m.index + m[0].length;
  }
  emphasis(parent, text.slice(last), ctx);
}

function emphasis(parent, text, ctx) {
  // wikilinks first (they can't nest emphasis), then **bold**/*italic* on the gaps
  let last = 0;
  let m;
  WIKILINK.lastIndex = 0;
  while ((m = WIKILINK.exec(text))) {
    boldItalic(parent, text.slice(last, m.index));
    wikilink(parent, m[1], ctx);
    last = m.index + m[0].length;
  }
  boldItalic(parent, text.slice(last));
}

function wikilink(parent, body, ctx) {
  const [targetRaw, aliasRaw] = body.split("|");
  const target = targetRaw.trim();
  const label = (aliasRaw || targetRaw).trim();
  const rel = ctx.resolve ? ctx.resolve(target) : null;
  if (rel) {
    const a = el("a", "wikilink", label);
    a.setAttribute("role", "link");
    a.setAttribute("tabindex", "0");
    const go = () => ctx.onLink && ctx.onLink(rel);
    a.addEventListener("click", go);
    a.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); }
    });
    parent.appendChild(a);
  } else {
    parent.appendChild(el("span", "wikilink dangling", label));
  }
}

function boldItalic(parent, text) {
  if (!text) return;
  const re = /\*\*([^*]+)\*\*|\*([^*]+)\*|_([^_]+)_/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (m[1] !== undefined) parent.appendChild(el("strong", null, m[1]));
    else parent.appendChild(el("em", null, m[2] !== undefined ? m[2] : m[3]));
    last = m.index + m[0].length;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
}

export function renderMarkdown(text, ctx = {}) {
  const doc = el("div", "note-doc");
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  let i = 0;

  // strip a leading YAML frontmatter block into a quiet metadata line
  if (lines[0] === "---") {
    let j = 1;
    while (j < lines.length && lines[j] !== "---") j++;
    if (j < lines.length) {
      const meta = el("div", "meta");
      meta.textContent = lines.slice(1, j).join(" · ");
      doc.appendChild(meta);
      i = j + 1;
    }
  }

  while (i < lines.length) {
    let line = lines[i];

    if (line.trim() === "") { i++; continue; }

    // fenced code
    const fence = line.match(/^```+/);
    if (fence) {
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) { buf.push(lines[i]); i++; }
      i++; // closing fence
      const pre = el("pre");
      pre.appendChild(el("code", null, buf.join("\n")));
      doc.appendChild(pre);
      continue;
    }

    // heading
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = Math.min(h[1].length, 3);
      const node = el("h" + level);
      inline(node, h[2], ctx);
      doc.appendChild(node);
      i++;
      continue;
    }

    // blockquote (consecutive > lines)
    if (line.startsWith(">")) {
      const bq = el("blockquote");
      while (i < lines.length && lines[i].startsWith(">")) {
        const p = el("p");
        inline(p, lines[i].replace(/^>\s?/, ""), ctx);
        bq.appendChild(p);
        i++;
      }
      doc.appendChild(bq);
      continue;
    }

    // list (unordered or ordered)
    const isUl = /^\s*[-*]\s+/.test(line);
    const isOl = /^\s*\d+\.\s+/.test(line);
    if (isUl || isOl) {
      const list = el(isUl ? "ul" : "ol");
      const re = isUl ? /^\s*[-*]\s+/ : /^\s*\d+\.\s+/;
      while (i < lines.length && (isUl ? /^\s*[-*]\s+/ : /^\s*\d+\.\s+/).test(lines[i])) {
        const li = el("li");
        inline(li, lines[i].replace(re, ""), ctx);
        list.appendChild(li);
        i++;
      }
      doc.appendChild(list);
      continue;
    }

    // paragraph (consecutive plain lines)
    const buf = [];
    while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,6}\s|>|```|\s*[-*]\s|\s*\d+\.\s)/.test(lines[i])) {
      buf.push(lines[i]);
      i++;
    }
    const p = el("p");
    inline(p, buf.join(" "), ctx);
    doc.appendChild(p);
  }
  return doc;
}

// knowme web — escaping, markdown, core globals (D/editing), postJSON, reveal.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

// Escapes quotes too, not just &<>. Text nodes never needed it, and for a long
// time nothing put model output inside an ATTRIBUTE — so the gap was invisible.
// The copy buttons (#58) are the first place that happens, and a reply
// containing one double quote was enough to close data-text="..." and attach
// its own event handler. The model's output is not fully ours: search_web and
// browse_web pull text off the open web, and this dashboard holds the memory,
// the traces and the settings. Escape at the helper, once, for every caller.
const esc = s => (s??"").toString().replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// --- tiny markdown renderer for chat replies (no dependency, XSS-safe: we
// escape first, then apply a small set of transforms the LLM actually uses:
// bold/italic/code, links, ordered/unordered lists, and tables).
function mdInline(s){   // s is already HTML-escaped
  return s
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|message:\/\/[^\s)]+)\)/g,
             (m, text, url) => `<a href="${url}" target="_blank" rel="noopener">${text}</a>`)
    .replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*_`])[*_]([^*_`\s][^*_`]*?)[*_](?![\w*])/g, "$1<em>$2</em>")
    .replace(/`([^`]+?)`/g, "<code>$1</code>");
}
function renderMarkdown(text){
  const lines = esc(text).split(/\r?\n/);
  const row = l => /^\s*\|.*\|\s*$/.test(l);
  const sep = l => /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(l);
  const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  const out = [];
  let i = 0;
  while (i < lines.length){
    const l = lines[i];
    if (row(l) && i+1 < lines.length && sep(lines[i+1])){          // table
      const head = cells(l); i += 2; const body = [];
      while (i < lines.length && row(lines[i])){ body.push(cells(lines[i])); i++; }
      out.push(`<table class="mdtable"><thead><tr>${head.map(h=>`<th>${mdInline(h)}</th>`).join("")}</tr></thead><tbody>${
        body.map(r=>`<tr>${r.map(c=>`<td>${mdInline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const h = l.match(/^\s*#{1,6}\s+(.*)$/);
    if (h){ out.push(`<div class="mdh">${mdInline(h[1])}</div>`); i++; continue; }
    if (/^\s*[-*]\s+/.test(l)){                                     // unordered list
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])){ items.push(mdInline(lines[i].replace(/^\s*[-*]\s+/,""))); i++; }
      out.push(`<ul class="mdlist">${items.map(x=>`<li>${x}</li>`).join("")}</ul>`); continue;
    }
    if (/^\s*\d+\.\s+/.test(l)){                                    // ordered list
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])){ items.push(mdInline(lines[i].replace(/^\s*\d+\.\s+/,""))); i++; }
      out.push(`<ol class="mdlist">${items.map(x=>`<li>${x}</li>`).join("")}</ol>`); continue;
    }
    if (/^\s*`{3,}/.test(l)){                                       // fenced code block
      const lang = l.replace(/^\s*`{3,}/, "").trim();
      i++;
      const codeLines = [];
      while (i < lines.length && !/^\s*`{3,}\s*$/.test(lines[i])){ codeLines.push(lines[i]); i++; }
      if (i < lines.length) i++;   // skip closing ```
      const langLabel = lang ? `<span class="mdcode-lang">${lang}</span>` : "";
      out.push(`<div class="mdcode"><div class="mdcode-head">${langLabel}<button class="mdcode-copy" onclick="copyCode(this)">复制</button></div><pre><code>${codeLines.join("\n")}</code></pre></div>`);
      continue;
    }
    if (/^\s*[-*_]{3,}\s*$/.test(l)){ out.push("<hr class='mdhr'>"); i++; continue; } // hr
    if (/^\s*$/.test(l)){ i++; continue; }
    const para = [];                                                // paragraph
    while (i < lines.length && lines[i].trim() && !/^\s*[-*]\s|^\s*\d+\.\s|^\s*#{1,6}\s/.test(lines[i])
           && !(row(lines[i]) && i+1<lines.length && sep(lines[i+1]))){
      para.push(mdInline(lines[i])); i++;
    }
    out.push(`<div class="mdp">${para.join("<br>")}</div>`);
  }
  return out.join("");
}
function copyCode(btn){
  const code = btn.closest(".mdcode").querySelector("pre code");
  navigator.clipboard.writeText(code.textContent).then(() => {
    const orig = btn.textContent;
    btn.textContent = "已复制！"; btn.classList.add("copied");
    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 2000);
  });
}
function copyMsg(btn){
  const text = btn.getAttribute("data-text") || "";
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = "已复制！"; btn.classList.add("copied");
    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 2000);
  });
}
let D = null;

// Click a section's data to open the real local file/folder (editor or Finder).
function revealFile(p){ fetch("/api/reveal?path=" + encodeURIComponent(p)); }
const reveal = (path, label) => `<a class="reveal" onclick="revealFile('${path}')">${esc(label)}</a>`;

const statusZh = s => ({retrieve:"检索", skip:"跳过", quick:"快速", full:"完整",
  pass:"通过", fail:"失败", error:"错误", ok:"正常", running:"运行中",
  done:"完成", configured:"已配置", connected:"已连接", enabled:"已启用",
  unconfigured:"未配置", disabled:"已禁用"}[s] || s);

// --- memory CRUD (dashboard side). `editing` pauses the 5s rebuild so an
// in-progress edit isn't wiped (same idea as the animation guard).
let editing = false;
// All dashboard POST requests go through this small adapter.  The old one-liner
// called Response.json() unconditionally, which turned an empty 404/connection
// response into the much less useful browser error "Unexpected end of JSON
// input".  Reading text first lets us report the HTTP status and still handle
// a server-side error that is returned as JSON.
async function postJSON(url, body){
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json", "Accept": "application/json"},
      cache: "no-store",
      body: JSON.stringify(body),
    });
  } catch (error) {
    throw new Error(`无法连接 Web（${error.message || error}）`);
  }

  const text = await response.text();
  let payload = {};
  if (text.trim()) {
    try {
      payload = JSON.parse(text);
    } catch (_error) {
      throw new Error(`服务器返回了无法解析的响应（HTTP ${response.status}）`);
    }
  } else if (response.ok) {
    throw new Error(`服务器返回了空响应（HTTP ${response.status}）`);
  }
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

// --- Shared row atoms.
//
// Deliberately three tiny FRAGMENTS, not one big sessionRow()/pinnedRow().
// The session inbox (views.js) and the conversation's thread menu (chat.js) draw
// genuinely different things — a card in a tab versus an item in a dropdown —
// so a shared row component would need a parameter for every difference and
// would be worse than the duplication. What they actually share is these three
// facts about a session, and those are the parts that drift: add a gateway and
// the tag strip changes in two places; change the date format and the meta line
// changes in two places. That has already happened once in this repo.

// The interface tags on a conversation (dashboard / cli).
const gwTags = s => (s.sources||[]).map(src =>
  `<span class="gwtag ${esc(src)}">${esc(src)}</span>`).join("");

// "12 msg · 2026-07-26 21:56" — a session's size and when it last moved.
const sessionMeta = s =>
  `${s.messages} 条消息 · ${esc((s.last_at||"").slice(0,16).replace("T"," "))}`;

// One tool in a stage strip. Shared by the conversation's harness strip
// (render.js) and the arena's per-card strip (compare.js) — those two strips
// are otherwise different on purpose (the arena has no gate/reply stage and
// wraps), but the chip itself must look identical in both or the same tool
// call appears to be two different things.
const toolChip = name => `<span class="stage done">工具 · ${esc(name)}</span>`;

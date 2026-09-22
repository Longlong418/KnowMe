// knowme dashboard — the Reader: your document library and the reading pane.
// Split out of main.js (it was ~200 lines of an unrelated concern sharing a file
// with the render loop) because this app is the one that grows per format.
// Classic <script>, shared global scope (no build step, no modules).
// Load order + rules: static/README.md.
//
// WHAT IS STORED WHERE: every document lives in the LIBRARY on the server —
// the original bytes plus the extracted text (applications/library.py). This
// file holds no documents; it holds the state of the reading session:
//
//   currentDoc    the open document (metadata + text + its file_url)
//   readerShown   how much of a long document has been RENDERED so far
//   pdfSession    the loaded pdf.js document + which pages are on screen
//
// All three survive the 5s poll because render() skips rebuilding this view
// (see main.js); restoreReaderState() repaints from them on every tick.

let currentDoc = null;
let libraryDocs = [];
let libraryLoaded = false;
let readerQuery = "";
const READER_DOC_KEY = "knowme_reader_open_doc";
// How much of the open document is on screen. A 400 KB document rendered in one
// innerHTML is a multi-second freeze in the tab, so text is painted in chunks
// and the rest is one click away. See renderReaderText().
const READER_CHUNK = 60000;
let readerShown = READER_CHUNK;
// The loaded pdf.js document and how many of its pages have been drawn.
let pdfSession = null;          // {docId, doc, rendered}
const PDF_MAX_PAGES = 60;       // a 900-page PDF must not try to draw itself
const PDF_PAGES_PER_BATCH = 8;

VIEWS.reader = function(){
  const askOpen = askPanelOpen();
  return `<section class="reader-shell${askOpen ? " ask-open" : ""}">
    <aside class="reader-library">
      <div class="rl-head">
        <span class="reader-kicker">文档库 · ${esc(ACTIVE_AGENT || "general")}</span>
        <label class="reader-drop"><input type="file" id="rc-file-input"
          accept=".md,.markdown,.txt,.py,.js,.ts,.json,.csv,.html,.htm,.xml,.pdf"
          onchange="readerLoad(this)"><span>+ 添加文件</span></label>
      </div>
      <div class="reader-url">
        <input type="url" id="rc-url-input" placeholder="粘贴 https:// 链接" autocomplete="off">
        <button class="sessbtn" onclick="handleUrlInput()">读取</button>
      </div>
      <input type="search" id="rc-search" class="reader-search" placeholder="搜索文档内容…"
             autocomplete="off" oninput="readerSearch()">
      <div id="rc-library" class="reader-docs"></div>
    </aside>
    <div class="reader-pane">
      <div class="reader-paper-head">
        <span id="rc-title">当前文档</span>
        <span class="meta" id="rc-meta">选中文本后可发送给 Agent</span>
        <button class="sessbtn ask-toggle" onclick="toggleAskPanel()"
                title="随时就正在读的内容提问">${askOpen ? "收起提问" : "问 Agent"}</button>
      </div>
      <div id="rc-content"><p class="empty">从左边选一份文档，或先添加一份。</p></div>
      <div id="rc-selection" class="reader-selection">
        <div><b>选中文本</b> · 已捕获</div><div id="rc-text"></div>
        <div class="rs-actions">
          <button class="save" onclick="rcSend()">发送给 Agent</button>
          <button class="sessbtn" onclick="rcClearSelection()">取消</button>
        </div>
      </div>
    </div>
    ${askOpen ? askPanelHTML() : ""}
  </section>`;
};

// --- the ask panel: the Reader agent, next to the document --------------------
//
// Same chat machine as the conversation view (sendChatTo in render.js), pointed
// at its own message list and its own agent. It is NOT a second copy of the
// chat: this file supplies three things — where the messages live (ASK), who
// answers (ASK_AGENT), and how to repaint (.asklog) — and reuses everything
// else, including the timeline cards.
//
// The log element carries `.asklog`, deliberately NOT `.chatlog`: syncChatLogs()
// fans out to every `.chatlog` in the document, so a shared class name would
// paint the MAIN conversation's messages into this panel. The stats toggle is
// therefore wired to `.asklog` as well (style.css).
const ASK_AGENT = "reader";
const ASK = [];
let askLoaded = false;

function askPanelOpen(){
  const saved = localStorage.getItem("knowme_ask_open");
  return saved === null ? true : saved === "1";   // on by default: it is the point
}
function toggleAskPanel(){
  localStorage.setItem("knowme_ask_open", askPanelOpen() ? "0" : "1");
  render();                                        // the panel is part of the view
}
function askPanelHTML(){
  return `<aside class="reader-ask">
    <div class="ra-head">
      <span class="reader-kicker">提问 · READER AGENT</span>
      <span class="meta">${esc(currentDoc ? currentDoc.title : "还没有打开文档")}</span>
    </div>
    <div class="asklog"></div>
    <div class="chatbar">
      <input id="amsg" placeholder="就这份材料提问…" autocomplete="off">
      <button id="asend">发送</button>
    </div>
  </aside>`;
}

const ASK_CHAT = {chat: ASK, agentId: () => ASK_AGENT,
                  repaint: () => syncLogClass("asklog", ASK, ASK_EMPTY)};
const ASK_EMPTY = "问点什么吧——这个 Agent 看得到你正在读的文档，也能自己查文档库。";

// Called by wireChat() alongside the main composer. Re-binding on every rebuild
// is required because the panel's markup is generated.
function wireAsk(){
  const b = document.getElementById("asend"), i = document.getElementById("amsg");
  if (!b && !i) return;
  if (b) b.onclick = () => sendChatTo(ASK_CHAT, i);
  if (i) i.onkeydown = e => { if (e.key === "Enter") sendChatTo(ASK_CHAT, i); };
  if (!askLoaded) loadAskThread();
  syncLogClass("asklog", ASK, ASK_EMPTY);
}

// Show the reader agent's current thread. Read-only: it loads the agent's
// existing session without touching the main conversation's SESSION, and the
// next question continues it (sending with agent_id=reader appends server-side).
async function loadAskThread(){
  askLoaded = true;
  const sid = (D && D.current_sessions && D.current_sessions[ASK_AGENT]) || "default";
  let r;
  try {
    r = await postJSON("/api/session", {action: "history", id: sid, agent_id: ASK_AGENT});
  } catch (error) {
    return;
  }
  if (!r.ok) return;
  ASK.length = 0;
  (r.history || []).map(histItem).forEach(m => ASK.push(m));
  syncLogClass("asklog", ASK, ASK_EMPTY);
}

// --- the library list --------------------------------------------------------

// Fetched once per page load, and again after anything that changes it (add,
// delete) — NOT on the 5s poll, because the poll does not rebuild this view and
// the list is not something the server pushes.
async function loadLibrary(force){
  if (libraryLoaded && !force) return libraryDocs;
  try {
    const res = await postJSON("/api/library", {action: "list", agent_id: ACTIVE_AGENT});
    libraryDocs = (res && res.documents) || [];
    libraryLoaded = true;
    renderLibrary();
    return libraryDocs;
  } catch (error) {
    // Keep the retry visible instead of leaving an empty list that looks like
    // a successful, brand-new library.  `loadLibrary(true)` is safe to call
    // again after the user restarts the Dashboard or fixes its port.
    const el = document.getElementById("rc-library");
    if (el) {
      const message = esc(error.message || error);
      el.innerHTML = `<div class="empty">文档库加载失败：${message}<br>
        <button class="sessbtn" onclick="loadLibrary(true)">重试</button></div>`;
    }
    return [];
  }
}

// Paint the list (or the search results, which have the same row shape plus a
// snippet). Stamp-guarded like the document pane: this runs on the poll, and
// rewriting it would drop the highlight as you arrow through results.
function renderLibrary(){
  const el = document.getElementById("rc-library");
  if (!el) return;
  const rows = readerQuery ? librarySearchRows : libraryDocs;
  const stamp = `${readerQuery}|${rows.map(d => d.id).join(",")}`;
  if (el.dataset.stamp === stamp) return;
  el.dataset.stamp = stamp;
  if (!rows.length){
    el.innerHTML = `<div class="empty">${readerQuery ? "没有匹配的文档" : "文档库还是空的"}</div>`;
    return;
  }
  el.innerHTML = rows.map(d => `<div class="rdoc ${currentDoc && currentDoc.id === d.id ? "on" : ""}"
      onclick="readerOpen('${esc(d.id)}')">
    <span class="rdoc-kind">${esc(kindBadge(d.kind))}</span>
    <span class="rdoc-title">${esc(d.title)}</span>
    <span class="rdoc-meta">${esc(documentMeta(d))}</span>
    ${d.snippet ? `<span class="rdoc-snippet">${esc(d.snippet)}</span>` : ""}
    <button class="rdoc-del" title="从文档库删除"
            onclick="event.stopPropagation();readerDelete('${esc(d.id)}')">&times;</button>
  </div>`).join("");
}

const kindBadge = kind => ({pdf: "PDF", markdown: "MD", html: "HTML", csv: "CSV",
  json: "JSON", code: "COD", text: "TXT"}[kind] || "TXT");

function documentMeta(d){
  // 0 chars is not "0 字" — it is a scan, and it is worth saying so in the list
  // rather than letting it look like an empty file until you open it.
  const size = d.chars ? (d.chars >= 1000 ? `${Math.round(d.chars / 1000)}k 字` : `${d.chars} 字`)
                       : "无文字层";
  return `${size} · ${(d.created_at || "").slice(0, 10)}`;
}

let librarySearchRows = [];
let searchTimer = null;
function readerSearch(){
  const input = document.getElementById("rc-search");
  readerQuery = (input && input.value || "").trim();
  clearTimeout(searchTimer);
  // Debounced: search is a round trip, and this fires on every keystroke.
  searchTimer = setTimeout(async () => {
    if (!readerQuery){ librarySearchRows = []; renderLibrary(); return; }
    try {
      const res = await postJSON("/api/library", {action: "search", query: readerQuery});
      librarySearchRows = (res && res.results) || [];
      renderLibrary();
    } catch (error) {
      const el = document.getElementById("rc-library");
      if (el) el.innerHTML = `<div class="empty">搜索失败：${esc(error.message || error)}</div>`;
    }
  }, 220);
}

// --- adding documents --------------------------------------------------------

// `input` is the element the change event fired on (the markup passes `this`).
// Reading the file off the event target instead of looking the input up by id
// keeps this correct even if the view is rebuilt underneath us: the element
// that actually received the pick still holds it. The id lookup remains as a
// fallback for any caller that does not pass one.
function readerLoad(input){
  input = input || document.getElementById("rc-file-input");
  if (!input || !input.files || !input.files[0]) { alert("请选择一个文件"); return; }
  const file = input.files[0];
  const reader = new FileReader();
  reader.onload = async e => {
    try {
      const response = await postJSON("/api/library", {
        action: "upload", name: file.name, data: e.target.result, agent_id: ACTIVE_AGENT});
      if (!response.ok) throw new Error(response.error || "文件解析失败");
      input.value = "";                       // let the same file be picked again
      await loadLibrary(true);
      await readerOpen(response.document.id);
    } catch (error) { alert("文件加载失败：" + error.message); }
  };
  reader.readAsDataURL(file);
}

async function handleUrlInput(){
  const input = document.getElementById("rc-url-input");
  const url = input && input.value.trim();
  if (!url) return;
  try {
    const response = await postJSON("/api/library", {action: "url", url, agent_id: ACTIVE_AGENT});
    if (!response.ok) throw new Error(response.error || "URL 解析失败");
    input.value = "";
    await loadLibrary(true);
    await readerOpen(response.document.id);
  } catch (error) {
    alert("URL 加载失败（目标可能不允许服务端读取，或不是可解析的文档）：" + error.message);
  }
}

async function readerDelete(docId){
  if (!confirm("从文档库删除这份文档？原文文件也会一起删除。")) return;
  try {
    await postJSON("/api/library", {action: "delete", doc_id: docId});
  } catch (error) {
    alert("删除失败：" + (error.message || error));
    return;
  }
  if (currentDoc && currentDoc.id === docId){
    currentDoc = null;
    localStorage.removeItem(READER_DOC_KEY);
  }
  await loadLibrary(true);
  renderReaderDoc();
}

// --- opening and painting a document ----------------------------------------

async function readerOpen(docId){
  let res;
  try {
    res = await postJSON("/api/library", {action: "open", doc_id: docId});
  } catch (error) {
    alert("文档打不开：" + (error.message || error));
    return;
  }
  if (!res.ok) { alert("文档打不开：" + (res.error || "未知错误")); return; }
  currentDoc = {...res.document, text: res.text, has_more: res.has_more,
                file_url: res.file_url};
  localStorage.setItem(READER_DOC_KEY, docId);
  readerShown = READER_CHUNK;             // a new document starts at the top
  pdfSession = null;                      // and any previous PDF is gone
  await publishReaderContext({selection: ""});
  renderLibrary();
  renderReaderDoc();
}

// Repaint the document pane from currentDoc. Called on open, on "load more",
// and on every poll via restoreReaderState().
function renderReaderDoc(){
  const contentEl = document.getElementById("rc-content");
  const titleEl = document.getElementById("rc-title");
  const metaEl = document.getElementById("rc-meta");
  if (!contentEl) return;
  if (titleEl) titleEl.textContent = currentDoc ? currentDoc.title : "当前文档";
  if (metaEl && currentDoc)
    metaEl.textContent = `${kindBadge(currentDoc.kind)} · ${currentDoc.chars} 字 · 选中文本后可发送给 Agent`;
  if (!currentDoc){
    contentEl.dataset.stamp = "";
    contentEl.dataset.pdf = "";
    contentEl.innerHTML = '<p class="empty">从左边选一份文档，或先添加一份。</p>';
    return;
  }
  // One stamp per document AND per rendered length: a poll ("same document, same
  // amount shown") must not touch innerHTML, or it drops the text you have
  // selected mid-drag — the bug this whole guard exists for. A change either way
  // (different document, or you clicked 继续加载) repaints.
  const stamp = `${currentDoc.id}:${readerShown}`;
  if (contentEl.dataset.stamp === stamp) return;
  contentEl.dataset.stamp = stamp;
  if (currentDoc.kind === "pdf") renderReaderPdf(contentEl);
  else renderReaderText(contentEl);
}

function renderReaderText(contentEl){
  contentEl.dataset.pdf = "";
  contentEl.classList.remove("reader-pdfwrap");
  const total = currentDoc.text.length;
  // A document with no extractable text is not a bug to report as one: a
  // scanned PDF renders fine, it just has no characters for us to search or
  // quote. Saying which it is beats showing an empty pane.
  if (!total){
    contentEl.innerHTML = `<p class="empty">这份文档没有可提取的文字（可能是扫描件，
      里面是文字的图像而不是文字）。你可以正常阅读它，但搜索和「发送给 Agent」都看不到它的内容。</p>`;
    return;
  }
  const shown = Math.min(total, readerShown);
  const body = renderMarkdown(readerChunk(currentDoc.text, shown));
  // Rendered markdown must live inside .r: every .md* style in style.css is
  // scoped to it, so without the wrapper headings, lists and tables come out
  // unstyled — which is exactly how "markdown 渲染不了" looked.
  const more = shown < total
    ? `<div class="reader-more"><button class="sessbtn" onclick="readerMore()">
        继续加载（还有 ${Math.round((total - shown) / 1000)}k 字）</button></div>`
    : "";
  contentEl.innerHTML = `<div class="r">${body}</div>${more}`;
}

// Cut at a blank line so a chunk never splits a code fence or a table in half,
// which would change what the markdown renders to. Falls back to the hard limit
// when there is no boundary to cut on.
function readerChunk(text, shown){
  if (shown >= text.length) return text;
  const cut = text.lastIndexOf("\n\n", shown);
  return text.slice(0, cut > shown * 0.6 ? cut : shown);
}

function readerMore(){
  readerShown += READER_CHUNK;
  renderReaderDoc();
}

// --- PDF rendering (pdf.js) --------------------------------------------------
//
// The ONE non-classic dependency in the frontend, loaded by dynamic import():
// native browser modules, no bundler, no build step. The import is lazy and
// cached, so opening a markdown document never downloads a 1.2 MB worker.
//
// Version and provenance: static/vendor/pdfjs/README.md. In short: 6.x because
// anything before 4.2.67 has CVE-2024-4367 (JS execution out of a crafted PDF).
let pdfLibPromise = null;
function loadPdfLib(){
  if (pdfLibPromise) return pdfLibPromise;
  pdfLibPromise = import("/static/vendor/pdfjs/build/pdf.min.mjs").then(lib => {
    lib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/build/pdf.worker.min.mjs";
    return lib;
  }).catch(err => { pdfLibPromise = null; throw err; });   // retry on a later open
  return pdfLibPromise;
}

// pdf.js 6 computes a document fingerprint with Uint8Array.prototype.toHex
// (pdf.worker.min.mjs, six call sites), so on a browser without it EVERY pdf
// fails — not an edge case, it is the first thing getDocument does. toHex is
// from 2025 (Chrome/Edge 140, Firefox 133, Safari 18.2). Checking here, before
// 1.7 MB of renderer is fetched, turns an internal "n.toHex is not a function"
// into a sentence that names the fix.
//
// toHex is the one and only hard requirement: it is asked for on every document.
// Nothing else belongs in this check — an earlier version of it also demanded
// Uint8Array.prototype.fromBase64, which does not exist because fromBase64 is a
// STATIC method on the constructor (Uint8Array.fromBase64, used once in the
// worker for base64-embedded content). Reading it off the prototype returned
// undefined on every browser ever made, so that check refused to render ANY pdf,
// everywhere, and looked exactly like "PDF 不支持". Ask for what the worker
// really calls, on the object that really carries it.
function pdfUnsupportedReason(){
  if (typeof Uint8Array.prototype.toHex === "function") return "";
  return "当前浏览器缺少 PDF 渲染需要的 JavaScript 特性（Uint8Array.toHex）。" +
         "Chrome/Edge 140+、Firefox 133+、Safari 18.2+ 才有；升级浏览器即可。";
}
// pdf.js's defaults assume a bundler, so every asset path is given explicitly.
// Without cmaps a Chinese or Japanese PDF renders as blank pages, which is the
// failure that looks most like "PDF 不支持".
const PDF_ASSETS = {
  cMapUrl: "/static/vendor/pdfjs/cmaps/", cMapPacked: true,
  standardFontDataUrl: "/static/vendor/pdfjs/standard_fonts/",
  wasmUrl: "/static/vendor/pdfjs/image_decoders/",
  iccUrl: "/static/vendor/pdfjs/iccs/",
};

// The PDF branch could not render. Fall back to the extracted text when there
// is any, and say plainly when there is not (a scan has nothing to fall back to).
function pdfFallback(contentEl, message){
  contentEl.classList.remove("reader-pdfwrap");
  contentEl.innerHTML = currentDoc.text
    ? `<div class="meta">${message}。下面是从文件中抽取的文字。</div>
       <pre class="pdf-fallback">${esc(currentDoc.text.slice(0, READER_CHUNK))}</pre>`
    : `<div class="meta">${message}。</div>
       <p class="empty">这份 PDF 也没有可提取的文字，所以没有可以退而求其次显示的正文。</p>`;
}

async function renderReaderPdf(contentEl){
  contentEl.innerHTML = `<div class="meta">正在加载 PDF…</div>`;
  contentEl.classList.add("reader-pdfwrap");
  const unsupported = pdfUnsupportedReason();
  if (unsupported){ pdfFallback(contentEl, unsupported); return; }
  let lib;
  try {
    lib = await loadPdfLib();
  } catch (error) {
    // pdf.js missing or blocked. The extracted text is still here, so show it
    // rather than an error page — and when there is no text either (a scan),
    // say what happened instead of rendering an empty box.
    pdfFallback(contentEl, `PDF 渲染器加载失败（${esc(String(error))}）`);
    return;
  }
  try {
    if (!pdfSession || pdfSession.docId !== currentDoc.id){
      const doc = await lib.getDocument({url: currentDoc.file_url, ...PDF_ASSETS}).promise;
      pdfSession = {docId: currentDoc.id, doc, lib, rendered: 0};
    }
  } catch (error) {
    pdfFallback(contentEl, `PDF 打不开：${esc(String(error))}`);
    return;
  }
  contentEl.innerHTML = "";
  await pdfDrawPages(contentEl, 0);
}

// Draw pages [from, from+PDF_PAGES_PER_BATCH), then offer the next batch. Pages
// are canvas + a text layer over it, so the text in the PDF can be selected and
// sent to the agent exactly like text in a markdown document.
async function pdfDrawPages(contentEl, from){
  const {doc, lib} = pdfSession;
  const width = Math.max(320, contentEl.clientWidth || 720);
  const last = Math.min(doc.numPages, from + PDF_PAGES_PER_BATCH, PDF_MAX_PAGES);
  for (let n = from + 1; n <= last; n++){
    const page = await doc.getPage(n);
    const base = page.getViewport({scale: 1});
    const scale = Math.min(2, width / base.width);     // never upscale past 2x
    const viewport = page.getViewport({scale});
    const wrap = document.createElement("div");
    wrap.className = "pdf-page";
    wrap.style.width = `${Math.floor(viewport.width)}px`;
    wrap.style.height = `${Math.floor(viewport.height)}px`;
    const canvas = document.createElement("canvas");
    const ratio = window.devicePixelRatio || 1;         // crisp on HiDPI screens
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    const textLayer = document.createElement("div");
    textLayer.className = "textLayer";
    wrap.append(canvas, textLayer);
    contentEl.appendChild(wrap);
    await page.render({canvasContext: canvas.getContext("2d"),
                       viewport, transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0]}).promise;
    // The text layer is what makes a PDF selectable. pdf.js positions the spans
    // and does the character matching; the styling it needs (.textLayer in
    // style.css) is transcribed from pdf.js's own web/pdf_viewer.css, which is
    // Apache-2.0 like the library itself.
    await new lib.TextLayer({
      textContentSource: await page.getTextContent(),
      container: textLayer,
      viewport,
    }).render();
  }
  pdfSession.rendered = last;
  const more = doc.numPages > last
    ? `<div class="reader-more"><button class="sessbtn" onclick="pdfMore()">
        继续加载第 ${last + 1}–${Math.min(doc.numPages, last + PDF_PAGES_PER_BATCH)} 页
        （共 ${doc.numPages} 页）</button></div>`
    : "";
  contentEl.insertAdjacentHTML("beforeend", more);
}

async function pdfMore(){
  const contentEl = document.getElementById("rc-content");
  if (!contentEl || !pdfSession) return;
  contentEl.querySelector(".reader-more")?.remove();
  await pdfDrawPages(contentEl, pdfSession.rendered);
}

// --- selection -> the agent --------------------------------------------------

// Publish the open document (and any selection) to the Context Bridge, so the
// next turn of the ACTIVE agent sees what you are reading. Kept out of the
// poll: this runs on open and on send, which are the moments the context
// actually changes.
function publishReaderContext(extra = {}){
  if (!currentDoc) return Promise.resolve({ok:false, error:"no document"});
  return postJSON("/api/extras", {
    application: "reader",
    resource: currentDoc.title,
    content: currentDoc.text,
    selection: extra.selection || "",
    metadata: {doc_id: currentDoc.id, kind: currentDoc.kind},
    agent_id: ACTIVE_AGENT,
    session_id: SESSION || D?.current_sessions?.[ACTIVE_AGENT] || "default"
  });
}

function rcSend(){
  const sel = window.getSelection();
  if (!sel.rangeCount || !sel.toString().trim()) { alert("请先在文档中选中文字"); return; }
  const text = sel.toString();
  publishReaderContext({selection: text}).then(res => {
    if (res.ok){
      const box = document.getElementById("dmsg");
      if (box) box.value = "[选中文本] " + text.substring(0, 200) + (text.length > 200 ? "..." : "");
      const panel = document.getElementById("rc-selection");
      if (panel) panel.style.display = "none";
      // The composer lives in the #agent view; offer the way there rather than
      // silently filling a box the reader cannot see.
      if (!box && typeof openAgent === "function") openAgent(ACTIVE_AGENT);
    } else {
      alert("注入失败：" + (res.error || "未知错误"));
    }
  }).catch(err => alert("网络错误：" + err));
}

function rcClearSelection(){
  window.getSelection()?.removeAllRanges();
  const panel = document.getElementById("rc-selection");
  if (panel) panel.style.display = "none";
}

// Detect a fresh selection and reveal the send panel. We never rewrite the
// document here — see the stamp guard in renderReaderDoc().
function syncReaderSelection(){
  const selEl = document.getElementById("rc-selection");
  const textEl = document.getElementById("rc-text");
  if (!selEl || !textEl) return;
  const text = window.getSelection().toString().trim();
  if (text){
    textEl.textContent = text;
    selEl.style.display = "block";
  } else {
    selEl.style.display = "none";
  }
}

// Called at the end of every render() (main.js) while the reader is the view.
// Async, and its result is deliberately ignored by the caller: render() is
// synchronous and must not block on a fetch. Awaiting the list here is what
// keeps the ORDER right — the list paints before the pane does, and a caller
// that does want to wait (the tests) can.
async function restoreReaderState(){
  // loadLibrary() itself fetches once per page (libraryLoaded), which is all the
  // restore below needs: after a refresh the JS state starts empty, so the first
  // call here does hit the server. Re-fetching on every poll would break the
  // "a poll does not re-fetch" rule this pane is built on.
  await loadLibrary();
  // The document itself lives in memory, so the 5s poll keeps it for free --
  // but a page reload starts with nothing. readerOpen() wrote the id to
  // localStorage for exactly that case: come back to the document you were
  // reading, unless it is gone from the library.
  if (!currentDoc){
    const savedId = localStorage.getItem(READER_DOC_KEY);
    if (savedId && libraryDocs.some(doc => doc.id === savedId)) await readerOpen(savedId);
  }
  renderLibrary();
  renderReaderDoc();
  syncReaderSelection();
}

// --- Coding Workspace --------------------------------------------------------
// Kept here rather than in main.js: it is the same interaction as the Reader
// (open a local thing, publish it to the Context Bridge) and it published under
// the same `application: coding` snapshot.
let currentCodingFile = "";
let currentCodingContent = "";
function openCodingFileEncoded(encoded){
  openCodingFile(decodeURIComponent(encoded));
}
async function openCodingFile(path){
  const res = await postJSON("/api/workspace", {
    action: "read", path, agent_id: ACTIVE_AGENT
  });
  if (!res.ok) return alert("文件无法打开：" + (res.error || "未知错误"));
  currentCodingFile = res.file.path;
  currentCodingContent = res.file.content;
  await postJSON("/api/extras", {
    application: "coding", resource: currentCodingFile,
    content: currentCodingContent, selection: "", agent_id: ACTIVE_AGENT,
    session_id: SESSION || D?.current_sessions?.[ACTIVE_AGENT] || "default"
  });
  if (activeView === "coding") render();
}
function closeCodingFile(){
  currentCodingFile = "";
  currentCodingContent = "";
  if (activeView === "coding") render();
}

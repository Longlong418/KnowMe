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
let pdfSession = null;          // {docId, doc, lib, rendered, zoom}
const PDF_MAX_PAGES = 60;       // a 900-page PDF must not try to draw itself
const PDF_PAGES_PER_BATCH = 8;
const PDF_ZOOM_MIN = 0.25;      // ctrl+wheel zoom of the page, not of the window
const PDF_ZOOM_MAX = 4;
const PDF_ZOOM_STEP = 1.15;
let pdfPendingAnchor = null;    // where under the pointer the zoom is pinned
let pdfZoomTimer = null;
let pdfRedrawing = false;

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
        <span id="rc-doc-acts" style="display:none">
          <button class="sessbtn" onclick="readerRename()"
                  title="只改文档库里的显示名，磁盘上的文件不动">重命名</button>
          <button class="sessbtn" onclick="readerClose()"
                  title="关掉这份文档：Agent 也就不再看得到它">&times;</button>
        </span>
        <span class="meta" id="rc-meta">选中文本后可在右侧提问</span>
        <button class="sessbtn ask-toggle" onclick="toggleAskPanel()"
                title="随时就正在读的内容提问">${askOpen ? "收起提问" : "问 Agent"}</button>
      </div>
      <div id="rc-content"><p class="empty">从左边选一份文档，或先添加一份。</p></div>
      <div id="rc-selection" class="reader-selection">
        <div><b>选中文本</b> · 已捕获</div><div id="rc-text"></div>
        <div class="rs-actions">
          <button class="save" onclick="rcSend()">在右侧提问</button>
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
// Which thread the panel currently holds — the ID, not a yes/no. It used to be a
// one-way `askLoaded = true`, so the first thread loaded stayed the panel's
// thread for the life of the page: opening another document changed nothing
// until you collapsed and reopened the panel, which rebuilt the whole view and
// started the state over. Comparing ids is what lets a document's own thread
// arrive without a rebuild.
let askLoadedFor = null;

function askPanelOpen(){
  const saved = localStorage.getItem("knowme_ask_open");
  return saved === null ? true : saved === "1";   // on by default: it is the point
}
function toggleAskPanel(){ setAskPanel(!askPanelOpen()); }
function setAskPanel(open){
  localStorage.setItem("knowme_ask_open", open ? "1" : "0");
  // The panel is part of the view markup, so it only follows this by being
  // REBUILT -- and a bare render() does not rebuild the Reader: the
  // `view === "reader" && !subChanged` branch skips the rebuild on purpose, so
  // the 5s poll cannot destroy the file input or a live text selection. That
  // guard also swallowed the 收起提问 click, which is why it did nothing.
  // Clearing activeView makes render() see a view change, exactly as a
  // navigation would, so it rebuilds this one time and the poll stays quiet.
  activeView = null;
  render();
}
function askPanelHTML(){
  return `<aside class="reader-ask">
    <div class="ra-head">
      <span class="reader-kicker">提问 · READER AGENT</span>
      <span class="meta" id="ra-title">${esc(currentDoc ? currentDoc.title : "还没有打开文档")}</span>
    </div>
    <div class="asklog"></div>
    <div class="attpreview" id="aatt" hidden></div>
    <div class="chatbar">
      <input type="file" id="afile" accept="image/*" multiple hidden>
      <button id="apick" class="attachbtn" title="发图片（也可以直接把截图粘贴进来）">🖼</button>
      <input id="amsg" placeholder="就这份材料提问…" autocomplete="off">
      <button id="asend">发送</button>
    </div>
  </aside>`;
}

// The panel's header names the document, and it is repainted from the ONE place
// that paints the document — renderReaderDoc(). It used to be baked into the
// markup when the view was built, so it named whatever document happened to be
// open at that instant and nothing ever revised it afterwards.
function paintAskTitle(){
  const el = document.getElementById("ra-title");
  if (el) el.textContent = currentDoc ? currentDoc.title : "还没有打开文档";
}

const ASK_CHAT = {chat: ASK, agentId: () => ASK_AGENT,
                  repaint: force => syncLogClass("asklog", ASK, ASK_EMPTY, force),
                  key: "a", attach: []};
const ASK_EMPTY = "问点什么吧——这个 Agent 看得到你正在读的文档，也能自己查文档库。";

// Called by wireChat() alongside the main composer. Re-binding on every rebuild
// is required because the panel's markup is generated.
function wireAsk(){
  const b = document.getElementById("asend"), i = document.getElementById("amsg");
  if (!b && !i) return;
  if (b) b.onclick = () => sendChatTo(ASK_CHAT, i);
  if (i) i.onkeydown = e => { if (e.key === "Enter") sendChatTo(ASK_CHAT, i); };
  wireComposer(ASK_CHAT);   // 📎 + paste, same code as the main composer
  if (askLoadedFor !== askSessionId()) loadAskThread();
  syncLogClass("asklog", ASK, ASK_EMPTY);
}

// Show the reader agent's current thread. Read-only: it loads the agent's
// existing session without touching the main conversation's SESSION, and the
// next question continues it (sending with agent_id=reader appends server-side).
async function loadAskThread(){
  const sid = askSessionId();
  let r;
  try {
    r = await postJSON("/api/session", {action: "history", id: sid, agent_id: ASK_AGENT});
  } catch (error) {
    return;
  }
  if (!r.ok) return;
  askLoadedFor = sid;
  fillAsk(r.history || []);
}

// Put a thread's rows into the panel. In place — the `.asklog` node is the live
// one and syncLogClass() paints it (see render.js), so nothing here needs the
// view to be rebuilt, which is what would drop a text selection.
function fillAsk(history){
  ASK.length = 0;
  history.map(histItem).forEach(m => ASK.push(m));
  syncLogClass("asklog", ASK, ASK_EMPTY, true);   // a loaded thread opens at its end
}

// Point the panel at the thread the OPEN DOCUMENT owns (see askSessionId).
// 'switch' is what makes the next question belong to this document, and its
// response already carries that thread's rows, so the panel is painted from it
// rather than fetching the same history a second time. Wrapped in try/catch on
// purpose: switching is the one call that BUILDS the reader agent, and building
// it needs a provider key — a machine without one must still open a document.
async function openAskThread(){
  const sid = askSessionId();
  if (!currentDoc){ askLoadedFor = null; await loadAskThread(); return; }
  try {
    const r = await postJSON("/api/session",
                             {action: "switch", id: sid, agent_id: ASK_AGENT});
    if (r && r.ok){ askLoadedFor = sid; fillAsk(r.history || []); }
  } catch (error) {
    /* keep reading; the panel just will not have this document's thread yet */
  }
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
  // The stamp must cover EVERYTHING the markup below depends on, or the
  // early return keeps a stale list. It was (query | row ids), and the row ids
  // do not change when you open a DIFFERENT document -- so the `.on` highlight
  // stayed on the document you first opened and never followed your clicks.
  // The open document's id is part of the markup, so it is part of the stamp.
  // The NAME is in there too, for the same reason: 重命名 changes what the row
  // says and changes no id at all, so an id-only stamp would leave the old name
  // in the list until something else happened to move the highlight.
  const stamp = `${readerQuery}|${currentDoc ? currentDoc.id : ""}|`
                + rows.map(d => d.id + ":" + d.title).join(",");
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
  if (!confirm("从文档库删除这份文档？原文文件、以及这份文档自己的对话都会一起删除，不可恢复。"))
    return;
  const wasOpen = !!(currentDoc && currentDoc.id === docId);
  try {
    await postJSON("/api/library", {action: "delete", doc_id: docId});
  } catch (error) {
    alert("删除失败：" + (error.message || error));
    return;
  }
  // Deleting the document you are reading closes it, and closing it is what
  // takes its snapshot off the bridge. Deleting some OTHER document is not a
  // reason to touch the key this page filed for the one on screen.
  if (wasOpen) await readerClose();
  await loadLibrary(true);
  renderLibrary();
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
  // Paint first, then move the panel: the question you ask next belongs to THIS
  // document, and the panel has to say so before anything else can go wrong.
  await openAskThread();
}

// Close the document: forget it on screen AND take it off the bridge.
//
// The bridge used to be write-only — publishReaderContext() filed a snapshot
// under two keys and nothing ever removed it, so an agent went on "seeing" a
// document you had closed hours ago. The server has had the action all along
// (extras_action's "clear"); this is the caller it never had.
async function readerClose(){
  await clearReaderContext();             // before currentDoc is gone: it needs the key
  currentDoc = null;
  localStorage.removeItem(READER_DOC_KEY);
  readerShown = READER_CHUNK;
  pdfSession = null;
  askLoadedFor = null;
  renderLibrary();
  renderReaderDoc();
  // …and leave the document's own thread. Keeping it would mean a question asked
  // with no document open continues the conversation of the document you just
  // closed — the panel would go on answering about a document that is gone.
  try {
    const r = await postJSON("/api/session", {action: "new", agent_id: ASK_AGENT});
    if (r && r.session_id){
      D.current_sessions = {...(D.current_sessions || {}), [ASK_AGENT]: r.session_id};
      askLoadedFor = null;
      fillAsk([]);
    }
  } catch (error) {
    /* the panel just keeps its thread; nothing about reading depends on this */
  }
  paintAskTitle();
}

// Rename the document in the LIBRARY only (documents.title). The stored file,
// its id and its content are untouched — this is the name you read, not the name
// on disk.
async function readerRename(){
  if (!currentDoc) return;
  const asked = prompt("文档显示名（只改库里的名字，磁盘上的文件不动）", currentDoc.title);
  if (asked === null) return;
  let r;
  try {
    r = await postJSON("/api/library",
                       {action: "rename", doc_id: currentDoc.id, title: asked});
  } catch (error) {
    alert("重命名失败：" + (error.message || error));
    return;
  }
  if (!r || !r.ok){ alert("重命名失败：" + ((r && r.error) || "未知错误")); return; }
  currentDoc.title = r.title;             // the name the server actually stored
  await loadLibrary(true);                // the list shows the new name too
  renderLibrary();
  renderReaderDoc();
  // The bridge carries the NAME as well as the text (metadata.resource), so a
  // rename that skipped this would leave the agent calling your document by its
  // old name for as long as it stayed open — the two-places-one-fact bug again.
  await publishReaderContext({selection: ""});
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
  // 重命名 / 关闭 only mean something with a document open, and the ask panel's
  // header names the same document. Both follow this one paint: it runs on open,
  // on delete, on 继续加载, on the poll's restore, and after a rename — so there is
  // no second place for the panel's title to go stale.
  const acts = document.getElementById("rc-doc-acts");
  if (acts) acts.style.display = currentDoc ? "" : "none";
  paintAskTitle();
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
      pdfSession = {docId: currentDoc.id, doc, lib, rendered: 0, zoom: 1};
    }
  } catch (error) {
    pdfFallback(contentEl, `PDF 打不开：${esc(String(error))}`);
    return;
  }
  wirePdfZoom(contentEl);
  // Repainting the SAME document keeps its zoom (the panel toggle repaints);
  // opening another one starts from the session's fresh zoom of 1.
  contentEl.classList.toggle("zoomed", pdfSession.zoom !== 1);
  contentEl.innerHTML = "";
  await pdfDrawPages(contentEl, 0);
}

// Draw pages [from, from+PDF_PAGES_PER_BATCH), then offer the next batch. Pages
// are canvas + a text layer over it, so the text in the PDF can be selected and
// sent to the agent exactly like text in a markdown document.
async function pdfDrawPages(contentEl, from){
  const {doc, lib} = pdfSession;
  contentEl.querySelector(".reader-more")?.remove();
  const width = Math.max(320, contentEl.clientWidth || 720);
  const last = Math.min(doc.numPages, from + PDF_PAGES_PER_BATCH, PDF_MAX_PAGES);
  for (let n = from + 1; n <= last; n++){
    const page = await doc.getPage(n);
    const base = page.getViewport({scale: 1});
    // Fit the page to the pane, then apply the reader's own zoom on top. At
    // zoom 1 this is exactly what it always was.
    const fit = Math.min(2, width / base.width);       // never upscale past 2x
    const viewport = page.getViewport({scale: fit * pdfSession.zoom});
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
  await pdfDrawPages(contentEl, pdfSession.rendered);
}

// --- zooming the page, not the window ---------------------------------------

// Ctrl+wheel is the browser's own page zoom, which on a document viewer zooms
// the wrong thing: the sidebar and the library grow with the text and you lose
// your place. So the pane takes the gesture for itself — preventDefault needs a
// non-passive listener — and re-draws the pages bigger or smaller.
function wirePdfZoom(contentEl){
  // #rc-content survives a repaint (renderReaderDoc replaces its children, not
  // the element), so the flag lives on the element: a plain addEventListener
  // here would add one more zoom step on every repaint.
  if (contentEl.dataset.zoomWired) return;
  contentEl.dataset.zoomWired = "1";
  contentEl.addEventListener("wheel", (ev) => {
    if (!ev.ctrlKey || !pdfSession) return;
    ev.preventDefault();
    // Pin the zoom to the point under the pointer, the way a map does.
    pdfPendingAnchor = pdfPointerAnchor(contentEl, ev.clientY);
    const step = ev.deltaY < 0 ? PDF_ZOOM_STEP : 1 / PDF_ZOOM_STEP;
    pdfSession.zoom = Math.min(PDF_ZOOM_MAX, Math.max(PDF_ZOOM_MIN, pdfSession.zoom * step));
    contentEl.classList.toggle("zoomed", pdfSession.zoom !== 1);
    schedulePdfRedraw();
  }, {passive: false});
}

// Which page the pointer is over, and how far down it (0..1). Only the index is
// kept: the redraw throws the page elements away and makes new ones.
function pdfPointerAnchor(contentEl, clientY){
  const pages = contentEl.querySelectorAll(".pdf-page");
  for (let i = 0; i < pages.length; i++){
    const rect = pages[i].getBoundingClientRect();
    if (clientY < rect.bottom){
      return {index: i, frac: Math.min(1, Math.max(0, (clientY - rect.top) / (rect.height || 1)))};
    }
  }
  return pages.length ? {index: pages.length - 1, frac: 0} : null;
}

function schedulePdfRedraw(){
  // One redraw per burst of wheel ticks: re-rendering 8 pages takes longer than
  // the ticks arrive, so a redraw started per tick would only queue up.
  clearTimeout(pdfZoomTimer);
  pdfZoomTimer = setTimeout(pdfRedraw, 160);
}

async function pdfRedraw(){
  const contentEl = document.getElementById("rc-content");
  if (!contentEl || !pdfSession) return;
  const pages = contentEl.querySelectorAll(".pdf-page");
  if (!pages.length) return;                     // the pane is not showing this PDF
  if (pdfRedrawing){ schedulePdfRedraw(); return; }
  const anchor = pdfPendingAnchor;
  const page = anchor ? pages[anchor.index] : null;
  // Plain numbers, taken now: the rect is only useful before the pages are
  // thrown away, and this keeps the arithmetic below readable.
  const rect = page ? page.getBoundingClientRect() : null;
  const beforeTop = rect ? rect.top : 0;
  const beforeHeight = rect ? rect.height : 0;
  pdfRedrawing = true;
  try {
    const target = Math.min(pdfSession.rendered, pages.length);  // keep 继续加载's pages
    contentEl.innerHTML = "";
    for (let from = 0; from < target; from += PDF_PAGES_PER_BATCH){
      await pdfDrawPages(contentEl, from);
    }
  } finally {
    pdfRedrawing = false;
  }
  if (rect) pdfRestoreAnchor(contentEl, anchor, beforeTop, beforeHeight);
}

// Zoom changes the height of everything above the page you are looking at, so
// the scroll offset has to be corrected or the text jumps out from under the
// pointer. The anchored point sits `frac` of the way down its page: the page
// moved by (top - beforeTop), and grew by (height - beforeHeight), of which
// `frac` is above the point.
function pdfRestoreAnchor(contentEl, anchor, beforeTop, beforeHeight){
  const pages = contentEl.querySelectorAll(".pdf-page");
  const page = pages[Math.min(anchor.index, pages.length - 1)];
  if (!page) return;
  const rect = page.getBoundingClientRect();
  scrollParent(contentEl).scrollTop +=
    (rect.top - beforeTop) + anchor.frac * (rect.height - beforeHeight);
}

// The nearest scrolling ancestor — <main> in this layout. Writing to scrollTop
// works for whatever that turns out to be, so this does not hardcode the shell.
function scrollParent(el){
  for (let node = el.parentElement; node; node = node.parentElement){
    const overflowY = getComputedStyle(node).overflowY;
    if (overflowY === "auto" || overflowY === "scroll") return node;
  }
  return document.scrollingElement || document.documentElement;
}

// --- selection -> the agent --------------------------------------------------

// Publish the open document (and any selection) to the Context Bridge, so the
// next turn of an agent sees what you are reading. Kept out of the poll: this
// runs on open and on send, which are the moments the context actually changes.
//
// It publishes TWICE, because two different agents can be asked about this
// document and the bridge is keyed by (agent, session):
//   * ASK_AGENT — the panel on the right. It asks as `reader`, and the server
//     looks the snapshot up as (agent.agent_id, agent.session.session_id).
//   * ACTIVE_AGENT — the main conversation, for the 选中文字→发送 flow (rcSend),
//     which hands the text to the agent you are browsing as.
// Publishing only under ACTIVE_AGENT is why the panel used to answer "我看不到
// 你打开的是哪篇": the document was filed under ("default", <default thread>)
// while the panel's own turn ran as `reader` and looked up ("reader", ...).
function publishReaderContext(extra = {}){
  if (!currentDoc) return Promise.resolve({ok:false, error:"no document"});
  const snapshot = {
    application: "reader",
    resource: currentDoc.title,
    content: currentDoc.text,
    selection: extra.selection || "",
    metadata: {doc_id: currentDoc.id, kind: currentDoc.kind}
  };
  const targets = readerContextTargets();
  return Promise.all(targets.map(([agent_id, session_id]) =>
    postJSON("/api/extras", {...snapshot, agent_id, session_id})
  )).then(results => ({ok: results.every(r => r && r.ok), results}));
}

// Where the open document is filed. ONE list: publishReaderContext() writes
// these keys and clearReaderContext() deletes them, so the two cannot disagree
// about where the document lives.
function readerContextTargets(){
  return [
    [ASK_AGENT, askSessionId()],
    [ACTIVE_AGENT, SESSION || D?.current_sessions?.[ACTIVE_AGENT] || "default"]
  ];
}

// Take the document back off the bridge — the undo of publishReaderContext().
// Same two keys, same expression, and idempotent: clearing a key nothing was
// written to is not an error.
function clearReaderContext(){
  return Promise.all(readerContextTargets().map(([agent_id, session_id]) =>
    postJSON("/api/extras", {action: "clear", agent_id, session_id})
  )).catch(() => null);
}

// The reader agent's own thread: the one the OPEN DOCUMENT owns, so a document's
// questions stay with the document (one document, one conversation). The prefix
// is not arbitrary — the server treats `doc-` threads specially in two places
// (browser_agent.DOC_PREFIX: they are never resumed as "the recent chat", never
// rotated), so the two sides have to spell it the same way.
//
// No document open -> fall back to whatever thread the reader agent is in.
const ASK_DOC_PREFIX = "doc-";
function askSessionId(){
  if (currentDoc) return ASK_DOC_PREFIX + currentDoc.id;
  return (D && D.current_sessions && D.current_sessions[ASK_AGENT]) || "default";
}

function rcSend(){
  const sel = window.getSelection();
  if (!sel.rangeCount || !sel.toString().trim()) { alert("请先在文档中选中文字"); return; }
  const text = sel.toString();
  return publishReaderContext({selection: text}).then(res => {
    if (!res.ok){ alert("注入失败：" + (res.error || "未知错误")); return; }
    const panel = document.getElementById("rc-selection");
    if (panel) panel.style.display = "none";
    // It lands in the ask panel on the right, NOT in the main conversation.
    // This used to prefill #dmsg and, when that was not on screen, navigate to
    // #agent/<active> to go find it — so quoting a sentence threw you out of the
    // document you were reading. The panel is already next to the document, and
    // sending there is what the reader's own agent is for; the selection still
    // reaches the main conversation, because publishReaderContext() above files
    // it under BOTH agents.
    //
    // The panel's markup is generated, so a CLOSED panel has to be opened
    // through a rebuild (see setAskPanel) -- and the box can only be filled
    // after it, because the rebuild replaces it. An open panel is left alone:
    // rebuilding it would throw away the text selection for nothing.
    if (!askPanelOpen()) setAskPanel(true);
    const box = document.getElementById("amsg");
    if (!box) return;
    box.value = "[选中文本] " + text.substring(0, 200) + (text.length > 200 ? "..." : "");
    box.focus();
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

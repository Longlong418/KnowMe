// knowme dashboard — render/refresh loop, resizers/chrome, bootstrap (LOADS LAST).
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

let activeView = null, activeSub = null;
// The hash keys stay as they are — #settings is linked from graph.js, views.js,
// the README and DEMO-CHECKLIST, and from anyone's bookmark. Only the LABEL
// moved: after the Connections registry took keys, providers and integrations
// out of that page, what remained was two switches that change how a turn runs,
// which is a behaviour, not a setting.
const TITLES = {chat:"聊天与观察", overview:"总览", gateway:"网关", loop:"循环",
                memory:"记忆", tools:"工具", models:"模型", connections:"连接",
                ops:"LLM 运维", reader:"阅读器", knowledge:"知识库",
                graph:"图工作流——为循环提供结构",
                settings:"行为——每轮对话如何运行",
                database:"数据库——KnowMe 在 state.db 中保存的一切"};
TITLES.coding = "Coding Workspace";
function render(){
  if (!D) return;
  const [v, subRaw] = (location.hash||"#overview").slice(1).split("/");
  const sub = subRaw || null;
  const view = VIEWS[v] ? v : "overview";
  const subChanged = sub !== activeSub || view !== activeView;
  const effSub = sub;
  document.querySelectorAll("nav a").forEach(a=>a.classList.toggle("on",
    a.dataset.v === view && (!a.dataset.sub || a.dataset.sub === effSub)));
  document.getElementById("title").textContent =
    TITLES[`${view}/${effSub}`] || TITLES[view] || view[0].toUpperCase()+view.slice(1);
  if (view === "overview" || view === "graph"){
    // don't rebuild mid-animation or the glowing SVG gets wiped
    if (activeView !== view || !animating){ document.getElementById("view").innerHTML = VIEWS[view](D); }
  } else if ((view === "memory" || view === "settings" || view === "database" || view === "models" || view === "connections") && editing && !subChanged){
    // don't wipe an in-progress edit on the 5s refresh — but DO switch sub-tabs
  } else {
    editing = false;
    // Rebuilding #view innerHTML resets the scroll. On a same-view refresh (the
    // 5s poll, a sort click) keep the reader where they were — only jump to top
    // on an actual navigation (subChanged), where top is correct.
    const main = document.querySelector("main");
    const keepScroll = !subChanged && main;
    const y = keepScroll ? main.scrollTop : 0;
    document.getElementById("view").innerHTML = VIEWS[view](D, sub);
    if (keepScroll) main.scrollTop = y;
  }
  activeView = view; activeSub = sub;
  document.getElementById("model").textContent = `${D.provider} · ${D.model}`;
  syncAgentChrome();
  document.getElementById("n-gw").textContent = (D.chat_log||[]).length;
  document.getElementById("n-loop").textContent = D.stats.turns;
  document.getElementById("n-graph").textContent =
    (D.graph && (D.graph.stats.quick + D.graph.stats.full)) || "";
  document.getElementById("n-mem").textContent = D.facts.length + D.episodes.length;
  document.getElementById("n-tools").textContent = D.calendar.length + D.outbox.length;
  document.getElementById("n-db").textContent = (D.db && D.db.all_tables.length) || "";
  document.getElementById("n-ops").textContent = D.stats.tool_errors || (D.eval_report ? "" : "!");
  // Restore reader state if on reader view
  if (view === "reader") restoreReaderState();
}
let lastFetch = Date.now();
function tickLive(){
  if (!D) return;
  const ago = Math.round((Date.now()-lastFetch)/1000);
  document.getElementById("sub").innerHTML =
    `<span class="live"><span class="dot"></span>实时</span> · ${ago} 秒前更新 · ${esc(D.home)}`;
}
let dockRestored = false;
async function restoreDock(){
  // On page load the dock is empty even though the current thread has messages
  // — restore them so a refresh never looks like it lost the chat.
  dockRestored = true;
  const sid = D && D.current_sessions && D.current_sessions[ACTIVE_AGENT];
  if (!sid || CHAT.length) return;
  await loadThreadInto(sid, {setSession: true});
}
async function refresh(){
  try {
    D = await (await fetch("/api/data?agent_id=" + encodeURIComponent(ACTIVE_AGENT || "default"))).json(); lastFetch = Date.now();
    render(); tickLive();
    syncModelChip();  // keep the dock's model pill in sync with the active brain
    applyTele();      // reflect the stats on/off choice (default on)
    syncLiveView();   // live-update an opened conversation (e.g. new phone messages)
    if (!dockRestored) restoreDock();
  } catch(e){ /* server restarting — keep showing last data */ }
}
// --- resizable columns: drag the thin handle between nav|main and main|dock.
// Width lives in a CSS var + localStorage, so it survives refreshes.
function wireResizer(id, cssVar, key, fromRight, min, max){
  const el = document.getElementById(id);
  if (!el) return;
  el.onmousedown = e => {
    e.preventDefault();
    document.body.classList.add("resizing");
    const move = ev => {
      let w = fromRight ? (window.innerWidth - ev.clientX) : ev.clientX;
      w = Math.max(min, Math.min(max, w));
      document.documentElement.style.setProperty(cssVar, w + "px");
      localStorage.setItem(key, w);
    };
    const up = () => { document.body.classList.remove("resizing");
      document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  };
}
function wireChrome(){
  // restore saved widths
  const nw = localStorage.getItem("navW"); if (nw) document.documentElement.style.setProperty("--nav-w", nw+"px");
  const dw = localStorage.getItem("dockW"); if (dw) document.documentElement.style.setProperty("--dock-w", dw+"px");
  wireResizer("nav-resizer", "--nav-w", "navW", false, 150, 380);
  wireResizer("dock-resizer", "--dock-w", "dockW", true, 260, 680);
  // hide / show the sidebar
  const setNav = v => { document.body.classList.toggle("nav-hidden", v); localStorage.setItem("navHidden", v?"1":"0"); };
  const nt = document.getElementById("nav-toggle"), nr = document.getElementById("nav-reopen");
  if (nt) nt.onclick = () => setNav(true);
  if (nr) nr.onclick = () => setNav(false);
  setNav(localStorage.getItem("navHidden") === "1");
}

window.addEventListener("hashchange", render);
window.__hold = (v)=>{ animating = v; };   // test hook: freeze the diagram
wireDock(); wireChrome();
refresh(); setInterval(refresh, 5000); setInterval(tickLive, 1000);
pollEvents(); setInterval(pollEvents, 450);   // live harness animation

// Reader App helpers
let currentDoc = null;

// The Reader is intentionally a focused workspace, not another admin form.
// Keep this view close to its interaction code so adding a new document format
// does not require hunting through the larger diagnostics view file.
VIEWS.reader = function(){
  return `<section class="reader-hero"><div class="reader-kicker">READING ROOM · ${esc(ACTIVE_AGENT || "general")}</div>
    <h2>把一份材料带进来</h2><p>上传文本或粘贴公开 URL。解析后的内容会同步给当前 Agent，方便提问、摘录和做笔记。</p>
    <div class="reader-actions"><label class="reader-drop"><input type="file" id="rc-file-input" accept=".md,.txt,.py,.json,.csv,.html,.htm,.xml,.pdf" onchange="readerLoad()"><span>选择文件</span><small>MD · TXT · HTML · PDF</small></label>
    <div class="reader-url"><input type="url" id="rc-url-input" placeholder="https://example.com/article"><button class="save" onclick="handleUrlInput()">读取 URL</button></div></div></section>
    <section class="reader-paper"><div class="reader-paper-head"><span>当前文档</span><span class="meta">选中文本后可发送给 Agent</span></div><div id="rc-content"><p class="empty">还没有文档。先选择文件或读取 URL。</p></div></section>
    <div id="rc-selection" class="reader-selection"><div><b>选中文本</b> · 已捕获</div><div id="rc-text"></div><button class="save" onclick="rcSend()">发送给 Agent</button></div>`;
};

function renderReaderContent(){
  const contentEl = document.getElementById("rc-content");
  const selEl = document.getElementById("rc-selection");
  if (!contentEl || !selEl) return;
  if (currentDoc){
    const paragraphs = currentDoc.content.split(/\n{2,}/).filter(Boolean);
    contentEl.innerHTML = paragraphs.map(p => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`).join("");
    contentEl.dataset.path = currentDoc.name;
  } else {
    contentEl.innerHTML = '<p style="color:var(--ink2)">从左侧文件输入、拖拽文档或粘贴 URL 进入阅读...</p>';
    delete contentEl.dataset.path;
  }
  if (window.getSelection().toString().trim()){
    const text = window.getSelection().toString();
    document.getElementById("rc-text").textContent = text;
    selEl.style.display = "block";
  } else {
    selEl.style.display = "none";
  }
}

function readerLoad(){
  const input = document.getElementById("rc-file-input");
  if (!input.files || !input.files[0]) { alert("请选择一个文件"); return; }
  const file = input.files[0];
  const reader = new FileReader();
  reader.onload = async e => {
    try {
      const response = await postJSON("/api/reader", {action: "upload", name: file.name, data: e.target.result});
      if (!response.ok) throw new Error(response.error || "文件解析失败");
      currentDoc = response.document;
      renderReaderContent();
      await publishReaderContext({selection: ""});
    } catch (error) { alert("文件加载失败：" + error.message); }
  };
  reader.readAsDataURL(file);
}

function handleUrlInput(){
  const input = document.getElementById("rc-url-input");
  if (!input || !input.value.trim()) return;
  const url = input.value.trim();
  // Browser fetch keeps the MVP dependency-free. Public pages must allow CORS;
  // when they do not, the error tells the user to download the file instead.
  postJSON("/api/reader", {action: "url", url})
    .then(response => {
      if (!response.ok) throw new Error(response.error || "URL 解析失败");
      currentDoc = response.document;
      renderReaderContent();
      return publishReaderContext({selection: ""});
    })
    .catch(error => alert("URL 加载失败（目标可能不允许浏览器跨域读取）：" + error));
}

function publishReaderContext(extra = {}){
  if (!currentDoc) return Promise.resolve({ok:false, error:"no document"});
  return postJSON("/api/extras", {
    application: "reader",
    resource: currentDoc.name,
    content: currentDoc.content,
    selection: extra.selection || "",
    agent_id: ACTIVE_AGENT,
    session_id: SESSION || D?.current_sessions?.[ACTIVE_AGENT] || "default"
  });
}

function rcSend(){
  const sel = window.getSelection();
  if (!sel.rangeCount || !sel.toString().trim()) { alert("请先在文档中选中文字"); return; }
  const text = sel.toString();
  publishReaderContext({selection: text}).then(res => {
    if (res.ok) {
      document.getElementById("dmsg").value = "[选中文本] " + text.substring(0, 200) + (text.length > 200 ? "..." : "");
      document.getElementById("rc-selection").style.display = "none";
    } else {
      alert("注入失败：" + (res.error || "未知错误"));
    }
  }).catch(err => alert("网络错误：" + err));
}

// Restore reader state on render (called at end of render() in main.js)
function restoreReaderState(){
  renderReaderContent();
}

// Coding Workspace keeps the selected file in the browser while the dashboard
// polls. Opening a file is read-only, then publishes the same Context Bridge
// snapshot used by Reader so the active Agent can discuss the code immediately.
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

// Knowledge Base helpers
let currentNoteId = null;
let currentNoteContent = "";

async function createKnowledgeNote(){
  const title = document.getElementById("kb-title")?.value?.trim();
  const folder = document.getElementById("kb-folder")?.value?.trim() || "default";
  const content = document.getElementById("kb-content")?.value || "";
  if (!title) return alert("请输入笔记标题");
  const res = await postJSON("/api/knowledge", {
    action: "create", title, folder, content, agent_id: ACTIVE_AGENT
  });
  if (res.ok) location.hash = "#knowledge";
}

async function viewKnowledgeNote(noteId){
  currentNoteId = noteId;
  const res = await postJSON("/api/knowledge", {
    action: "get", note_id: noteId, agent_id: ACTIVE_AGENT
  });
  if (res.ok){
    document.getElementById("kb-note-detail").style.display = "block";
    document.getElementById("kb-edit-title").value = res.note?.title || "";
    document.getElementById("kb-edit-content").value = res.note?.content || "";
    renderKnowledgePreview();
  }
}

async function saveKnowledgeNote(){
  const id = currentNoteId;
  const content = document.getElementById("kb-edit-content")?.value || "";
  const title = document.getElementById("kb-edit-title")?.value?.trim();
  if (!id) return;
  const res = await postJSON("/api/knowledge", {
    action: "update", note_id: id, content, title, agent_id: ACTIVE_AGENT
  });
  if (res.ok) location.hash = "#knowledge";
}

async function deleteKnowledgeNote(){
  if (!currentNoteId) return;
  if (!confirm("确定删除这条笔记吗？此操作不可撤销。")) return;
  const res = await postJSON("/api/knowledge", {
    action: "delete", note_id: currentNoteId, agent_id: ACTIVE_AGENT
  });
  closeKnowledgeDetail();
  location.hash = "#knowledge";
}

function closeKnowledgeDetail(){
  currentNoteId = null;
  currentNoteContent = "";
  document.getElementById("kb-note-detail").style.display = "none";
}

function renderKnowledgePreview(){
  const content = document.getElementById("kb-edit-content")?.value || "";
  const safe = esc(content);
  const html = safe.replace(/\[\[([^\]]+)\]\]/g,
    '<a href="#knowledge" style="color:var(--accent)">[$1]</a>');
  document.getElementById("kb-preview").innerHTML = "<pre>" + html + "</pre>";
}

function filterKnowledgeNotes(){
  const folder = document.getElementById("kb-folder-filter")?.value || "";
  if (folder) localStorage.setItem("knowme_kb_folder", folder);
  else localStorage.removeItem("knowme_kb_folder");
  location.hash = "#knowledge"; // Will refresh and filter
}

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
                ops:"LLM 运维", reader:"阅读器",
                graph:"图工作流——为循环提供结构",
                settings:"行为——每轮对话如何运行",
                database:"数据库——KnowMe 在 state.db 中保存的一切"};
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
  document.getElementById("n-gw").textContent = (D.chat_log||[]).length;
  document.getElementById("n-loop").textContent = D.stats.turns;
  document.getElementById("n-graph").textContent =
    (D.graph && (D.graph.stats.quick + D.graph.stats.full)) || "";
  document.getElementById("n-mem").textContent = D.facts.length + D.episodes.length;
  document.getElementById("n-tools").textContent = D.calendar.length + D.outbox.length;
  document.getElementById("n-db").textContent = (D.db && D.db.all_tables.length) || "";
  document.getElementById("n-ops").textContent = D.stats.tool_errors || (D.eval_report ? "" : "!");
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
  const sid = D && D.current_session;
  if (!sid || CHAT.length) return;
  await loadThreadInto(sid, {setSession: true});
}
async function refresh(){
  try {
    D = await (await fetch("/api/data")).json(); lastFetch = Date.now();
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

function readerLoad(){
  const input = document.getElementById("rc-file-input");
  if (!input.files || !input.files[0]) { alert("请选择一个文件"); return; }
  const file = input.files[0];
  const reader = new FileReader();
  reader.onload = e => {
    currentDoc = { name: file.name, content: e.target.result };
    document.getElementById("rc-content").innerHTML = "<pre>" + esc(e.target.result) + "</pre>";
    document.getElementById("rc-content").dataset.path = file.name;
    publishReaderContext({selection: ""});
  };
  reader.readAsText(file);
}

function publishReaderContext(extra = {}){
  if (!currentDoc) return Promise.resolve({ok:false, error:"no document"});
  return postJSON("/api/extras", {
    application: "reader",
    resource: currentDoc.name,
    content: currentDoc.content,
    selection: extra.selection || "",
    session_id: SESSION || D?.current_session || "default"
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

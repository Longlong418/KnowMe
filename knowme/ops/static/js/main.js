// knowme dashboard — render/refresh loop, resizers/chrome, bootstrap (LOADS LAST).
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.
//
// This file is the LOOP, not a home for app code: it decides which VIEWS[hash]
// renders, keeps the poll honest, and starts everything. An app's own helpers
// belong in its own file (reader.js, chat.js, models.js, memory.js) — the Reader
// used to live at the bottom of this one, and moving it out is what made both
// files readable.

let activeView = null, activeSub = null;
// The hash keys stay as they are — #settings is linked from graph.js, views.js,
// the README and DEMO-CHECKLIST, and from anyone's bookmark. Only the LABEL
// moved: after the Connections registry took keys, providers and integrations
// out of that page, what remained was two switches that change how a turn runs,
// which is a behaviour, not a setting.
const TITLES = {agent:"对话", overview:"总览", gateway:"网关", loop:"循环",
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
  // The hash is the source of truth for WHICH agent you are looking at, so a
  // sidebar click, the back button and a pasted #agent/coding link all land the
  // same way. selectAgent() is a pure state switch and never writes the hash,
  // which is why this can't recurse: after it runs the two agree and this stops
  // firing. selection changes have already taken effect by the time it returns
  // (its first await is below that), so the render continues normally.
  if (view === "agent" && sub && sub !== ACTIVE_AGENT) selectAgent(sub);
  const subChanged = sub !== activeSub || view !== activeView;
  const effSub = sub;
  document.querySelectorAll("nav a").forEach(a=>a.classList.toggle("on",
    a.dataset.v === view && (!a.dataset.sub || a.dataset.sub === effSub)));
  document.getElementById("title").textContent =
    TITLES[`${view}/${effSub}`] || TITLES[view] || view[0].toUpperCase()+view.slice(1);
  if (view === "overview" || view === "graph"){
    // don't rebuild mid-animation or the glowing SVG gets wiped
    if (activeView !== view || !animating){ document.getElementById("view").innerHTML = VIEWS[view](D); }
  } else if (view === "reader" && !subChanged){
    // The Reader owns transient state the server knows nothing about: the open
    // document, whatever you have typed into the URL box, and — the one that
    // bit us — the <input type="file">'s FileList. Rebuilding this view on the
    // 5s poll destroyed that input while the native file picker was still open,
    // so choosing a file then alerted "请选择一个文件": readerLoad() re-queried
    // the DOM by id and found the fresh, empty replacement. Same reason a URL
    // you had typed but not yet submitted vanished. Repainting is also what
    // cleared a mid-drag text selection in the document. There is nothing on
    // this page that the poll refreshes, so skip the rebuild entirely and let
    // restoreReaderState() below paint the document.
  } else if (view === "agent" && !subChanged){
    // Same trap as the Reader, and the same fix. The conversation panel owns
    // state the server knows nothing about: what you have typed into #dmsg, where
    // the log is scrolled, which <details> you expanded. Rebuilding it every 5s
    // would wipe all three mid-sentence. Navigations still rebuild (subChanged),
    // and the thread itself is repainted from CHAT by wireChat() below — which
    // is the part that actually needs to track the poll.
  } else if ((view === "memory" || view === "settings" || view === "database" || view === "models" || view === "connections") && editing && !subChanged){
    // don't wipe an in-progress edit on the 5s refresh — but DO switch sub-tabs
  } else if (view === "knowledge" && !subChanged && (editing || currentNoteId)){
    // Keep the selected note open while the 5s data poll updates the sidebar.
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
  // Restore reader state if on reader view. Its ask panel rides on the chat
  // machine, so the same rebuild needs its composer bound — wireChat() also
  // wires the reader panel (wireAsk) for exactly this reason.
  if (view === "reader"){ restoreReaderState(); wireChat(); }
  if (view === "knowledge" && typeof restoreKnowledgeState === "function"){
    restoreKnowledgeState();
  }
  // The conversation markup is generated, so its input and log have to be
  // (re)bound and repainted after every rebuild.
  if (view === "agent") wireChat();
}
let lastFetch = Date.now();
function tickLive(){
  if (!D) return;
  const ago = Math.round((Date.now()-lastFetch)/1000);
  document.getElementById("sub").innerHTML =
    `<span class="live"><span class="dot"></span>实时</span> · ${ago} 秒前更新 · ${esc(D.home)}`;
}
async function refresh(){
  try {
    D = await (await fetch("/api/data?agent_id=" + encodeURIComponent(ACTIVE_AGENT || "default"))).json(); lastFetch = Date.now();
    render(); tickLive();
    syncModelChip();  // keep the conversation's model pill in sync with the active brain
    applyTele();      // reflect the stats on/off choice (default on)
    syncLiveView();   // live-update an opened conversation (e.g. new phone messages)
    if (!threadRestored) restoreThread();
  } catch(e){ /* server restarting — keep showing last data */ }
}
// --- resizable sidebar: drag the thin handle between nav|main.
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
  wireResizer("nav-resizer", "--nav-w", "navW", false, 150, 380);
  // hide / show the sidebar
  const setNav = v => { document.body.classList.toggle("nav-hidden", v); localStorage.setItem("navHidden", v?"1":"0"); };
  const nt = document.getElementById("nav-toggle"), nr = document.getElementById("nav-reopen");
  if (nt) nt.onclick = () => setNav(true);
  if (nr) nr.onclick = () => setNav(false);
  setNav(localStorage.getItem("navHidden") === "1");
}

window.addEventListener("hashchange", render);
window.__hold = (v)=>{ animating = v; };   // test hook: freeze the diagram
// Open on the conversation. An empty hash used to fall back to 总览, but the
// agent thread is what you come back to — a real hash (bookmark, reload, back
// button) is respected and wins over this. wireChat() is not called here: the
// panel does not exist until render() has run, and refresh() calls it.
if (!location.hash) location.hash = "#agent/" + ACTIVE_AGENT;
wireChrome();
refresh(); setInterval(refresh, 5000); setInterval(tickLive, 1000);
pollEvents(); setInterval(pollEvents, 450);   // live harness animation

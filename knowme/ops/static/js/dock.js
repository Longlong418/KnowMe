// knowme dashboard — chat sessions/history (loadThreadInto), model chip, stats toggle.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

// --- chat sessions (the "New chat" + history picker, like a chat app)
let ACTIVE_AGENT = localStorage.getItem("knowme_active_agent") || "default";
let SESSION = "default";

function activeAgentData(){
  return ((D && D.agents) || []).find(a => a.id === ACTIVE_AGENT) ||
    {id:"default", name:"General", icon:"✦", status:"idle"};
}

function syncAgentChrome(){
  const active = activeAgentData();
  document.querySelectorAll(".agent-link").forEach(button => {
    const data = ((D && D.agents) || []).find(a => a.id === button.dataset.agent);
    button.classList.toggle("on", button.dataset.agent === ACTIVE_AGENT);
    button.classList.toggle("ready", data && data.status === "ready");
  });
  const title = document.getElementById("dock-agent");
  if (title) title.textContent = `${active.icon || "✦"} ${active.name} Agent`;
  const input = document.getElementById("dmsg");
  if (input) input.placeholder = `给 ${active.name} Agent 发消息…`;
}

async function selectAgent(agentId){
  if (!((D && D.agents) || []).some(a => a.id === agentId)) return;
  ACTIVE_AGENT = agentId;
  localStorage.setItem("knowme_active_agent", agentId);
  liveView = null;
  SESSION = (D.current_sessions || {})[agentId] || "default";
  CHAT.length = 0;
  syncAgentChrome();
  await loadThreadInto(SESSION, {setSession:true});
  await refresh();
}
async function newChat(){
  const r = await postJSON("/api/session", {action:"new", agent_id:ACTIVE_AGENT});
  if (r.session_id){ liveView = null; SESSION = r.session_id; CHAT.length = 0; syncChatLogs(); }
  closeSessMenu();
}
// The ONE way to pull a thread's rows into the dock, so the paths can't drift
// (they used to: some dropped meta, some added a length-guard, some didn't).
//   mode 'switch'  -> action:switch, also moves the agent's active thread
//   mode 'history' -> action:history, read-only ('__all__' = full timeline)
// Replaces CHAT + repaints, unless `guard` is set and the length is unchanged
// (the live-poll case, to avoid a needless redraw). Returns the items or null.
async function loadThreadInto(id, {mode = "history", setSession = false, guard = false} = {}){
  const r = await postJSON("/api/session", {action: mode, id, agent_id:ACTIVE_AGENT});
  if (!r.ok) return null;
  const fresh = (r.history || []).map(histItem);
  if (guard && fresh.length === CHAT.length) return fresh;   // unchanged -> skip repaint
  if (setSession){
    SESSION = r.session_id;
    if (typeof publishReaderContext === "function" && currentDoc)
      await publishReaderContext({selection:""});
  }
  CHAT.length = 0; fresh.forEach(m => CHAT.push(m)); syncChatLogs();
  return fresh;
}
async function switchSession(id){
  await loadThreadInto(id, {mode: "switch", setSession: true});
  closeSessMenu();
}
// Open a conversation from the Gateway inbox: load it into the dock (the active
// thread), keep it live-synced (so new CLI messages appear), and make
// sure the dock is visible.
let liveView = null;   // a conversation opened from the inbox, kept live-updated
async function openConversation(id){
  document.body.classList.remove("dock-closed");
  localStorage.setItem("dockClosed", "0");
  liveView = id;
  await switchSession(id);   // switch the agent so a reply continues this thread
  render();                  // reflect the active-session highlight in the inbox
}
// Read-only "everything" view: the full cross-thread timeline in the dock, like
// the Loop tab but as chat. Doesn't switch the agent — your next message still
// goes to the active thread; this is purely for reading your whole history.
async function viewAllHistory(){
  closeSessMenu();
  document.body.classList.remove("dock-closed");
  localStorage.setItem("dockClosed", "0");
  liveView = "__all__";
  await loadThreadInto("__all__");
}
// Re-pull the opened conversation each refresh so CLI messages show up live —
// unless a turn is mid-stream in the dock.
async function syncLiveView(){
  if (!liveView || CHAT.some(m => m.pending)) return;
  await loadThreadInto(liveView, {guard: true});   // guard: repaint only if changed
}
function closeSessMenu(){ const m=document.getElementById("sessmenu"); if(m) m.remove(); }
function toggleSessMenu(ev){
  ev.stopPropagation();
  if (document.getElementById("sessmenu")){ closeSessMenu(); return; }
  const sessions = (D && D.sessions_by_agent && D.sessions_by_agent[ACTIVE_AGENT]) || [];
  const menu = document.createElement("div");
  menu.className = "sessmenu"; menu.id = "sessmenu";
  // "All messages" shows the full cross-thread timeline (like the Loop tab, but
  // as chat) — so your whole history is one scroll, not split across threads.
  const allItem = `<div class="sessitem allitem ${liveView==='__all__'?'on':''}" onclick="viewAllHistory()">
      <div><b>全部消息</b>——完整时间线</div>
      <div class="sm">汇总所有对话，最新消息在最后</div></div>`;
  menu.innerHTML = allItem + (sessions.length ? sessions.map(s => {
    const tags = gwTags(s);
    return `<div class="sessitem ${s.id===SESSION?"on":""}" onclick="openConversation('${esc(s.id)}')">
      <div>${esc(s.title||s.id)} ${tags}</div>
      <div class="sm">${sessionMeta(s)}</div>
    </div>`;
  }).join("") : `<div class="sessitem">还没有历史对话</div>`);
  const r = ev.currentTarget.getBoundingClientRect();
  menu.style.top = (r.bottom+6)+"px";
  menu.style.left = Math.max(8, r.right-300)+"px";
  document.body.appendChild(menu);
}
document.addEventListener("click", e => {
  const m = document.getElementById("sessmenu");
  if (m && !m.contains(e.target)) closeSessMenu();
  const mm = document.getElementById("modelmenu");
  const chip = document.getElementById("modelchip");
  if (mm && !mm.contains(e.target) && e.target !== chip && !chip?.contains(e.target)) closeModelMenu();
});

// --- mini model switcher in the chat dock: a pill showing the current brain,
// clicking it drops the live catalog to swap without leaving the conversation.
// Posts to /api/providers (the same endpoint the Models page uses).
function syncModelChip(){
  const el = document.getElementById("modelchip");
  if (!el || !D || !D.settings) return;
  const st = D.settings;
  el.innerHTML = `<span class="mc-dot"></span><span class="mc-name">${esc(st.model || st.provider || "model")}</span><span class="mc-caret">&#9662;</span>`;
}
function closeModelMenu(){ const m = document.getElementById("modelmenu"); if (m) m.remove(); }

// --- per-turn stats toggle (gate / seconds / iterations / tools). On by
// default; the choice persists in localStorage. Hides the .tele blocks via a
// body class so it applies to already-rendered turns too.
function applyTele(){
  const off = localStorage.getItem("knowme_tele") === "0";
  document.body.classList.toggle("no-tele", off);
  const b = document.getElementById("teletoggle");
  if (b) b.classList.toggle("on", !off);
}
function toggleTele(){
  const off = localStorage.getItem("knowme_tele") === "0";
  localStorage.setItem("knowme_tele", off ? "1" : "0");   // flip
  applyTele();
}
function toggleModelMenu(ev){
  ev.stopPropagation();
  if (document.getElementById("modelmenu")){ closeModelMenu(); return; }
  const st = (D && D.settings) || {};
  // Disabled providers leave the switcher (the Models grid's disable button);
  // their pins stay on file and reappear when re-enabled.
  const disabled = st.disabled_providers || [];
  const pinned = (st.pinned || []).filter(p => !disabled.includes(p.provider));
  const items = pinned.length ? pinned.map(p =>
    `<div class="sessitem ${(p.provider===st.provider && p.model===st.model)?"on":""}"
          onclick="switchTo('${esc(p.provider)}','${esc(p.model)}')">
       <span class="mm-prov">${esc(p.provider)}</span> <span class="mm-id">${esc(p.model)}</span>${
       p.default?'<span class="mm-def">默认</span>':""}</div>`
  ).join("") : `<div class="sessitem">尚未固定任何模型。</div>`;
  const menu = document.createElement("div");
  menu.className = "sessmenu modelmenu"; menu.id = "modelmenu";
  menu.innerHTML = `<div class="mm-h">你的模型</div>${items}`
    + `<div class="mm-f"><a href="#models" onclick="closeModelMenu()">+ 前往“模型”添加 &rsaquo;</a></div>`;
  const r = ev.currentTarget.getBoundingClientRect();
  menu.style.top = (r.bottom + 6) + "px";
  menu.style.left = Math.max(8, r.right - 250) + "px";
  document.body.appendChild(menu);
}
// Switch BOTH provider and model in one click (a pinned model can be any
// provider). Same-provider switch keeps the gate model; cross-provider lets the
// new provider's default gate model take over.
async function switchTo(provider, model){
  const st = (D && D.settings) || {};
  const chip = document.getElementById("modelchip");
  const name = chip && chip.querySelector(".mc-name");
  closeModelMenu();
  if (name) name.textContent = "正在切换…";
  await postJSON("/api/providers", {provider, model,
    small_model: provider === st.provider ? st.small_model : ""});
  await refresh();
}

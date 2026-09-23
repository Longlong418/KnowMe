// knowme web — the conversation view (#agent/<id>), sessions/history,
// model chip, stats toggle.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.
//
// This file used to be dock.js and rendered into a permanent third column. That
// column is gone: a conversation is now a VIEW, reached by clicking an agent in
// the sidebar (`#agent/<id>`), so it gets the full width and the browser's back
// button works. Everything below that was not about the column survived the
// move unchanged.

// --- chat sessions (the "New chat" + history picker, like a chat app)
let ACTIVE_AGENT = localStorage.getItem("knowme_active_agent") || "default";
let SESSION = "default";

function activeAgentData(){
  return ((D && D.agents) || []).find(a => a.id === ACTIVE_AGENT) ||
    {id:"default", name:"General", icon:"✦", status:"idle"};
}

// The stored agent id can outlive the agent itself (the catalog changed, or the
// profile was renamed). Then /api/data answers "unknown agent: X" for every
// request, D stays null, render() bails on its first line, and the page has no
// data AND no way back: the sidebar never paints, and even a click on another
// agent cannot help because selectAgent() needs D.agents to accept it. Forget
// the id, go back to the default agent, and keep the URL honest about it.
let forgotUnknownAgent = false;
function recoverUnknownAgent(){
  forgotUnknownAgent = true;
  const unknown = ACTIVE_AGENT;
  ACTIVE_AGENT = "default";
  localStorage.setItem("knowme_active_agent", ACTIVE_AGENT);
  if (location.hash === "#agent/" + unknown) location.hash = "#agent/" + ACTIVE_AGENT;
  refresh();
}

function syncAgentChrome(){
  document.querySelectorAll(".agent-link").forEach(button => {
    const data = ((D && D.agents) || []).find(a => a.id === button.dataset.agent);
    button.classList.toggle("on", button.dataset.agent === ACTIVE_AGENT);
    button.classList.toggle("ready", data && data.status === "ready");
  });
}

// The conversation view: one agent, the full width of the pane. This is what
// the sidebar's agent buttons open. The header text and the input placeholder
// are generated here rather than patched in later by syncAgentChrome(), because
// this markup is rebuilt from scratch on every navigation — there is nothing to
// keep in sync.
VIEWS.agent = function(){
  const a = activeAgentData();
  const name = esc(a.name || "General");
  return `<section class="agent-view">
    <div class="agent-head">
      <span class="agent-title"><span class="agent-icon">${esc(a.icon || "✦")}</span>${name} Agent</span>
      <span class="arch-status"></span>
    </div>
    <div class="sesshead">
      <button class="sessbtn" onclick="newChat()">+ 新建对话</button>
      <button class="sessbtn" onclick="toggleSessMenu(event)">历史记录 &#9662;</button>
      <button class="sessbtn teletoggle" id="teletoggle" onclick="toggleTele()" title="显示或隐藏每轮统计：门控决策、耗时、迭代和工具">统计</button>
      <button class="modelchip" id="modelchip" onclick="toggleModelMenu(event)" title="切换当前对话使用的模型">&hellip;</button>
    </div>
    <div class="chatlog"></div>
    <div class="attpreview" id="datt" hidden></div>
    <div class="chatbar">
      <input type="file" id="dfile" accept="image/*" multiple hidden>
      <button id="dpick" class="attachbtn" title="发图片（也可以直接把截图粘贴进来）">🖼</button>
      <input id="dmsg" placeholder="给 ${name} Agent 发消息…" autocomplete="off">
      <button id="dsend">发送</button>
    </div>
  </section>`;
};

// The sidebar buttons call this. It ONLY routes — the hash is what decides which
// agent you are looking at, which is what makes the back button work.
function openAgent(agentId){
  const target = "#agent/" + agentId;
  // Clicking the agent you are already on leaves the hash unchanged, so no
  // hashchange fires and the click would look broken. Reload the thread by hand.
  if (location.hash === target) selectAgent(agentId);
  else location.hash = target;
}

// Pure state switch. Deliberately does NOT touch location.hash: render() is the
// only thing that routes, and it calls this when the hash and the state
// disagree. A state change that also routed would be the recursion this split
// exists to avoid.
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
// The ONE way to pull a thread's rows into the conversation, so the paths can't
// drift (they used to: some dropped meta, some added a length-guard, some
// didn't).
//   mode 'switch'  -> action:switch, also moves the agent's active thread
//   mode 'history' -> action:history, read-only (the live inbox polls this one)
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
// Open a conversation from the Gateway inbox: go to that agent's conversation
// view (the dock column it used to un-hide is gone), load the thread into it,
// and keep it live-synced so new CLI messages appear.
let liveView = null;   // a conversation opened from the inbox, kept live-updated
async function openConversation(id){
  liveView = id;
  const target = "#agent/" + ACTIVE_AGENT;
  if (location.hash !== target) location.hash = target;
  await switchSession(id);   // switch the agent so a reply continues this thread
  render();                  // reflect the active-session highlight in the inbox
}
// Delete a conversation for good. The server does the whole job in one action —
// its messages, its archived tail, and the thread itself if it is the one this
// agent is in. What survives is what was distilled OUT of it: memories and notes
// never carried a session id, so nothing you taught the agent is lost with the
// messages that taught it.
async function deleteConversation(id){
  if (!confirm("删除这条对话？消息会彻底删掉、不可恢复（已经提炼进记忆的内容保留）。")) return;
  let r;
  try {
    r = await postJSON("/api/session", {action:"delete", id, agent_id:ACTIVE_AGENT});
  } catch (error) {
    alert("删除失败：" + (error.message || error));
    return;
  }
  if (!r || !r.ok){ alert("删除失败：" + ((r && r.error) || "未知错误")); return; }
  closeSessMenu();
  if (id === SESSION){
    // We just deleted the thread this agent is in, and the server moved it to a
    // fresh one: follow that id, or the page (and the next message) would still
    // be pointing at the conversation we removed.
    liveView = null;
    SESSION = r.session_id;
    CHAT.length = 0;
    (r.history || []).map(histItem).forEach(m => CHAT.push(m));
    syncChatLogs();
    if (typeof publishReaderContext === "function" && currentDoc)
      await publishReaderContext({selection:""});
  } else if (liveView === id){
    liveView = null;         // stop live-syncing a conversation that is gone
  }
  await refresh();           // the History list loses the row
}
// Re-pull the opened conversation each refresh so CLI messages show up live —
// unless a turn is mid-stream.
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
  // One row per conversation, and nothing that merges them: each is its own
  // thread, so it is read, continued and deleted on its own. (There used to be a
  // "全部消息 —— 完整时间线" row above these, stitching every thread into one
  // timeline; it went away with the backend branch that fed it.)
  menu.innerHTML = (sessions.length ? sessions.map(s => {
    const tags = gwTags(s);
    return `<div class="sessitem ${s.id===SESSION?"on":""}" onclick="openConversation('${esc(s.id)}')">
      <div class="sess-head"><div>${esc(s.title||s.id)} ${tags}</div>
        <button class="sess-del" title="删除这条对话（不可恢复）"
                onclick="event.stopPropagation();deleteConversation('${esc(s.id)}')">&times;</button>
      </div>
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

// --- mini model switcher in the conversation header: a pill showing the
// current brain, clicking it drops the live catalog to swap without leaving the
// conversation. Posts to /api/providers (the same endpoint the Models page uses).
function syncModelChip(){
  const el = document.getElementById("modelchip");
  if (!el || !D || !D.settings) return;
  const st = D.settings;
  el.innerHTML = `<span class="mc-dot"></span><span class="mc-name">${esc(st.model || st.provider || "model")}</span><span class="mc-caret">&#9662;</span>`;
}
function closeModelMenu(){ const m = document.getElementById("modelmenu"); if (m) m.remove(); }

// --- per-turn stats toggle (gate / seconds / iterations / tools). On by
// default; the choice persists in localStorage. Hides the .tele blocks via a
// body class so it applies to already-rendered turns too. NOTE: the CSS rule
// that does the hiding is keyed off `.chatlog` (style.css), which is why the
// conversation's log element keeps that class name.
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

// On load the conversation view is empty even though the current thread has
// messages — restore them so a refresh never looks like it lost the chat.
let threadRestored = false;
async function restoreThread(){
  threadRestored = true;
  const sid = D && D.current_sessions && D.current_sessions[ACTIVE_AGENT];
  if (!sid || CHAT.length) return;
  await loadThreadInto(sid, {setSession: true});
}

// knowme web — the architecture SVG (archSVG, byte-frozen) + its live animation.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

// --- Architecture: a calm live SVG that mirrors the whiteboard's structure
// (Harness wraps the ephemeral run · Loop is a cycle · memory feeds up through
// the gate · LLM Ops is a separate loop). Deliberately few arrows + lots of
// air — the detail lives in each tab. Every node is live and clickable.
// DO NOT rewrite this chart. The data-node/data-edge ids each box emits drive
// the live animation via the STAGE map below — keep the two in sync.
function archSVG(d){
  const s = d.stats;
  const box = (x,y,w,h,title,sub,view,cls="",nid="") =>
    `<g class="node ${cls}" ${nid?`data-node="${nid}"`:""} ${view?`onclick="location.hash='${view}'"`:""}>
       <rect class="bx" x="${x}" y="${y}" width="${w}" height="${h}" rx="9"/>
       <text class="nt" x="${x+13}" y="${y+24}">${title}</text>
       ${sub?`<text class="ns" x="${x+13}" y="${y+42}">${sub}</text>`:""}
     </g>`;
  const lbl = (x,y,t) => `<text class="grp" x="${x}" y="${y}">${t}</text>`;
  const flow = (d2,cls="",eid="") => `<path class="flow ${cls}" ${eid?`data-edge="${eid}"`:""} d="${d2}"/>`;
  const flowLbl = (x,y,t,anchor="start") => `<text class="fl" x="${x}" y="${y}" text-anchor="${anchor}">${t}</text>`;

  return `<div style="overflow-x:auto"><svg viewBox="0 -10 1044 674" class="arch" role="img">
    <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" class="head"/></marker></defs>

    <!-- HARNESS container: everything runs on your laptop, including the
         offline LLM Ops loop (tinted sub-panel) -->
    <rect class="container" x="12" y="20" width="1020" height="628" rx="16"/>
    ${lbl(16,4,"运行框架——在你的电脑上运行 · 每轮内部状态都是临时的")}

    <!-- the turn: gateway → working memory → loop → reply -->
    ${box(32,72,128,56,"入口","CLI · 网页","chat","","gateway")}
    ${flow("M160 100 L192 100","","e-gw-wm")}
    ${box(192,72,144,56,"工作记忆","每轮重新组装","memory/overview","","wm")}

    <rect class="loopbox" x="370" y="56" width="168" height="166" rx="12"/>
    ${lbl(384,48,"循环")}
    ${box(384,72,140,50,"LLM 智能体","推理","loop","","llm")}
    ${box(384,152,140,52,"工具","create_event…","tools","","tools")}
    ${flow("M448 122 L448 152")}${flow("M470 152 L470 122")}
    ${flowLbl(456,141,"行动")}
    ${flow("M336 100 L370 100","","e-wm-loop")}
    ${flow("M538 100 L558 106")}${flowLbl(542,93,"回复")}
    ${box(558,84,104,52,"回复","→ 返回给你","loop","","reply")}
    <!-- The reply returns through the interface that received the request. -->
    <path class="flow" data-edge="e-reply-gw" d="M610 84 C610 40 596 34 566 34 L130 34 C104 34 96 44 96 72" marker-end="url(#arr)"/>
    ${flowLbl(348,28,"回复从同一网关返回","middle")}
    <!-- every turn is saved for consolidation: down a clear right lane,
         then left into the consolidation box -->
    <path class="flow dash" data-edge="e-reply-save" d="M650 136 C660 150 660 200 660 600 L430 600" marker-end="url(#arr)"/>
    ${flowLbl(668,214,"保存聊天",'start')}

    <!-- retrieval gate feeding working memory (the hero) -->
    <path class="gate node" data-node="gate" onclick="location.hash='memory/overview'" d="M264 250 L340 296 L264 342 L188 296 Z"/>
    <text class="nt" x="264" y="292" text-anchor="middle" style="pointer-events:none">检索门控</text>
    <text class="ns" x="264" y="310" text-anchor="middle" style="pointer-events:none">${s.gate_skips} 跳过 · ${s.gate_retrieves} 检索</text>
    ${flow("M264 250 L264 128","dash","e-gate-wm")}${flowLbl(274,196,"仅在需要时")}

    <!-- MEMORY: grouped section with a direct link from the gate to each pillar -->
    ${lbl(40,404,"记忆——三大支柱")}
    <rect class="memgroup" x="28" y="414" width="600" height="128" rx="12"/>
    ${flow("M148 452 L246 336","dash","e-gate-proc")}
    ${flow("M340 452 L272 344","dash","e-gate-sem")}
    ${flow("M542 452 L286 338","dash","e-gate-epi")}
    ${flowLbl(356,392,"门控会读取全部三类记忆",'middle')}
    ${box(44,452,208,72,"程序性记忆","如何行动 · SKILL.md · "+d.skills.length+" 个技能","memory/skills","","procedural")}
    ${box(264,452,204,72,"语义记忆 · FTS5","持久事实 · "+d.facts.length+" 条","memory/semantic","","semantic")}
    ${box(480,452,132,72,"情景记忆",d.episodes.length+" 条","memory/episodic","","episodic")}

    <!-- consolidation writes back into memory -->
    ${box(44,576,384,52,"记忆整理 · 每 "+d.consolidate_every+" 轮",d.chat_pending+"/"+d.consolidate_every*2+" 条排队 → 提炼为事实","memory/consolidation","","consolidation")}
    ${flow("M340 576 L340 528","","e-consol-sem")}${flowLbl(350,560,"提炼")}

    <!-- LLM OPS: the offline improvement loop — inside the harness (it all
         runs on the laptop) but a distinct tinted sub-panel -->
    <rect class="container ops" x="736" y="40" width="280" height="372" rx="14"/>
    ${lbl(752,64,"LLM 运维——离线改进循环")}
    ${flowLbl(752,80,"观察每次运行 · 改进智能体",'start')}
    <!-- every turn crosses the gap to feed the trace -->
    <path class="flow" data-edge="e-reply-trace" d="M660 104 C700 100 726 100 752 106" marker-end="url(#arr)"/>
    ${flowLbl(688,96,"每一轮")}
    ${box(752,92,250,50,"追踪",s.trace_files+" 个文件 · 始终开启","ops","","trace")}
    ${flow("M878 142 L878 156")}
    ${box(752,156,250,50,"评测","确定性 + 模型评判","ops")}
    ${flow("M878 206 L878 220")}
    ${box(752,220,250,50,"发布门禁",d.eval_report?"确定性 "+statusZh(d.eval_report.deterministic)+" · 评判 "+statusZh(d.eval_report.judge):"运行 make gate","ops")}
    ${flow("M878 270 L878 284")}
    ${box(752,284,250,50,"发布","新提示词 · 模型 · 配置","ops")}
    <!-- feedback: Release improves the Harness — a short arrow across the gap,
         so the outer loop closes without a long wrap crowding the margins -->
    <path class="flow dash" d="M752 312 C712 324 698 352 676 358" marker-end="url(#arr)"/>
    ${flowLbl(596,346,"改进后的提示词 + 配置",'end')}
  </svg></div>`;
}

// ---- Live harness animation: light up the diagram as a turn flows through,
// driven by the trace stream so ANY gateway (browser, phone, CLI) triggers it.
// The node/edge ids below MUST match the data-node="…"/data-edge="…" ids that
// archSVG emits above — change one, change the other (that's why both live in
// this file). test_static_assets.py won't catch a mismatch here; the animation
// just silently stops lighting a box.
const STAGE = {
  turn_start:    {nodes:["gateway","wm"],            edges:["e-gw-wm"],                 label:"收到消息"},
  gate:          {nodes:["gate"],                    edges:["e-gate-wm"],               label:"检索门控"},
  llm:           {nodes:["llm"],                     edges:["e-wm-loop"],               label:"智能体推理"},
  tool:          {nodes:["tools"],                   edges:[],                          label:"运行工具"},
  turn_end:      {nodes:["reply","trace"],           edges:["e-reply-trace","e-reply-save"], label:"回复"},
  consolidation: {nodes:["consolidation","semantic"],edges:["e-consol-sem"],            label:"整理记忆"},
};
let evCursor = null, evQueue = [], playing = false, animating = false;
let eventTimer = null;   // the poll's interval id while it is running, else null

function hot(sel, cls, ms){
  document.querySelectorAll(sel).forEach(el => {   // every diagram copy lights up
    el.classList.add(cls);
    setTimeout(()=>el.classList.remove(cls), ms);
  });
}
function animateStage(ev){
  if (GRAPH_KINDS.has(ev.type)) return animateGraphStage(ev);   // graph.js owns these
  const spec = STAGE[ev.type];
  if (!spec || !document.querySelector(".arch")) return;
  document.querySelectorAll(".arch-status").forEach(st => st.innerHTML = `<span class="live-dot"></span>${spec.label}`);
  spec.nodes.forEach(n => hot(`[data-node="${n}"]`, "hot", 1000));
  spec.edges.forEach(e => hot(`[data-edge="${e}"]`, "live", 1000));
  if (ev.type==="gate" && ev.decision==="retrieve"){
    ["procedural","semantic","episodic"].forEach(n => hot(`[data-node="${n}"]`,"hot",1000));
    ["e-gate-proc","e-gate-sem","e-gate-epi"].forEach(e => hot(`[data-edge="${e}"]`,"live",1000));
  }
}
function playNext(){
  if (!evQueue.length){ playing=false; animating=false;
    document.querySelectorAll(".arch-status").forEach(st => st.innerHTML=""); return; }
  playing = true; animating = true;
  animateStage(evQueue.shift());
  setTimeout(playNext, 620);   // stagger so stages light up in sequence
}
async function pollEvents(){
  try{
    const r = await (await fetch("/api/events" + (evCursor==null?"":"?cursor="+evCursor))).json();
    if (evCursor != null && r.events.length){
      evQueue.push(...r.events);
      if (!playing) playNext();
    }
    evCursor = r.cursor;
  } catch(e){ /* server busy */ }
}
// Start or stop the poll, called by render() with the answer to "is a diagram
// on screen". These events exist to light one up and nothing else reads them,
// so polling from a page that has no diagram was 2.2 requests a second that
// could not paint anything — and the conversation is where you spend most of
// your time, so that was most of the poll's whole cost. Only 总览 and 图工作流
// draw a chart (both carry the `.arch` class), so they keep the fast poll and
// every other page now costs nothing at all.
function setEventPolling(on){
  if (on){
    if (eventTimer != null) return;   // already polling — every 5s render() lands here
    // Resume from NOW, never from where we left off: that cursor points at
    // every turn that happened while you were on another page, and replaying
    // them would flash the diagram once per turn you did not come to watch.
    // A null cursor is the existing "start fresh" signal — the server answers
    // it with the current tail and no events (see events_since).
    evCursor = null;
    pollEvents();
    eventTimer = setInterval(pollEvents, 450);
    return;
  }
  if (eventTimer == null) return;
  clearInterval(eventTimer);
  eventTimer = null;
}

// knowme web — graph workflows: the topology chart + its live animation.
// Split out: classic <script>, shared global scope. Load order: static/README.md.
//
// The chart is DATA-DRIVEN: it renders Graph.describe() served in /api/data
// (d.graph.workflows), so the picture is provably the topology the engine
// runs — the anti-drift lesson learned from archSVG's byte-freeze. Never
// hand-edit a workflow's shape here; change the workflow and this follows.
// Ids are namespaced "g-" so they can never collide with archSVG's ids.

// --- layered layout: START at the left, each node one column after its
// furthest predecessor. Small graphs only (triage is 5 nodes) — no library.
//
// A CYCLE CANNOT BE LAYERED, so the back edges are found first and left out of
// the relaxation. deep_research loops (research → research) and the relaxation
// below is `layer[dst] = max(layer[dst], layer[src] + 1)`: fed its own output,
// the node gains a column on every pass and ends up off the canvas entirely —
// the whole chart slides right and the loop is never seen. A back edge is an
// edge into a node still on the DFS stack, which includes a node pointing at
// ITSELF; graphSVG draws those as an arc instead.
//
// For a DAG (triage, gather) nothing is ever a back edge, so every number this
// returns is the one it returned before — the regression lock asserts the two
// existing charts' SVG is byte-identical.
function graphLayout(wf){
  const names = ["START", ...wf.nodes.map(n => n.name), "END"];
  const out = {}; names.forEach(n => out[n] = []);
  wf.edges.forEach(e => { if (out[e.src]) out[e.src].push(e.dst); });
  const back = new Set(), mark = {};             // 1 = on the stack, 2 = done
  const peel = n => {
    mark[n] = 1;
    out[n].forEach(d => {
      if (mark[d] === 1) back.add(n + "|" + d);   // into a node we are inside of
      else if (!mark[d]) peel(d);
    });
    mark[n] = 2;
  };
  names.forEach(n => { if (!mark[n]) peel(n); });
  const layer = {START: 0};
  for (let pass = 0; pass < names.length; pass++)     // relax until stable
    wf.edges.forEach(e => {
      if (back.has(e.src + "|" + e.dst)) return;
      const src = layer[e.src] ?? 0;
      layer[e.dst] = Math.max(layer[e.dst] ?? 0, src + 1);
    });
  const cols = [];
  names.forEach(n => {
    if (layer[n] == null) layer[n] = 0;
    (cols[layer[n]] = cols[layer[n]] || []).push(n);
  });
  return {layer, cols: cols.filter(c => c && c.length), back};
}

function graphSVG(wf, opts = {}){
  const {cols, back} = graphLayout(wf);
  const kinds = Object.fromEntries(wf.nodes.map(n => [n.name, n.kind]));
  // W 和 GX 可以按图改（深度研究那张 6 列的图要用），H/GY/PAD 不动。
  //
  // 为什么只调得动这两个：这张图是 SVG，viewBox 里的东西**一起缩放**。塞进
  // 半栏里被压到 54% 时，把方框画到 200 宽只会让它更宽、然后被缩得更狠 ——
  // 屏幕上一样大。真正管用的只有整张图的固有宽度：固有宽度 ≤ 容器宽度，
  // 才是 1:1 显示，里面 14px 的字才真的是 14px。
  //
  // 默认值就是 triage/gather 被冻结时的那组数，一个字都别动（有字节锁）。
  const W = opts.w ?? 168, H = 52, GX = opts.gx ?? 74, GY = 22, PAD = 14;
  // A loop is drawn ABOVE its box (see edgeLine), so a chart that has one has
  // to reserve that much headroom or the arc pokes out of the viewBox and gets
  // clipped. A chart with no back edge reserves nothing — top === PAD and every
  // number below is the one it was before, which is what keeps the existing
  // charts byte-identical.
  const LOOP = 30;
  const top = PAD + (back.size ? LOOP + 12 : 0);
  const height = Math.max(...cols.map(c => c.length)) * (H + GY) - GY + PAD + top;
  const width = cols.length * (W + GX) - GX + PAD * 2;
  const pos = {};
  cols.forEach((col, ci) => col.forEach((n, ri) => {
    const colH = col.length * (H + GY) - GY;
    pos[n] = {x: PAD + ci * (W + GX), y: top + (height - top - PAD - colH) / 2 + ri * (H + GY)};
  }));
  // Ids carry the workflow name. START and END exist in EVERY workflow, so on a
  // page showing two charts an un-namespaced `g-START` lit both of them at once
  // — and hot() deliberately hits every match, so the bug looked like a feature.
  const nid = n => `g-${wf.name}-${n}`;
  // Both labels used to overclaim. "local read" was true for triage's calendar
  // peek and false for gather's scan_github (a subprocess) and scan_web (the
  // network) — what tool nodes share is that NO MODEL RUNS. And "small model"
  // was true of triage's classify and false of gather's synthesize, which uses
  // the main one; the kind alone does not know which, so do not claim.
  const SUB = {llm: "一次模型调用", agent: "核心循环作为节点", tool: "代码执行，不用模型", fn: ""};
  const NODE_ZH = {START:"开始", END:"结束", classify:"分类", calendar:"今日日历",
    route:"路由", quick_reply:"快速回复", full_agent:"完整智能体",
    scan_github:"扫描 GitHub", scan_web:"扫描网页", scan_calendar:"扫描日历",
    scan_memory:"扫描记忆", synthesize:"综合整理",
    plan:"拆解子问题", research:"研究一轮", save:"存档"};
  const nodeBox = n => {
    const p = pos[n];
    if (n === "START" || n === "END")
      return `<g class="node" data-node="${nid(n)}">
        <rect class="bx" x="${p.x + W/2 - 34}" y="${p.y + H/2 - 15}" width="68" height="30" rx="15"/>
        <text class="nt" x="${p.x + W/2}" y="${p.y + H/2 + 5}" text-anchor="middle" style="font-size:12px">${NODE_ZH[n] || n}</text></g>`;
    const sub = SUB[kinds[n]] || "";
    return `<g class="node" data-node="${nid(n)}">
      <rect class="bx" x="${p.x}" y="${p.y}" width="${W}" height="${H}" rx="9"/>
      <text class="nt" x="${p.x + 12}" y="${p.y + 22}">${esc(NODE_ZH[n] || n)}</text>
      ${sub ? `<text class="ns" x="${p.x + 12}" y="${p.y + 39}">${sub}</text>` : ""}</g>`;
  };
  const edgeLine = e => {
    const a = pos[e.src], b = pos[e.dst];
    const cls = `flow${e.conditional ? " dash" : ""}`;
    const id = `g-${wf.name}-${e.src}-${e.dst}`;
    // A back edge cannot use the S-curve below: src and dst are the SAME box,
    // so x1 and x2 are computed from one `pos` entry, the curve has zero width,
    // and it hides behind the node it is supposed to be coming back to. A loop
    // has to leave one side and arrive at another — out of the right edge, over
    // the top, down into the top edge.
    if (back.has(e.src + "|" + e.dst)){
      const rx = a.x + W, ry = a.y + H/2, tx = a.x + W/2;
      return `<path class="${cls}" data-edge="${id}"
        d="M${rx} ${ry} C${rx + 46} ${ry} ${rx + 46} ${a.y - LOOP} ${tx + 32} ${a.y - LOOP} C${tx} ${a.y - LOOP} ${tx} ${a.y - 22} ${tx} ${a.y}" marker-end="url(#garr)"/>`;
    }
    const x1 = a.x + (e.src === "START" ? W/2 + 34 : W), y1 = a.y + H/2;
    const x2 = b.x + (e.dst === "END" ? W/2 - 34 : 0), y2 = b.y + H/2;
    const mx = (x1 + x2) / 2;
    return `<path class="${cls}" data-edge="${id}"
      d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}" marker-end="url(#garr)"/>`;
  };
  return `<div style="overflow-x:auto"><svg viewBox="0 0 ${width} ${height}" class="arch graphchart"
      style="max-width:${width}px" role="img">
    <defs><marker id="garr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
      orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" class="head"/></marker></defs>
    ${wf.edges.map(edgeLine).join("")}
    ${["START", ...wf.nodes.map(n => n.name), "END"].map(nodeBox).join("")}
  </svg></div>`;
}

// Which workflow is running RIGHT NOW, set by graph_start and cleared by
// graph_end. The Overview panel used to read the last COMPLETED run, so during
// a gather it still showed triage — and since animateGraphStage only lights a
// chart that is on screen, nothing lit up either. One wrong chart caused both
// bugs: you saw the old shape, and you saw it stay dark.
let GRAPH_LIVE = null;
// The workflow to keep showing once a run ENDS. Without it the panel fell back
// to d.graph.runs[0] the instant graph_end fired — and that payload is only
// refreshed by the /api/data poll, so for one beat it still named the PREVIOUS
// run. Observed as: swap to gather, flick back to triage, then back to gather
// when the poll caught up. Remembering locally means the panel never shows a
// workflow older than the one it just watched.
let GRAPH_SHOWN = null;

// --- the compact Overview panel: the harness auto-decides, this reflects it.
function graphPanel(d){
  const g = d.graph || {enabled: false, workflows: [], runs: [], stats: {quick: 0, full: 0}};
  // Overview is a STATUS surface — "what just happened" — while the Graph tab
  // is a reference one: "what shapes exist". So this shows the workflow that
  // most recently RAN, from the trace. Pinning it to workflows[0] meant Overview
  // showed triage forever, seconds after a gather, which is why the panel read
  // as leftovers rather than as news.
  // A run in flight wins over the last finished one — during a gather you want
  // to watch the gather, not read about the triage that came before it.
  const showing = GRAPH_LIVE || GRAPH_SHOWN || ((g.runs || [])[0] || {}).workflow;
  const last = (g.runs || [])[0];
  const wf = (g.workflows || []).find(w => w && w.name === showing)
             || (g.workflows || [])[0];
  const tot = g.stats.quick + g.stats.full;
  const seg = (cls, n, label, pct) =>
    `<div class="${cls}" style="width:${pct}%">${pct >= 14 ? `${n} ${label}` : ""}</div>`;
  const split = !tot
    ? `<div class="meta" style="margin:6px 0 10px">还没有图工作流对话——开启后，每条消息都会在这里分流</div>`
    : `<div class="splitbar">
        ${seg("seg-skip", g.stats.quick, "次快速", Math.round(g.stats.quick / tot * 100))}
        ${seg("seg-ret", g.stats.full, "次完整", 100 - Math.round(g.stats.quick / tot * 100))}
      </div><div class="meta" style="margin:6px 0 10px">${g.stats.quick} 次仅由小模型回答——核心循环没有启动</div>`;
  // The flag gates TRIAGE — the per-message door — and nothing else. `knowme
  // gather` is a routine you start yourself and runs regardless, so the old
  // copy ("off = every turn runs the classic loop") was quietly false the
  // moment a second workflow existed.
  if (!g.enabled && !last)
    return `<div class="card"><div class="meta">逐条消息进入图工作流的入口目前<b>已关闭</b>——每轮聊天都会运行上方经典循环。
      在<a class="reveal" onclick="location.hash='settings'">行为</a>中开启<b>图工作流</b>，即可先对每条消息分流。
      你主动运行的工作流（如 <code>make gather</code>）不需要该开关——
      <a class="reveal" onclick="location.hash='graph'">在这里查看</a>。</div></div>`;
  const when = GRAPH_LIVE
    ? `<span class="live-dot"></span><b>${esc(GRAPH_LIVE)}</b> 正在运行`
    : last
    ? `上次运行：<b>${esc(last.workflow || "")}</b>${last.ms ? ` · ${(last.ms/1000).toFixed(1)} 秒` : ""}${
        last.steps ? ` · ${last.steps} 个节点` : ""}`
    : "实时——对话流经节点时会依次亮起";
  return `<div class="card" style="cursor:pointer" onclick="location.hash='graph'">
    ${g.enabled ? split : ""}${wf ? graphSVG(wf) : ""}
    <div class="meta" style="margin-top:8px">${when} · 点击查看完整过程</div></div>`;
}

// --- live animation: same machinery as the loop's STAGE map. hot() lights
// every copy on the page, so the Overview panel and the Graph tab glow together.
const GRAPH_KINDS = new Set(["graph_start", "node_start", "node_end", "route", "graph_end"]);
function animateGraphStage(ev){
  if (!document.querySelector(".graphchart")) return;
  const status = t => document.querySelectorAll(".arch-status").forEach(
    st => st.innerHTML = `<span class="live-dot"></span>${t}`);
  // Every graph event carries `workflow`, so the ids can be scoped to the chart
  // that is actually running instead of lighting every chart on the page.
  const w = ev.workflow || "";
  if (ev.type === "graph_start"){
    // Swap the Overview chart to this workflow before anything runs, so the
    // nodes about to light up are the ones on screen.
    graphLive(w);
    status(`${w} 开始`);
    hot(`[data-node="g-${w}-START"]`, "hot", 1000);
  }
  else if (ev.type === "node_start"){
    status(`${w} · ${ev.node}`);
    // Held, not pulsed: a node is lit for as long as it is WORKING. Pulsing on
    // node_end only ever showed you what had already finished, which is the
    // opposite of watching it happen — and with four nodes in one wave it is
    // the difference between seeing a fan-out and seeing four blinks.
    document.querySelectorAll(`[data-node="g-${w}-${ev.node}"]`)
      .forEach(el => el.classList.add("hot"));
  }
  else if (ev.type === "node_end"){
    document.querySelectorAll(`[data-node="g-${w}-${ev.node}"]`)
      .forEach(el => el.classList.remove("hot"));
    hot(`[data-node="g-${w}-${ev.node}"]`, "done", 900);
  }
  else if (ev.type === "route"){
    status(`路由 → ${ev.target}`);
    // The EDGE lights, the target node does not. node_start owns a node's light
    // and holds it for as long as the node is working; lighting it here too does
    // nothing node_start is not about to do, and this timer then takes the class
    // away again 1400ms later — so the node went dark a second into a two-minute
    // run. deep_research's self-loop made it obvious: research → research relit
    // the node that had just finished and unlit the round that had just begun.
    hot(`[data-edge="g-${w}-${ev.router}-${ev.target}"]`, "live", 1400);
  }
  else if (ev.type === "graph_end"){
    hot(`[data-node="g-${w}-END"]`, "hot", 1000);
    graphLive(null);
  }
}

// Setting the live workflow re-renders, so the panel swaps the moment a run
// starts rather than at the next poll.
function graphLive(name){
  if (GRAPH_LIVE === name) return;
  if (name) GRAPH_SHOWN = name;   // sticky: outlives the run, so no flick-back
  GRAPH_LIVE = name;
  if (typeof render === "function") render();
}

// ---------------------------------------------------------------------------
// THE RUNNER — N nodes as a row of live cards.
//
// The topology chart above shows the SHAPE: "these four are independent". It
// cannot show that they actually ran together, because a picture has no time
// axis and a viewer cannot tell four boxes lit at once from four lit very fast
// in sequence. So the cards carry the time: they start together, tick while
// running, and finish out of order. Watching three settle while one spins is
// the proof the chart can only promise.
//
// Same shape as the Arena, deliberately — arena.py tags every event with `spec`
// and routes it to a card; the graph engine already tags every event with
// `node`. Swap the key, reuse .cmp-grid/.cmp-col, and it reads as a sibling
// because it is one.
let graphRun = {running: false, workflow: "", nodes: {}, order: [], waves: [],
                visit: {}, digest: "", draft: "", noteId: "", error: "", ticker: null};

function graphResetRun(workflow){
  graphRun = {running: true, workflow, nodes: {}, order: [], waves: [], visit: {},
              digest: "", draft: "", noteId: "", error: "", ticker: graphRun.ticker};
}

// A node that runs more than once needs one card PER RUN. deep_research goes
// round the loop three times, and keying the cards by node name alone meant all
// three cards were the same card: the third round's state overwrote the first's,
// so the trace showed three identical rows all reporting the last round's time
// while a round was still running. The key is therefore `name#visit`.
//
// node_start carries `visit`; node_end does NOT (test_graph_stream.py pins its
// key set to exactly workflow/node/ms/keys/error, and that is the right call —
// a card wants the name and the cost, not the payload), so the current visit is
// remembered here from the last node_start. visit 1 keeps the PLAIN NAME as its
// key, which is what makes this invisible for triage and gather: every one of
// their visits is 1, so every key and every rendered pixel is what it was.
function graphKey(name, visit){
  return (visit || 1) > 1 ? `${name}#${visit}` : name;
}

function graphApplyEvent(ev){
  const R = graphRun;
  const k = ev.kind;
  if (k === "graph_start"){
    R.order = ev.nodes || [];
    R.order.forEach(n => R.nodes[n] = {status: "waiting"});
  } else if (k === "node_start"){
    const key = graphKey(ev.node, ev.visit);
    R.visit[ev.node] = ev.visit || 1;
    // A wave is "the nodes that started before any of them finished". That is
    // exactly what the engine means by a wave, and it is what the row groups by.
    // A re-entry starts after every earlier member finished, so it opens a wave
    // of its own — the loop reads top to bottom, one row per round.
    const open = R.waves[R.waves.length - 1];
    if (open && !open.closed) open.nodes.push(key);
    else R.waves.push({nodes: [key], closed: false});
    R.nodes[key] = {status: "running", name: ev.node, startedAt: performance.now()};
  } else if (k === "node_end"){
    const w = R.waves[R.waves.length - 1];
    if (w) w.closed = true;   // first finish closes the wave for new members
    R.nodes[graphKey(ev.node, R.visit[ev.node])] =
      {status: ev.error ? "error" : "done", name: ev.node, ms: ev.ms,
       keys: ev.keys || [], error: ev.error || ""};
  } else if (k === "route"){
    R.route = {target: ev.target, reason: ev.reason};
  } else if (k === "graph_end"){
    R.running = false; R.totalMs = ev.ms;
  } else if (k === "done"){
    R.running = false;
    R.digest = ev.digest || ""; R.draft = ev.draft_path || ""; R.error = ev.error || "";
    R.noteId = ev.note_id || "";
  }
}

// `message` is the input a workflow takes (`/deep_research <topic>`); a workflow
// that declares no such parameter ignores it — the server decides, by looking at
// the runner's signature, so this stays one code path for every workflow.
// `onFrame` is who repaints: the Graph tab redraws the whole view, the research
// page repaints only its own panel (and must, or the topic being typed into its
// input would be wiped every 100ms).
async function runGraph(workflow, message = "", onFrame = null){
  if (graphRun.running) return;
  const repaint = onFrame || render;
  graphResetRun(workflow);
  // Without a ticker the elapsed numbers freeze and the cards look identical to
  // a sequential run — the one thing this view exists to disprove.
  clearInterval(graphRun.ticker);
  graphRun.ticker = setInterval(() => { if (graphRun.running) repaint(); }, 100);
  repaint();
  try {
    const res = await fetch("/api/graph/stream", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({workflow, message}),
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const p of parts){
        const line = p.trim();
        if (!line.startsWith("data:")) continue;
        try { graphApplyEvent(JSON.parse(line.slice(5))); } catch (e) { /* partial frame */ }
        repaint();
      }
    }
  } catch (e){
    graphRun.error = String(e);
  } finally {
    graphRun.running = false;
    clearInterval(graphRun.ticker);
    repaint();
  }
}

// `key` is a card's identity (`name`, or `name#visit`); `wave` is the wave that
// card belongs to, passed in rather than searched for. Looking it up by name —
// `waves.find(w => w.nodes.includes(name))` — always found the FIRST wave the
// name appeared in, so once a node could run twice the second round was timed
// against the first round's peers.
function graphCol(key, wave){
  const n = graphRun.nodes[key] || {status: "waiting"};
  const name = n.name || key;
  // Only a repeated node needs its round number on the card; triage and gather
  // never produce a key with one, so their headings are unchanged.
  const label = key.includes("#") ? `${esc(name)} <span class="chip">第 ${key.split("#")[1]} 轮</span>`
                                  : esc(name);
  if (n.status === "waiting")
    return `<div class="cmp-col" style="opacity:.5"><div class="cmp-h"><b>${label}</b></div>
      <div class="meta">排队中</div></div>`;
  if (n.status === "running"){
    const el = ((performance.now() - n.startedAt) / 1000).toFixed(1);
    return `<div class="cmp-col"><div class="cmp-h"><b>${label}</b></div>
      <div class="meta"><span class="live-dot"></span>${el}s</div></div>`;
  }
  if (n.status === "error")
    return `<div class="cmp-col err"><div class="cmp-h"><b>${label}</b></div>
      <div class="meta" style="color:var(--bad)">${esc(n.error)}</div></div>`;
  // The bar is scaled to the SLOWEST node in this node's wave, and every faster
  // node prints what it spent waiting at the barrier. That number is the honest
  // cost of wave execution — printing it teaches more than hiding it would.
  const peers = (wave ? wave.nodes : [key]).map(x => (graphRun.nodes[x] || {}).ms || 0);
  const slowest = Math.max(...peers, 1);
  const pct = Math.round((n.ms || 0) / slowest * 100);
  const waited = slowest - (n.ms || 0);
  return `<div class="cmp-col"><div class="cmp-h"><b>${label}</b>
      <span class="chip">${n.ms}ms</span></div>
    <div class="wavebar"><i style="width:${pct}%"></i></div>
    <div class="meta">${waited > 20 && peers.length > 1
      ? `在同步屏障等待了 ${(waited/1000).toFixed(1)} 秒`
      : (peers.length > 1 ? "决定了本波次的速度" : "")}</div>
    <div class="meta">${(n.keys || []).map(k => `<span class="chip">${esc(k)}</span>`).join(" ")}</div>
  </div>`;
}

// One wave's row of cards. Split out because two pages draw it — the Graph tab's
// runner and the research page — and two copies of a bar chart's arithmetic drift.
// `w.nodes` holds card KEYS (see graphKey), not node names: a wave holds the
// cards that started together, and one node can contribute several.
function graphWaveRow(w, i){
  const R = graphRun;
  const done = w.nodes.map(k => R.nodes[k] || {}).filter(n => n.ms != null);
  const slowest = done.length ? Math.max(...done.map(n => n.ms)) : 0;
  const sum = done.reduce((a, n) => a + n.ms, 0);
  return `<div class="meta" style="margin:14px 0 6px">波次 ${i + 1} · ${w.nodes.length} 个节点${
    slowest ? ` · ${(slowest/1000).toFixed(1)} 秒`
      + (w.nodes.length > 1 ? `（串行执行需要 ${(sum/1000).toFixed(1)} 秒）` : "") : ""}</div>
    <div class="cmp-grid">${w.nodes.map(k => graphCol(k, w)).join("")}</div>`;
}

// Every wave, for a page that draws the whole run at once.
function graphWaves(){
  return graphRun.waves.map((w, i) => graphWaveRow(w, i)).join("");
}

function graphRunPanel(){
  const R = graphRun;
  const btn = `<button class="btn" onclick="runGraph('gather')" ${R.running ? "disabled" : ""}>
    ${R.running ? "运行中…" : "运行 gather"}</button>`;
  let h = `<h2>运行并观察波次 <span class="meta" style="font-weight:400">
    图表展示结构，这些卡片展示实际执行过程</span></h2>
    <div class="card">${btn}
    <span class="meta" style="margin-left:10px">同时获取 GitHub、网页、日历和记忆。
    只生成建议，不直接执行；摘要会写入发件箱。</span>`;
  if (R.error) h += `<div class="meta" style="color:var(--bad);margin-top:10px">${esc(R.error)}</div>`;
  R.waves.forEach((w, i) => { h += graphWaveRow(w, i); });
  if (R.totalMs) h += `<div class="meta" style="margin-top:12px">完成耗时
    ${(R.totalMs/1000).toFixed(1)} 秒${R.draft ? ` · 已保存到 <code>${esc(R.draft)}</code>` : ""}</div>`;
  if (R.digest) h += `<div class="card" style="margin-top:10px">${renderMarkdown(R.digest)}</div>`;
  return h + `</div>`;
}

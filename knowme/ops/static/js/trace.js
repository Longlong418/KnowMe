// knowme web — the turn timeline: what the agent did, in order.
//
// Pure rendering, no state of its own. A step is {kind, label, ms, detail,
// status} and comes from one of three places, all producing the SAME shape so
// one renderer covers them:
//
//   live turns      pushStep() below, called by applyStreamEvent (render.js)
//                   as SSE events arrive — the timeline grows with the turn
//   history rows     meta.steps, persisted by AgentRuntime (core/runtime.py)
//                   with the same _step() recorder — a reloaded thread renders
//                   exactly what the live one ended with
//   the Loop tab    stepsFromTurn() folds that payload's llm_calls/tools/gate
//                   into steps, because those rows are rebuilt server-side
//                   from trace files and carry no meta.steps of their own
//
// Classic <script>, shared global scope (no build step, no modules).
// Load order + rules: static/README.md.

// Chinese titles per kind; the label next to it stays data (a tool name, an
// iteration number), so the title/localization lives in exactly one map.
const STEP_TITLES = {
  gate: "门控", context: "上下文", route: "路由", graph: "图",
  node: "节点", llm: "推理", tool: "工具", consolidation: "记忆整理",
};

// 人话表 —— 和后端 core/runtime.py 的 _STOP_REASON_ZH / _GATE_ZH / _TARGET_ZH /
// _COMPACT_ZH 是同一套说法，**改文案要两边一起改**。
//
// 为什么会有两份：同一轮轨迹在两条路上各拼一遍。直播时是浏览器自己拼
// （render.js 收到一个 SSE 事件就记一步；服务端存好的那份要等这一轮跑完才到），
// 跑完之后用的是服务端存在 meta.steps 里的那份。所以同一句话必须两边都写。
// 表里有就用表里的，没有就原样输出 —— 供应商多出一个新 stop_reason 时，用户
// 该看到一个陌生的英文词，而不是一句「未知」。
const STOP_REASON_ZH = {
  end_turn: "直接回答", tool_use: "要调用工具",
  max_tokens: "到达输出上限，回复被截断", stop_sequence: "遇到停止词",
  refusal: "模型拒绝回答", pause_turn: "暂停，等待继续",
};
const GATE_ZH = {retrieve: "要查记忆", skip: "不用查记忆"};
// quick_reply 是事件的原文，quick 是 Loop 页从 trace 文件里重建出来的写法 ——
// 同一个「直接回答」在两条路上各有一个值。
const TARGET_ZH = {quick_reply: "直接回答", quick: "直接回答", full: "完整流程"};
const COMPACT_ZH = {
  tool_budget: "先说清工具结果在哪",
  micro_compact: "旧的工具结果收成一行指针",
  state_summary: "整段对话压成摘要",
};

const zh = (map, key) => map[key] || String(key == null ? "" : key);
const joinZh = parts => parts.filter(Boolean).join("；");

// 一步的原料 → 那一行 label。每个 kind 只认自己那几个字段，多的忽略；
// 认不出来的（未知 kind）退回 d.text，永远不会渲染出 "undefined"。
function stepLabel(kind, d){
  d = d || {};
  switch (kind){
    case "gate":    return zh(GATE_ZH, d.decision);
    case "context": return `历史 ${d.history || 0} 条 → 送出 ${d.sent || 0} 条`;
    case "route":   return `${d.workflow || "graph"} → ${zh(TARGET_ZH, d.target)}`;
    case "graph":   return `工作流 ${d.workflow || ""} · ${(d.path || []).join(" → ")}`;
    case "llm":     return `第 ${d.iteration} 轮 · ${zh(STOP_REASON_ZH, d.stop_reason)}`;
    case "consolidation": return `新增 ${d.new_facts || 0} 条记忆`;
    default:        return String(d.text || "");   // node=节点名, tool=工具名
  }
}

// 折在「详情」里的那一行。工具和节点的正文是数据本身（工具输出、写入的 key），
// 照原样放进去。
function stepDetail(kind, d){
  d = d || {};
  switch (kind){
    case "context": return joinZh([
      `附加上下文 ${d.chars || 0} 字`,
      ...(d.compaction || []).map(c => `压缩：${zh(COMPACT_ZH, c)}`)]);
    case "graph":   return joinZh([`共 ${d.steps} 步`, d.error ? `出错：${d.error}` : ""]);
    case "node":    return joinZh([...(d.keys || []).map(k => `写入了 ${k}`),
                                   d.error ? `出错：${d.error}` : ""]);
    case "llm":     return `输入 ${d.in == null ? "?" : d.in} tokens，`
                         + `输出 ${d.out == null ? "?" : d.out} tokens`;
    default:        return String(d.text || "");
  }
}

const stepMs = ms => ms == null ? "" :
  ms >= 1000 ? (ms / 1000).toFixed(1) + " 秒" : ms + "ms";

// One turn -> <ol class="steps">. Every step is a dot on a shared vertical
// line, a label, a right-aligned duration, and — when there is a detail — a
// NATIVE <details> expander. Native so that opening one needs no state of ours
// and survives anything that only PATCHES the log in place (the live card's
// stream events). A wholesale repaint does replace the node — syncChatLogs()
// does that on every poll — and what carries your open boxes across that is
// syncLogClass' openDetails/restoreDetails, not <details> itself. (It used to
// say the opposite here, and the poll quietly closed every 详情 几秒后自己合上.)
// Tool/node output defaults to collapsed; that is <details>' default.
//
// The <ol> carries .tele so the conversation's 统计 toggle hides the whole
// timeline together with the rest of the per-turn telemetry.
function turnTimeline(t){
  const steps = (t && t.steps) || [];
  if (!steps.length) return "";
  const items = steps.map(s => {
    const title = STEP_TITLES[s.kind] || s.kind;
    const detail = s.detail
      ? `<details class="step-body"><summary>详情</summary><pre>${esc(s.detail)}</pre></details>`
      : "";
    return `<li class="step">
      <div class="step-line">
        <span class="dot ${s.status || "ok"}"></span>
        <span class="step-label">${title}${s.label ? ` · ${esc(s.label)}` : ""}</span>
        <span class="step-ms">${esc(stepMs(s.ms))}</span>
      </div>
      ${detail}
    </li>`;
  }).join("");
  return `<ol class="steps tele">${items}</ol>`;
}

// The live path's recorder — the browser-side twin of runtime._step(). Kept in
// the same order/kind vocabulary, and capped the same way (stop appending, keep
// the first 40 — matching the backend exactly), so the timeline a turn grows
// live is the timeline it settles into when `done` brings the server's copy.
const MAX_LIVE_STEPS = 40;
function pushStep(target, kind, label, detail, ms, status){
  const steps = target.steps = target.steps || [];
  if (steps.length >= MAX_LIVE_STEPS) return;
  steps.push({
    kind, label: String(label || ""), ms: ms == null ? null : Math.max(0, ms | 0),
    detail: String(detail || "").slice(0, 400), status: status || "ok",
  });
}

// The Loop tab adapter. Those rows arrive as parallel arrays (llm_calls,
// tools, gate, …) rebuilt from the day's trace files; this folds them into the
// step sequence the events actually happened in: route → context → gate → the
// run itself → consolidation. 推理 and 工具 steps merge on their timestamps so
// the order is the one that ran (iteration N's tool precedes iteration N+1),
// with durations read off the same clocks where both ends parse.
function stepsFromTurn(t){
  const t0 = t.ts ? Date.parse(t.ts) : NaN;
  const at = ts => { const x = ts ? Date.parse(ts) : NaN; return isNaN(x) || isNaN(t0) ? null : x - t0; };
  const out = [];
  if (t.graph && t.graph.route)
    out.push({kind: "route",
              label: stepLabel("route", {workflow: t.graph.workflow || "triage",
                                         target: t.graph.route}),
              detail: t.graph.reason || "", ms: null});
  if (t.context)
    out.push({kind: "context",
              label: stepLabel("context", {history: t.context.history_messages,
                                           sent: t.context.sent_messages}),
              detail: stepDetail("context", {chars: t.context.application_chars,
                                             compaction: t.context.compaction}),
              ms: null});
  if (t.gate)
    out.push({kind: "gate", label: stepLabel("gate", t.gate),
              detail: t.gate.reason || "", ms: null});
  const run = [
    ...(t.llm_calls || []).map(c => ({ts: c.ts, s: {
      kind: "llm", label: stepLabel("llm", c), detail: stepDetail("llm", c.usage || {}),
      ms: at(c.ts)}})),
    ...(t.tools || []).map(x => ({ts: x.ts, s: {
      kind: "tool", label: x.tool, detail: x.output || "",
      status: x.status || "ok", ms: null}})),
  ].sort((a, b) => (Date.parse(a.ts || "") || 0) - (Date.parse(b.ts || "") || 0));
  out.push(...run.map(r => r.s));
  if (t.consolidation)
    out.push({kind: "consolidation",
              label: stepLabel("consolidation", t.consolidation), ms: null});
  return out;
}

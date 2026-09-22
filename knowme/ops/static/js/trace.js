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
    out.push({kind: "route", label: `${t.graph.workflow || "triage"} → ${t.graph.route}`,
              detail: t.graph.reason || "", ms: null});
  if (t.context)
    out.push({kind: "context",
              label: `${t.context.history_messages}→${t.context.sent_messages} msg`,
              detail: [`app=${t.context.application_chars} chars`,
                       ...(t.context.compaction || []).map(c => `compact ${c}`)].join(", "),
              ms: null});
  if (t.gate)
    out.push({kind: "gate", label: t.gate.decision || "", detail: t.gate.reason || "", ms: null});
  const run = [
    ...(t.llm_calls || []).map(c => ({ts: c.ts, s: {
      kind: "llm", label: `iter ${c.iteration} · ${c.stop_reason}`,
      detail: `tokens ${(c.usage || {}).in}→${(c.usage || {}).out}`, ms: at(c.ts)}})),
    ...(t.tools || []).map(x => ({ts: x.ts, s: {
      kind: "tool", label: x.tool, detail: x.output || "",
      status: x.status || "ok", ms: null}})),
  ].sort((a, b) => (Date.parse(a.ts || "") || 0) - (Date.parse(b.ts || "") || 0));
  out.push(...run.map(r => r.s));
  if (t.consolidation)
    out.push({kind: "consolidation", label: `+${t.consolidation.new_facts} facts`, ms: null});
  return out;
}

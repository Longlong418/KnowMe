// knowme web — 深度研究：给一个主题，它自己查几轮，再写一份带出处的报告。
//
// 一页两栏：左边是这张工作流的图 + 它这一趟**实际走过**的波次（一轮一张卡片，
// 图上同时亮着正在跑的那个），右边是报告。上面那一行只干一件事：把主题交给
// runGraph；下面那两个数字框是这一趟的预算（每个 sub-agent 几次模型往返、整趟
// 最多几轮），跟主题走同一条路 —— 一起 POST 给服务端。
//
// 卡片里的明细（plan 拆出的子问题、每个 sub-agent 那一行）不是这一页画的：它是
// graph.js 里 graphColDetail() 画的，数据来自工作流自己发的两条事件。理由见那边
// 的注释 —— 引擎的 node_end 只带节点返回值里的**键名**。
//
// 它跑的是 knowme/graph/workflows/deep_research.py，图是 /api/data 现算的
// describe() 输出 —— 这一页不画图，它只是把那张图放上去。所以"图和实际执行的
// 是同一件事"这件事不需要在这里维护。
//
// 这一页在 render() 里属于"跳过重建"的那种（跟阅读器、Coding 页一样）：5 秒
// 一次的轮询重建整个 #view，会把你正在打的主题、正在亮的图、已经显示出来的
// 报告一起抹掉，而这一页没有任何东西是轮询喂的 —— 卡片和报告都由 SSE 推着走。

let researchTopic = "";        // 输到一半的主题，切走再切回来不丢
let researchNoteId = "";       // 上一次研究落进知识库的那条笔记
let researchPainted = null;    // 已经画过的报告原文，免得每 100ms 重渲染一遍

const RESEARCH_WORKFLOW = "deep_research";

// 那个工作流的拓扑，从服务端现算的列表里找。找不到就返回 null（比如工作流被
// 改名了），页面照样能用，只是没有图。
function researchTopology(){
  const wfs = (D && D.graph && D.graph.workflows) || [];
  return wfs.find(w => w && w.name === RESEARCH_WORKFLOW) || null;
}

// 刷新页面之后 SSE 那条流早就没了，而 /api/data 里的图工作流 runs 只有元数据
// （没有正文）。真正留着全文的是知识库那条笔记 —— knowledge_info() 每次轮询都
// 带全文。所以记住 note_id，回来按 id 把正文捞回来。
function researchNote(){
  if (!researchNoteId) return null;
  const notes = ((D || {}).knowledge_info || {}).notes || [];
  return notes.find(n => n.id === researchNoteId) || null;
}

function researchReportText(){
  if (graphRun.digest) return graphRun.digest;   // 这一趟刚跑完的
  const note = researchNote();                   // 或者刷新回来的
  return (note && note.content) || "";
}

function researchReport(){
  const text = researchReportText();
  if (!text)
    return `<div class="card empty">还没有报告。<br>
      上面填一个主题、点「开始研究」——它会拆子问题、上网搜、把页面读进来，
      看这一轮有没有新东西，再决定要不要补一轮。</div>`;
  const note = researchNote();
  const where = [
    graphRun.draft ? `文件 <code>${esc(graphRun.draft)}</code>` : "",
    note ? `知识库《${esc(note.title)}》` : "",
  ].filter(Boolean).join("，还有 ");
  return `<div class="card">${renderMarkdown(text)}</div>
    ${where ? `<div class="meta" style="margin-top:8px">也存了一份：${where}
      ${note ? `· <a href="#knowledge">去知识库看</a>` : ""}</div>` : ""}`;
}

function researchCards(){
  if (!graphRun.waves.length)
    return `<div class="card empty">点「开始研究」之后，这里会一行一行长出它实际跑过的
      波次：一轮一张卡片，各自的耗时分开算。图上的方框同时会亮起来 —— 包括回头
      再跑一轮时那个自环。</div>`;
  const R = graphRun;
  const tail = R.running ? `<div class="meta" style="margin-top:12px">
      <span class="live-dot"></span>跑着呢，卡片会一张一张长出来…</div>`
    : (R.totalMs ? `<div class="meta" style="margin-top:12px">跑完一共
        ${(R.totalMs/1000).toFixed(1)} 秒 · ${R.waves.length} 个波次</div>` : "");
  const bad = R.error ? `<div class="meta" style="color:var(--bad);margin-top:8px">${esc(R.error)}</div>` : "";
  return `<div class="card">${graphWaves()}${tail}${bad}</div>`;
}

// 按钮和"正在运行"那个小灯。这一页是**跳过重建**的（见文件头），所以它们不会
// 因为 state 变了就自己更新 —— 跑起来的时候那个按钮必须自己变成灰的、字变成
// 「研究中…」，否则页面上没有任何东西告诉你它已经开始了。
function researchButton(){
  return graphRun.running ? "研究中…" : "开始研究";
}

function researchLive(){
  return graphRun.running
    ? `<span class="meta"><span class="live-dot"></span>正在运行 · 第 ${graphRun.waves.length + 1} 步</span>`
    : `<span class="meta">一轮一张卡片</span>`;
}

// 报告只在正文真的变了的时候重画。ticker 每 100ms 叫一次这一步，而这里有
// 一万字的 markdown —— 每次都重渲染是白烧 CPU。
//
// 比较的是**正文**而不是 digest：刷新回来时 digest 是空的，正文是从知识库那条
// 笔记读的，只认 digest 的话报告永远画不出来（空 == 空）。
function repaintResearch(){
  const cards = document.getElementById("research-cards");
  if (cards) cards.innerHTML = researchCards();
  const report = document.getElementById("research-report");
  const text = researchReportText();
  if (report && text !== researchPainted){
    researchPainted = text;
    report.innerHTML = researchReport();
  }
  const btn = document.getElementById("research-go");
  if (btn){ btn.disabled = graphRun.running; btn.textContent = researchButton(); }
  const live = document.getElementById("research-live");
  if (live) live.innerHTML = researchLive();
  researchRestoreHot();
}

// hot() 挂的是 DOM 上的 class，"切走再切回来"是一次重建 —— 新元素上没有这个
// class，正在跑的节点就变成黑的。所以重建之后照 graphRun 里谁是 running 把它
// 补回去。（node_end 会自己摘掉，不用管。）
function researchRestoreHot(){
  Object.entries(graphRun.nodes).forEach(([key, n]) => {
    if (n.status !== "running") return;
    document.querySelectorAll(`[data-node="g-${RESEARCH_WORKFLOW}-${n.name || key}"]`)
      .forEach(el => el.classList.add("hot"));
  });
}

// --------------------------------------------------------------- 输入与启动

function researchType(value){
  researchTopic = value;   // 只进模块状态；提交时才落 localStorage
}

// 两个旋钮：每个 sub-agent 最多几次模型往返、整趟最多几轮。默认值和范围都照着后端的
// 那一份抄（ops/deep_research.py 的 MAX_ITERATIONS / MAX_ROUNDS / BUDGET_LIMITS），
// 页面不自己发明数。这里夹一次是为了让人在框里打不出离谱的值；**服务端还会再夹一次** ——
// 它不该相信浏览器发来的任何东西，而 /api/graph/stream 是谁都能 POST 的。
const RESEARCH_BUDGET = {iterations: [2, 20], rounds: [1, 6]};
const RESEARCH_BUDGET_DEFAULT = {iterations: 8, rounds: 3};
// 值可能是数字（默认值）也可能是字符串（用户刚打完），所以一律走 researchBudgetOut() 解析。
let researchBudget = {...RESEARCH_BUDGET_DEFAULT};

// 发出去之前的那一次夹紧。把输入框清空是很自然的动作，那时 Number("") 是 0 而不是 NaN，
// 光判 isFinite 会把它当成"要 0 次往返" —— 所以空串直接回落到默认值。
function researchBudgetOut(){
  const pick = (name) => {
    const [low, high] = RESEARCH_BUDGET[name];
    const raw = String(researchBudget[name] ?? "").trim();
    const value = Math.round(Number(raw));
    return raw && Number.isFinite(value)
      ? Math.min(high, Math.max(low, value))
      : RESEARCH_BUDGET_DEFAULT[name];
  };
  return {iterations: pick("iterations"), rounds: pick("rounds")};
}

// 两个输入框共用这一个 handler，靠 id 分辨改的是哪一个。改完立刻重画下面那句说明 ——
// 那句话是照着预算写的（「最多 N 轮」），不重画就还停在旧数字上。这一页在 render() 里
// 是"跳过重建"的，所以没有别的时机能更新它。
//
// 故意**不**把夹紧后的值写回输入框：那会在人打到一半时改他打的字（想打 10，刚敲下 "1"
// 就被改成 2）。夹紧的结果由下面那句说明写出来，那才是"实际会跑成什么样"。
function researchSetBudget(input){
  researchBudget[input.id === "research-iter" ? "iterations" : "rounds"] = input.value;
  researchBudgetSave();
  const note = document.getElementById("research-budget-note");
  if (note) note.innerHTML = researchBudgetNote();
}

function researchBudgetNote(){
  const b = researchBudgetOut();
  return `最多 ${b.rounds} 轮：每一轮给每个子问题各派一个 sub-agent 去搜、去读，然后问
    "这一轮有没有找到新东西" —— 有就带着已掌握的内容再补一轮，没有就收工写报告。
    每个 sub-agent 最多 ${b.iterations} 次模型往返，用完了那一行会标成「没查完」，
    报告里也会点名说这是本轮没查完、不是没有答案。报告同时写进知识库和 <code>outbox/</code>。`;
}

function researchBudgetSave(){
  const b = researchBudgetOut();
  localStorage.setItem("knowme_research_iter", String(b.iterations));
  localStorage.setItem("knowme_research_rounds", String(b.rounds));
}

async function startResearch(){
  const el = document.getElementById("research-topic");
  const topic = String((el && el.value) || researchTopic || "").trim();
  if (!topic) return;
  researchTopic = topic;
  localStorage.setItem("knowme_research_topic", topic);
  // 这一趟的报告还没落库：清掉上一条笔记的 id，不然报告栏会先显示上一趟的。
  researchNoteId = "";
  researchPainted = null;
  // 预算跟着这一趟走，跟主题是同一条路：服务端只把它交给声明了 budget 形参的 runner，
  // 也就是只有深度研究会收到。
  await runGraph(RESEARCH_WORKFLOW, topic, repaintResearch, researchBudgetOut());
  if (graphRun.noteId){
    researchNoteId = graphRun.noteId;
    localStorage.setItem("knowme_research_note", graphRun.noteId);
  }
  repaintResearch();
}

// 上次研究的主题和报告：刷新页面回来接着看。localStorage 只存"你上次关心的是什么"，
// 报告正文永远从知识库读 —— 存两份正文迟早对不上。
//
// 跑在**打包的时候**（文件最后一行），不是渲染的时候：页面第一次画出来就得带着
// 上次的主题，而在 render() 里补的话第一次是空的，得等下一次重建才填上。
function researchRestore(){
  researchTopic = localStorage.getItem("knowme_research_topic") || "";
  researchNoteId = localStorage.getItem("knowme_research_note") || "";
  researchPainted = null;
  // 旋钮也要记住。存进去的一定是夹紧过的值（researchBudgetSave 走的是
  // researchBudgetOut），所以这里读回来直接用。
  researchBudget.iterations =
    localStorage.getItem("knowme_research_iter") || researchBudget.iterations;
  researchBudget.rounds =
    localStorage.getItem("knowme_research_rounds") || researchBudget.rounds;
}

// 这张图有 6 列。以前它和「每一轮」的卡片挤在 .dr-grid 的左半栏里，那一栏
// 只有 400 来像素，而 .arch 的 min-width 是 760px —— 整张图被压到 54%，框里的
// 14px 字渲染出来只有 7.6px，所以看着糊。
//
// 现在它单独占整幅宽，并且方框和列距收窄到刚好铺满整幅（1180px 上下）。这样
// 是 1:1 —— 字就是实打实的 14px，方框在屏幕上比原来大了一倍多。**注意不能靠
// 把方框画大来解决**：viewBox 里的东西一起缩放，画得越大缩得越狠，屏幕上
// 一样大，只有固有宽度这一个杠杆。
const DR_CHART = {w: 152, gx: 48};

function deepResearchView(){
  const wf = researchTopology();
  const budget = researchBudgetOut();
  return `<div class="card">
    <div class="ctc-head"><b>要研究什么</b>
      <span class="meta">给一个主题，不是一个问题 —— 它自己去查。</span></div>
    <div class="dr-row">
      <input id="research-topic" type="text" spellcheck="false"
             placeholder="例如：固态电池的产业化进度"
             value="${esc(researchTopic)}"
             oninput="researchType(this.value)"
             onkeydown="if (event.key === 'Enter') startResearch()">
      <button id="research-go" class="save" onclick="startResearch()"
              ${graphRun.running ? "disabled" : ""}>${researchButton()}</button>
    </div>
    <div class="dr-budget">
      <label class="fld-inline">每个子问题最多
        <input id="research-iter" type="number" min="2" max="20" value="${budget.iterations}"
               oninput="researchSetBudget(this)">次模型往返</label>
      <label class="fld-inline">最多
        <input id="research-rounds" type="number" min="1" max="6" value="${budget.rounds}"
               oninput="researchSetBudget(this)">轮</label>
      <span class="meta">实际用掉的数会写在每张卡片的子 agent 那一行上</span>
    </div>
    <div class="meta" id="research-budget-note">${researchBudgetNote()}</div>
  </div>
  <section class="dr-chart">
    <h2>它怎么走 <span id="research-live">${researchLive()}</span></h2>
    ${wf ? `<div class="card">${graphSVG(wf, DR_CHART)}</div>` : ""}
  </section>
  <div class="dr-grid">
    <section><h2>每一轮</h2><div id="research-cards">${researchCards()}</div></section>
    <section><h2>报告</h2><div id="research-report">${researchReport()}</div></section>
  </div>`;
}

// 注册进视图表。跟 chat.js / reader.js 一个做法：这个页面的渲染和它自己的交互
// （输入框、SSE、定向重画）必须待在同一个文件里，所以不塞进 views.js 那个大对象。
VIEWS.deepresearch = deepResearchView;

researchRestore();

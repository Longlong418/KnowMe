// knowme web — 深度研究：给一个主题，它自己查几轮，再写一份带出处的报告。
//
// 一页两栏：左边是这张工作流的图 + 它这一趟**实际走过**的波次（一轮一张卡片，
// 图上同时亮着正在跑的那个），右边是报告。中间那个输入框只干一件事：把主题
// 交给 runGraph。
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

async function startResearch(){
  const el = document.getElementById("research-topic");
  const topic = String((el && el.value) || researchTopic || "").trim();
  if (!topic) return;
  researchTopic = topic;
  localStorage.setItem("knowme_research_topic", topic);
  // 这一趟的报告还没落库：清掉上一条笔记的 id，不然报告栏会先显示上一趟的。
  researchNoteId = "";
  researchPainted = null;
  await runGraph(RESEARCH_WORKFLOW, topic, repaintResearch);
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
    <div class="meta">最多三轮：每轮搜索并把页面读进来，然后问"这一轮有没有找到新东西"，
      有就带着已掌握的内容再补一轮，没有就收工写报告。报告同时写进知识库和
      <code>outbox/</code>。</div>
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

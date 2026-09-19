// knowme dashboard — subtab/db helpers, SQL console, Memory/Tools sub-views, VIEWS.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

// --- sub-tabs: keep long pages short by splitting them into hash-routed tabs
// (#memory/semantic, #database/facts). Each tab is a plain link, so it's
// bookmarkable and the architecture cards can deep-link straight to one.
function subtabBar(view, tabs, active){
  return `<div class="subtabs">${tabs.map(([key,label,n]) =>
    `<a class="subtab ${key===active?"on":""}" href="#${view}/${key}">${esc(label)}${
      n!=null?`<span class="n">${n}</span>`:""}</a>`).join("")}</div>`;
}

// A raw SQLite table, scrollable, with the column names AS the (indigo) sticky
// headers so the schema lines up over its data instead of floating above it.
function dbTable(t){
  if (!t.sample.length) return `<div class="card empty">空表——尚无数据</div>`;
  const head = t.columns.map(c => `<th class="dbcol">${esc(c)}${
    t.types&&t.types[c]?`<small>${esc(t.types[c].toLowerCase())}</small>`:""}</th>`).join("");
  const body = t.sample.map(r => `<tr>${t.columns.map(c =>
    `<td class="dbcell">${esc(String(r[c]??"").slice(0,120))}</td>`).join("")}</tr>`).join("");
  return `<div class="scrolly"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="meta" style="margin-top:6px">显示 ${t.sample.length}/${t.count} 行（最新数据在前）</div>`;
}
const DB_DESC = {
  calendar_events: "create_event 工具写入的事件（旗舰任务）",
  facts: "语义记忆——持久事实（记忆 ▸ 语义）",
  episodes: "情景记忆——带日期的摘要（记忆 ▸ 情景）",
  chat_log: "每条消息都带有 session_id 标签——记忆整理从这里读取",
};
const QUERY_EXAMPLES = [
  "SELECT role, content FROM chat_log ORDER BY id DESC LIMIT 10",
  "SELECT subject, content FROM facts",
  "SELECT session_id, COUNT(*) FROM chat_log GROUP BY session_id",
];
function dbQueryView(){
  return `<div class="meta" style="margin-bottom:10px">用于查询 <code>state.db</code> 的只读 SQL 控制台
      （类似精简版 Supabase 编辑器）。这里只允许执行 <code>SELECT</code>；数据库以只读方式打开，
      因此这里的操作不会修改你的数据。</div>
    <textarea class="sqlbox" id="sqlbox" spellcheck="false" onfocus="markEditing()" oninput="markEditing()">${esc(QUERY_EXAMPLES[0])}</textarea>
    <div style="margin:8px 0"><button class="save" onclick="runQuery()">运行</button>
      <span class="meta" style="margin-left:12px">示例：${QUERY_EXAMPLES.map(q=>`<span class="qexample" onclick="qFill(this.textContent)">${esc(q)}</span>`).join(" &nbsp; ")}</span></div>
    <div id="qout"></div>`;
}

// --- read-only SQL console (item: "a simple query editor like Supabase")
function qFill(sql){ const b=document.getElementById("sqlbox"); if(b){ b.value=sql; runQuery(); } }
async function runQuery(){
  editing = true;   // keep the 5s refresh from wiping the query + results
  const sql = (document.getElementById("sqlbox")||{}).value || "";
  const out = document.getElementById("qout");
  out.innerHTML = `<div class="meta">正在运行…</div>`;
  const r = await postJSON("/api/query", {sql});
  if (r.error){ out.innerHTML = `<div class="card empty" style="color:var(--bad)">${esc(r.error)}</div>`; return; }
  if (!r.rows.length){ out.innerHTML = `<div class="card empty">0 行</div>`; return; }
  out.innerHTML = `<div class="scrolly"><table><thead><tr>${
    r.columns.map(c=>`<th class="dbcol">${esc(c)}</th>`).join("")}</tr></thead><tbody>${
    r.rows.map(row=>`<tr>${row.map(v=>`<td class="dbcell">${esc(String(v).slice(0,120))}</td>`).join("")}</tr>`).join("")
    }</tbody></table></div><div class="meta" style="margin-top:6px">${r.rows.length} 行</div>`;
}

// --- Memory sub-tabs. Memory is the friendly, per-pillar view of what persists;
// the Data tab shows the SAME rows as raw SQLite tables (see the explainer).
function memOverview(d){
  const s = d.stats;
  const pillars = [
    ["语义记忆","semantic",d.facts.length+" 条事实","关于你和相关人员的持久、提炼后的事实"],
    ["情景记忆","episodic",d.episodes.length+" 条情景","每次整理生成一条带日期的摘要——有意保持精简"],
    ["程序性记忆","skills",d.skills.length+" 个技能","仅在相关时加载的 SKILL.md——规定如何行动"],
  ].map(([t,sub,n,desc]) => `<div class="box" style="min-width:0" onclick="location.hash='memory/${sub}'">
      <b>${t} <span class="meta" style="font-weight:400">· ${n}</span></b><span>${desc}</span></div>`).join("");
  return `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>记忆与数据库——同一文件的两种视图。</b>
      <div class="r">此标签页按记忆支柱整理展示 KnowMe 记住的内容。<a class="reveal" onclick="location.hash='database'">数据库标签页</a>
      则以原始 SQLite 表（以及 FTS5 关键词索引）的形式展示完全相同的数据。底层都是
      <code>.knowme/state.db</code>，只是观察层级不同。
      <br><br>一些助手（如 Hermes）把记忆保存在单个 <code>MEMORY.md</code> 文件中。KnowMe 将可查询的数据源保存在
      <code>state.db</code>（事实 + 情景，支持 FTS5 搜索），同时在每轮对话后写入易读的
      ${reveal("MEMORY.md","MEMORY.md")} 镜像——既有可以直接打开的真实文件，也有可靠数据库作为后盾。</div></div>
    <h2>三类记忆支柱</h2>
    <div class="tiles" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">${pillars}</div>
    <h2>检索门控——这一轮真的需要记忆吗？</h2>${gateSplit(s)}
    <div class="meta" style="margin-top:8px">在进行任何查询前，一个低成本模型会判断这一轮<b>是否</b>需要记忆——
      这就是记忆<i>检索</i>中的关键决策。（运维标签页会把同一组跳过/检索数据作为运行指标展示；决策本身仍属于记忆系统。）</div>
    <div class="meta" style="margin-top:14px">文件：${reveal("state.db","state.db")} · ${reveal("MEMORY.md","MEMORY.md")} · ${reveal("SOUL.md","SOUL.md")} · ${reveal("skills","skills/")}</div>`;
}
function memSemantic(d){
  let h = `<div class="meta" style="margin-bottom:12px">从你告诉 KnowMe 的内容中提炼出的持久事实——
    这是最精简、复用率最高的存储。你可以编辑或遗忘任意事实；更改将在下一轮对话生效。</div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>主题</th><th>事实</th><th>来源</th><th></th></tr>${
    d.facts.map(f => `<tr id="fact-${f.id}">
      <td><code>${esc(f.subject)}</code></td>
      <td class="fc">${esc(f.content)}</td>
      <td class="meta">${esc({user:"用户",consolidation:"记忆整理"}[f.source] || f.source)}</td>
      <td style="white-space:nowrap"><a class="reveal" onclick="editFact(${f.id})">编辑</a> · <a class="reveal del" onclick="delMem('delete_fact',${f.id})">删除</a></td>
    </tr>`).join("")}</table></div>`;
  return h;
}
function memEpisodic(d){
  const src = d.episodes_source || "sqlite";
  let h = `<div class="meta" style="margin-bottom:8px">后端：<span class="srcpill">${esc(src)}</span></div>`;
  if (d.episodes_error) h += `<div class="card empty">无法从 Notion 读取情景记忆：${esc(d.episodes_error)}</div>`;
  h += `<div class="card" style="background:var(--accent-soft);border-color:var(--line2)">
    <b>为什么这里内容很少？</b> <span class="r">情景记忆只为每次整理保存一条<i>提炼后的</i>摘要，
    而不是保存每条消息。逐条原始对话位于数据库标签页中的
    <a class="reveal" onclick="location.hash='database/chat_log'"><code>chat_log</code> 表</a>（数据量较大的表）；
    情景记忆只是其中的精华。</span></div>`;
  h += `<div class="card" style="padding:4px 8px"><table><tr><th>日期</th><th>情景</th><th></th></tr>${
    d.episodes.map(e => `<tr><td class="meta">${esc(e.happened_at)}</td><td>${esc(e.summary)}</td>
      <td><a class="reveal del" onclick="delMem('delete_episode','${e.id}')">删除</a></td></tr>`).join("")}</table></div>`;
  return h;
}
function memSkills(d){
  let h = `<div class="meta" style="margin-bottom:12px">程序性记忆——只有消息匹配时才会加载的 Markdown 指令。
    有三种添加方式：在聊天中教给 KnowMe（它会调用 <code>create_skill</code>）、编辑下方技能，
    或将 <code>SKILL.md</code> 放入${reveal("skills","技能文件夹")}。</div>`;
  h += d.skills.map((sk,i) => {
    const full = `---
name: ${sk.name}
description: ${sk.description}
---

${sk.body}`;
    return `<div class="card">
      <div class="u"><code>${esc(sk.name)}</code> <span class="meta" style="font-weight:400">· ${esc(sk.description)}</span>
        <span class="srcpill ${sk.editable?"":"apple"}" style="margin-left:6px">${sk.editable?"个人":"内置"}</span></div>
      <textarea class="editor" id="sk-${i}" style="min-height:150px;margin-top:8px" data-path="${esc(sk.path)}"
        oninput="dirty('sksave-${i}')" onfocus="markEditing()">${esc(full)}</textarea>
      <div style="margin-top:8px"><button class="save" id="sksave-${i}" disabled onclick="saveSkill(${i})">保存 SKILL.md</button>
        <span class="meta" id="skmsg-${i}" style="margin-left:10px">${esc(sk.rel)}</span></div></div>`;
  }).join("") || `<div class="card empty">尚未加载技能</div>`;
  return h;
}
function memSoul(d){
  return `<div class="meta" style="margin-bottom:12px">SOUL.md 是 KnowMe 的人格设定——每轮对话都会加载的系统提示词。
    编辑它会改变你的 KnowMe。更改将在下一轮对话生效。</div>
    <div class="card"><textarea id="soul" class="editor" style="min-height:260px"
      oninput="dirty('soul-save')" onfocus="markEditing()">${esc(d.soul||"")}</textarea>
    <div style="margin-top:8px"><button class="save" id="soul-save" disabled onclick="saveSoul()">保存 SOUL.md</button>
      <span class="meta" id="soul-msg" style="margin-left:10px"></span></div></div>
    <div class="meta" style="margin-top:10px">${reveal("SOUL.md","在编辑器中打开 SOUL.md")}</div>`;
}
function memConsolidation(d){
  const distilled = d.facts.filter(f => f.source==="consolidation");
  let h = `<div class="card"><b>工作原理。</b> <span class="r">每经过 ${d.consolidate_every} 轮对话，
    一个低成本模型就会读取尚未整理的 ${"<code>chat_log</code>"}，将其提炼为持久的
    <b>事实</b>（语义记忆）和一条<b>情景</b>（情景记忆）。批量处理可以降低成本，
    同时为摘要模型提供足够上下文，以判断哪些内容值得保留。</span></div>`;
  h += `<div class="tiles" style="margin-top:12px">
    <div class="tile"><b>${d.chat_pending}</b><span>排队中的消息</span></div>
    <div class="tile"><b>${d.consolidate_every*2}</b><span>触发阈值</span></div>
    <div class="tile"><b>${distilled.length}</b><span>整理得到的事实</span></div>
    <div class="tile"><b>${d.episodes.length}</b><span>情景总数</span></div></div>`;
  h += `<h2>提炼出的事实</h2>`;
  h += table(["主题","事实","时间"], distilled.map(f =>
    `<tr><td><code>${esc(f.subject)}</code></td><td>${esc(f.content)}</td><td class="meta">${esc((f.created_at||"").slice(0,10))}</td></tr>`));
  h += `<div class="meta" style="margin-top:10px">这是一项记忆操作，因此在这里展示。每次运行也会在运维中被
    <a class="reveal" onclick="location.hash='ops'">追踪</a>，并可由模型评判测试评分。</div>`;
  return h;
}

const TOOL_DESC_ZH = {
  create_event: "在用户的本地日历中创建事件；适用于安排、预订或规划具体时间的事项。",
  list_events: "读取所有已连接来源中的日历事件，包括 Google 日历和 KnowMe 本地日历。",
  search_web: "搜索公开网页并返回主要结果的标题、摘要和链接。",
  save_note: "把值得长期保留的事实、人物或项目偏好写入长期记忆。",
  send_message: "起草消息并放入本地发件箱，供用户检查后发送。",
  manage_memory: "搜索、更正或删除长期记忆中的事实与情景；修改前需要先查询对应 ID。",
  update_soul: "保存针对当前用户的持久行为规则与偏好，并从下一轮对话起生效。",
  create_skill: "创建可复用的 SKILL.md，让智能体在相关场景中重复用户教过的工作流。",
  github_read: "通过 gh CLI 以只读方式查看 GitHub 拉取请求和议题。",
  delegate_task: "把编程任务交给本机运行的专业编码智能体处理。",
  run_command: "在沙箱中运行命令并读取输出；该功能仍在规划中。",
  browse_web: "打开网页并读取或点击页面；该功能仍在规划中。",
  schedule_task: "让智能体自行安排周期性任务；该功能仍在规划中。",
};

// Tools ▸ Results: the artifacts tool calls produced (kept distinct from the
// tools themselves — the old tab conflated capability with output).
function toolsResults(d){
  let h = `<div class="meta" style="margin-bottom:10px">工具调用实际写入的内容。这里展示的是结果，而不是工具本身。</div>`;
  h += `<h2>日历事件 <span class="meta" style="font-weight:400">· 来自 create_event</span></h2>`;
  h += table(["事件","开始","结束","参与者"], d.calendar.map(e =>
    `<tr><td>${esc(e.title)}</td><td class="meta">${esc(e.start)}</td><td class="meta">${esc(e.end)}</td><td>${esc(e.attendees)}</td></tr>`));
  h += `<div class="meta" style="margin-bottom:16px">同时写入 <code>calendar.ics</code>——${reveal("calendar.ics","在文件管理器中显示 calendar.ics")}（双击可导入日历应用）</div>`;
  h += `<h2>发件箱——消息草稿 <span style="font-weight:400;text-transform:none;letter-spacing:0">· ${reveal("outbox","打开发件箱文件夹")}</span></h2>`;
  h += d.outbox.length ? d.outbox.map(o=>`<div class="card"><span class="u">${esc(o.name)}</span><div class="r">${esc(o.text)}</div></div>`).join("")
                       : `<div class="card empty">没有消息草稿</div>`;
  return h;
}
// Tools ▸ MCP: external connectors. Shows live status + a copy-paste config so
// anyone can plug in their own server (scalable, not a one-off).
function toolsMCP(t){
  const m = t.mcp;
  let h = `<div class="card ${m.configured?"":""}" style="border-color:${m.live?"var(--good)":"var(--line2)"}">
    <b>模型上下文协议（MCP）${m.live?"——已连接":m.configured?"——已配置":"——尚未设置"}。</b>
    <div class="r">MCP 让 KnowMe 可以借用任意外部服务器提供的工具（文件、GitHub、数据库等），
    工具名称采用 <code>&lt;服务器&gt;_&lt;工具&gt;</code> 命名空间。${m.configured
      ? `已配置服务器：${m.servers.map(s=>`<code>${esc(s)}</code>`).join(" ")}${m.live?"":"——发起一次聊天即可连接。"}`
      : "尚未配置服务器。"}</div></div>`;
  h += `<h2>连接一个服务器（约 30 秒）</h2><div class="card">
    <div class="meta">1——安装可选依赖：<code>pip install -e '.[mcp]'</code></div>
    <div class="meta" style="margin-top:6px">2——在${reveal(""," .knowme 文件夹")}中创建 <code>mcp.json</code>：</div>
    <pre style="font-family:var(--mono);font-size:11.5px;color:var(--ink2);white-space:pre-wrap;margin-top:8px">{"servers": [
  {"name": "fs", "command": "npx",
   "args": ["-y", "@modelcontextprotocol/server-filesystem", "${esc(D&&D.home||"")}"]}
]}</pre>
    <div class="meta" style="margin-top:8px">3——重新启动仪表盘。该服务器的工具会出现在上方的
      <a class="reveal" onclick="location.hash='tools/available'">可用工具 ▸ MCP 服务器</a>中，并可在聊天中调用。</div></div>`;
  h += `<div class="meta" style="margin-top:12px">这种模式可以扩展到任意 MCP 服务器（你自己的或厂商提供的），
    接入方式完全相同，无需修改 KnowMe 代码。技能也遵循相同思路——只需把 <code>SKILL.md</code>
    放入 ${reveal("skills","skills/")}。</div>`;
  return h;
}

function connectionField(key, field, prefix="connection"){
  const id = `${prefix}-${key}-${field.name}`;
  const label = `${esc(field.label)}${field.required?" *":""}`;
  const help = field.help ? `<div class="conn-field-help">${esc(field.help)}</div>` : "";
  if (field.kind === "bool") return `<div class="conn-field">
    <label class="conn-check" for="${id}"><input id="${id}" data-field="${esc(field.name)}" type="checkbox" ${field.value?"checked":""}>
      <span>${label}</span></label>${help}</div>`;
  if (field.kind === "choice") return `<div class="conn-field"><label class="fld" for="${id}"><span>${label}</span>
    <select id="${id}" data-field="${esc(field.name)}">${field.options.map(o=>`<option value="${esc(o)}" ${o===field.value?"selected":""}>${esc(o)}</option>`).join("")}</select>
    </label>${help}</div>`;
  const configured = field.secret && field.configured
    ? ` <span class="conn-secret-state">已设置 ····${esc(field.last4)}</span>` : "";
  const clear = field.secret && field.configured
    ? `<label class="conn-clear"><input type="checkbox" data-clear="${esc(field.name)}"> 清除已保存的值</label>` : "";
  return `<div class="conn-field"><label class="fld" for="${id}"><span>${label}${configured}</span>
    <input id="${id}" data-field="${esc(field.name)}" type="${field.secret?"password":"text"}"
      value="${field.secret?"":esc(field.value)}" placeholder="${field.secret?(field.configured?"留空以保留已保存的值":"尚未配置"):""}">
    </label>${clear}${help}</div>`;
}

// Connections are shared with the English CLI, so localize their registry
// metadata at the Dashboard boundary instead of changing the API contract.
const CONNECTION_WHAT_ZH = {
  google_calendar:"让 KnowMe 创建和更新 Google 日历事件。",
  apple_calendar:"让 KnowMe 在这台 Mac 上使用 Apple 日历。",
  apple_tools:"让 KnowMe 使用 Apple 邮件及其他本地 Apple 工具。",
  notion:"将情景记忆保存在 Notion 数据库中。",
  mem0:"将语义记忆保存在 Mem0 服务中。",
  zep:"将语义记忆保存在 Zep 时序图中。",
  langmem:"通过 LangMem 将语义记忆保存在 LangGraph 存储中。",
  supabase:"将语义记忆保存在 Supabase pgvector 中。",
  tavily:"让 KnowMe 搜索网页。",
  otel:"将追踪数据导出到 OTLP 收集器。",
};
const CONNECTION_FIELD_ZH = {
  KNOWME_GOOGLE_CALENDAR:"启用 Google 日历",
  KNOWME_GOOGLE_CALENDAR_ID:"日历 ID", KNOWME_APPLE_CALENDAR:"启用 Apple 日历",
  KNOWME_APPLE_CALENDARS:"日历", KNOWME_APPLE_TOOLS:"启用 Apple 工具",
  KNOWME_EPISODIC_STORE:"情景记忆存储", NOTION_TOKEN:"集成 Token",
  NOTION_EPISODES_DATABASE_ID:"情景数据库 ID", KNOWME_SEMANTIC_STORE:"语义记忆存储",
  MEM0_API_KEY:"API 密钥", MEM0_USER_ID:"用户 ID", ZEP_API_KEY:"API 密钥",
  ZEP_USER_ID:"用户 ID", ZEP_MAX_WAIT_SECONDS:"最长等待时间（秒）",
  KNOWME_LANGMEM_POSTGRES:"Postgres URL", OPENAI_API_KEY:"OpenAI 密钥",
  SUPABASE_URL:"项目 URL", SUPABASE_SERVICE_KEY:"服务密钥",
  TAVILY_API_KEY:"API 密钥", OTEL_EXPORTER_OTLP_ENDPOINT:"OTLP 端点",
};
const CONNECTION_HELP_ZH = {
  MEM0_API_KEY:"密钥来自 app.mem0.ai。适配器使用 infer=False，确保 add() 始终存储；竞技场比较的是检索能力，而不是 Mem0 自身的提取步骤。",
  MEM0_USER_ID:"默认为“knowme”。只有多人共享同一个 Mem0 账户时才需要修改。",
  ZEP_API_KEY:"密钥来自 getzep.com。事实会成为带有效期的图边，因此更正会取代旧值，而不是与旧值并列排名。",
  ZEP_USER_ID:"默认为“knowme”。Zep 按用户划分图。",
  ZEP_MAX_WAIT_SECONDS:"数据摄取是异步的；KnowMe 会轮询等待其变为可搜索状态。默认 120 秒；写入超时时可以调高。",
  KNOWME_LANGMEM_POSTGRES:"可选。留空时使用 LangGraph 的 InMemoryStore，数据会随进程结束而消失，适合基准测试，不适合持久化。",
  OPENAI_API_KEY:"LangMem 没有独立密钥；Embedding 通过 OpenAI 计费。语义搜索需要此密钥。",
  SUPABASE_SERVICE_KEY:"Embedding 还需要 OPENAI_API_KEY，也可设置 OPENAI_EMBED_MODEL。",
};
function localizeConnection(item){
  return {...item, what: CONNECTION_WHAT_ZH[item.key] || item.what,
    fields: (item.fields || []).map(f => ({...f,
      label: CONNECTION_FIELD_ZH[f.name] || f.label,
      help: CONNECTION_HELP_ZH[f.name] || f.help}))};
}
function connectionMessageZh(message){
  if (!message) return "";
  if (message.startsWith("missing ")) return "缺少 " + message.slice(8);
  return message;
}
async function saveConnection(key, force){
  const modal = document.querySelector(`.connmodal[data-connection="${key}"]`), values = {}, clear = [];
  if (!modal) return;
  modal.querySelectorAll("[data-field]").forEach(el => values[el.dataset.field] = el.type === "checkbox" ? (el.checked ? "1" : "") : el.value);
  modal.querySelectorAll("[data-clear]").forEach(el => { if (el.checked) clear.push(el.dataset.clear); });
  const msg = document.getElementById(`connection-msg-${key}`);
  msg.textContent = force ? "正在跳过成功测试并保存…" : "正在保存…";
  const r = await postJSON("/api/connections", {key, values, clear, force:!!force});
  if (!r.ok && r.can_force) {
    msg.innerHTML = `${esc(r.error)} <button class="save ghost conn-force" onclick="saveConnection('${esc(key)}',true)">仍然保存</button>`;
  } else if (!r.ok) {
    msg.textContent = r.error || "失败";
  } else {
    closeConnectionModal();
    await refresh();
  }
}
async function testConnection(key){
  const msg = document.getElementById(`connection-msg-${key}`);
  if (msg) msg.textContent = "正在测试…";
  const r = await postJSON("/api/connections/test", {key});
  if (!r.status) {
    if (msg) msg.textContent = r.error || "失败";
    return;
  }
  const display = connectionStatusDisplay(r.status);
  const status = document.getElementById("connection-modal-status");
  if (status) {
    status.className = `connstatus ${display.className}`;
    status.innerHTML = `<span class="conndot"></span>${esc(display.label)}`;
  }
  const detail = document.getElementById("connection-modal-status-detail");
  if (detail) detail.textContent = connectionMessageZh(r.status.message || "");
  const checked = document.getElementById("connection-modal-checked");
  if (checked) checked.textContent = r.status.checked_at ? `上次检查：${r.status.checked_at}` : "";
  if (msg) msg.textContent = connectionMessageZh(r.status.message) || display.label;
  await refresh();
}
async function saveProvider(provider){
  const info = (D.providers || []).find(x => x.key === provider);
  const field = info && info.fields[0] && document.getElementById(`provider-${provider}-${info.fields[0].name}`);
  const payload = {provider};
  if (field && field.value) payload.key = field.value;
  // Models are global fields for the *current* provider. Switching cards must
  // omit them so apply_provider selects the new provider's own default.
  if (provider === stProvider()) {
    const model = document.getElementById("provider-model"), small = document.getElementById("provider-small-model"), base = document.getElementById("provider-base-url"), custom = document.getElementById("provider-custom-key");
    if (model) payload.model = model.value;
    if (small) payload.small_model = small.value;
    if (base) payload.base_url = base.value;
    if (custom && custom.value) payload.custom_key = custom.value;
    if (document.getElementById("provider-clear-custom-key")?.checked) payload.custom_key = "";
  }
  const r = await postJSON("/api/providers", payload);
  if (!r.ok) alert(r.error || "服务商更新失败"); else refresh();
}
function stProvider(){ return (D.settings || {}).provider || "anthropic"; }

const CONNECTION_GROUPS = ["Productivity", "Memory", "Tools"];
const CONNECTION_GROUP_LABELS = {Productivity:"效率工具", Memory:"记忆", Tools:"工具"};
// "Memory", not "Storage". The registry already calls this group "Memory &
// Storage"; the display map was dropping the half that says what these
// actually are. Notion is the episodic store, Supabase the semantic one, and
// every hosted memory service that joins them is semantic too — none of it is
// generic storage, and Memory is one of the four pillars the rest of the
// dashboard is organised around.
const CONNECTION_GROUP_MAP = {
  "Calendar & Productivity": "Productivity",
  "Memory & Storage": "Memory",
  "Search & Observability": "Tools",
};

function connectionDisplayGroup(item){
  if (item.key === "apple_tools") return "Tools";
  return CONNECTION_GROUP_MAP[item.group] || "Tools";
}

function connectionStatusDisplay(status){
  const state = (status && status.state) || "not_configured";
  if (state === "connected") return {label:"已连接", className:"connected"};
  if (state === "error") return {label:"错误", className:"error"};
  // "configured" means every required field is filled and the extra is
  // installed — it just hasn't been probed. That is not a warning, so it must
  // not wear the amber "needs setup" pill: this state covers most of a working
  // setup on first visit, and colouring it like a problem told every new user
  // their Telegram, Notion and Tavily needed fixing when they were fine.
  if (state === "configured") return {label:"已配置 · 未测试", className:"configured"};
  if (state === "installed_but_unconfigured") return {label:"需要设置", className:"needs-setup"};
  return {label:"尚未配置", className:"not-configured"};
}

function connectionCard(item){
  const display = connectionStatusDisplay(item.status);
  const action = item.status && item.status.state !== "not_configured" ? "编辑" : "配置";
  // Say WHY on the card. "needs setup" covers two unrelated fixes — a missing
  // value ("missing NOTION_TOKEN") and a missing package ("missing notion
  // extra", which wants a pip install, not a key) — and the reason used to be
  // hidden until you opened the modal. The message repeats the label for
  // connected/configured, so only show it where it adds something.
  const why = (item.status && item.status.message
    && (item.status.state === "installed_but_unconfigured" || item.status.state === "error"))
    ? `<div class="connwhy">${esc(connectionMessageZh(item.status.message))}</div>` : "";
  return `<article class="provcard conncard" data-connection-card="${esc(item.key)}">
    <img class="provlogo connlogo" src="/static/logos/connections/${esc(item.key)}.svg" alt="">
    <div class="provname">${esc(item.name)}</div>
    <div class="connstatus ${display.className}"><span class="conndot"></span>${esc(display.label)}</div>
    ${why}
    <div class="conndesc">${esc(item.what)}</div>
    <div class="provactions connactions">
      <button class="save ghost" onclick="openConnectionModal('${esc(item.key)}')">${action}</button>
    </div>
  </article>`;
}

function connectionsGrid(items){
  const grouped = Object.fromEntries(CONNECTION_GROUPS.map(group => [group, []]));
  items.forEach(item => grouped[connectionDisplayGroup(item)].push(item));
  return CONNECTION_GROUPS.map(group => `<section class="connsection">
    <h2>${CONNECTION_GROUP_LABELS[group] || group}</h2>
    <div class="provgrid conngrid">${grouped[group].map(connectionCard).join("")}</div>
  </section>`).join("") + `<div id="connection-modal-root"></div>`;
}

function openConnectionModal(key){
  const rawItem = ((D && D.connections) || []).find(connection => connection.key === key);
  const item = rawItem && localizeConnection(rawItem);
  const root = document.getElementById("connection-modal-root");
  if (!item || !root) return;
  markEditing();
  const display = connectionStatusDisplay(item.status);
  const status = item.status || {};
  const fields = item.fields.map(field => connectionField(item.key, field)).join("");
  root.innerHTML = `<div class="connmodal-back" onclick="closeConnectionModal()" onkeydown="connectionModalKeydown(event)">
    <section class="connmodal" data-connection="${esc(item.key)}" role="dialog" aria-modal="true" aria-labelledby="connection-modal-title" onclick="event.stopPropagation()">
      <header class="connmodal-head">
        <img class="provlogo connlogo" src="/static/logos/connections/${esc(item.key)}.svg" alt="">
        <div class="connmodal-title">
          <h3 id="connection-modal-title">${esc(item.name)}</h3>
          <div class="connstatus ${display.className}" id="connection-modal-status"><span class="conndot"></span>${esc(display.label)}</div>
        </div>
        <button class="connmodal-close" type="button" onclick="closeConnectionModal()" aria-label="关闭">关闭</button>
      </header>
      <p class="conndesc connmodal-desc">${esc(item.what)}</p>
      <div class="connmodal-meta">
        <span id="connection-modal-status-detail">${esc(connectionMessageZh(status.message || ""))}</span>
        <span id="connection-modal-checked">${status.checked_at?`上次检查：${esc(status.checked_at)}`:""}</span>
      </div>
      ${(item.install_command || item.setup_url) ? `<div class="connsetup">
        ${item.install_command?`<code>${esc(item.install_command)}</code>`:""}
        ${item.setup_url?`<a href="${esc(item.setup_url)}" target="_blank" rel="noopener noreferrer">设置指南 ↗</a>`:""}
      </div>` : ""}
      <div class="connection-fields">${fields}</div>
      <footer class="connmodal-actions">
        <button class="save" onclick="saveConnection('${esc(item.key)}')">保存</button>
        <button class="save ghost" onclick="testConnection('${esc(item.key)}')">测试连接</button>
        <span class="connmodal-message" id="connection-msg-${esc(item.key)}" aria-live="polite"></span>
      </footer>
    </section>
  </div>`;
  setTimeout(() => {
    const target = root.querySelector(".connection-fields input, .connection-fields select")
      || root.querySelector(".connmodal-close");
    target?.focus();
  }, 0);
}

function closeConnectionModal(){
  editing = false;
  const root = document.getElementById("connection-modal-root");
  if (root) root.innerHTML = "";
  if (activeView === "connections") render();
}

function connectionModalKeydown(event){
  if (event.key === "Escape") closeConnectionModal();
}

const VIEWS = {
  models(d){
    // Provider card grid (logo / status dot / edit / enable-disable). Editing
    // happens in a modal opened from a card; both live in js/models.js.
    return modelsGrid(d);
  },
  connections(d){
    const items = (d.connections || []).map(localizeConnection);
    return items.length ? connectionsGrid(items) : `<div class="card empty">尚未注册任何集成。</div>`;
  },
  // Conversation inbox shared by the dashboard and CLI. Each message is tagged
  // with where it came from. You type in the dock on the right.
  // Gateway = an INBOX of conversations (like Slack/Intercom): one row per
  // conversation, tagged with its channel(s). Click one to open it in the chat
  // dock (the active thread). No longer a flat stream that duplicates the dock.
  gateway(d){
    const sessions = d.sessions || [];
    let h = `<div class="meta" style="margin-bottom:14px">网页端和 CLI 的对话都使用同一套记忆与循环。
      点击一项即可在右侧聊天区打开 &rarr;。</div>`;
    if (!sessions.length)
      return h + `<div class="card empty">还没有对话——在右侧聊天区说点什么吧 &rarr;</div>`;
    h += sessions.map(s => {
      const tags = gwTags(s);
      const on = s.id === SESSION;
      return `<div class="toolcard" style="cursor:pointer${on?';border-color:var(--accent)':''}" onclick="openConversation('${esc(s.id)}')">
        <div class="tn" style="display:flex;justify-content:space-between;align-items:baseline;gap:10px">
          <span>${esc(s.title||s.id)} ${tags}</span>
          <span class="meta" style="font-weight:400;white-space:nowrap">${sessionMeta(s)}</span></div>
        <div class="td">${esc(s.last||"")}</div></div>`;
    }).join("");
    return h;
  },
  overview(d){
    const s = d.stats;
    const u = d.usage || {total_cost:0};
    const tiles = [
        [money(u.total_cost),"累计花费","money"],[secs(s.latency_avg),"平均每轮耗时",""],
        [s.turns,"对话轮数",""],[s.tool_calls,"工具调用",""],
        [d.facts.length,"事实",""],[d.calendar.length,"事件",""],
      ].map(([v,l,c])=>`<div class="tile"><b class="${c}">${v}</b><span>${l}</span></div>`).join("");
    return `<div class="tiles">${tiles}</div>
    <h2>检索门控——关键决策</h2>${gateSplit(s)}
    <h2 style="margin-top:26px">系统架构——点击任意方框 <span class="arch-status"></span></h2>
    ${archSVG(d)}
    <h2>图工作流——当一轮任务需要明确结构</h2>
    ${graphPanel(d)}
    <h2>最近一轮</h2>${d.turns.length?turnCard(d.turns[0]):'<div class="card empty">还没有对话——先和 KnowMe 聊一句吧</div>'}`;
  },
  loop(d){
    return d.turns.length ? d.turns.map(turnCard).join("") : `<div class="card empty">还没有对话</div>`;
  },
  // Graph workflows: the loop's sibling. The chart is rendered from the
  // engine's own describe() (served in d.graph.workflows) so it can never
  // show a shape the engine doesn't run. Nothing here is a mode switch —
  // the harness routes every message itself; this tab just tells the story.
  graph(d){
    const g = d.graph || {enabled:false, workflows:[], stats:{quick:0, full:0}};
    let h = `<div class="meta" style="margin-bottom:14px">循环表示一轮智能体对话：模型不断选择工具，直到停止。
      有些工作具有明确的<b>结构</b>——一些步骤可以同时运行，还需要显式的条件路由。
      <b>图工作流</b>把这种结构变成一等公民：节点各自完成一项工作，边规定下一步。
      核心循环一行都没有修改——下方的 <code>full_agent</code> 节点就是同一个循环，只是作为一个步骤运行。
      运行框架会自行分流每条消息；你也可以在聊天框中按名称调用工作流：输入 <code>/graphs</code> 查看列表。</div>`;
    if (!g.enabled)
      h += `<div class="card"><b>已关闭</b>——当前每轮对话都运行经典循环。
        <div class="meta" style="margin-top:6px">可在<a class="reveal" onclick="location.hash='settings'">行为</a>中开启
        <b>图工作流</b>，或在 <code>.env</code> 中设置 <code>KNOWME_GRAPH_WORKFLOWS=1</code>。
        任意环节失败都会回退到普通循环，因此它不会让回复丢失，只会节省时间和 Token。</div></div>`;
    // The two workflows are two different JOBS with different triggers, which is
    // the thing the page has to make obvious — otherwise two stacked charts read
    // like two options you pick between.
    const NOTE = {
      triage: `<b>每条消息都会自动运行。</b>是否启用由图工作流开关控制。
        实线箭头表示必经路径，虚线表示由路由器选择。<code>full_agent</code> 是作为单个节点运行的普通循环——
        图不会替代循环，而是安排如何调用循环。`,
      gather: `<b>由你主动启动</b>——运行 <code>make gather</code> 或点击下方按钮——并且不受开关影响。
        四项扫描互不依赖，因此引擎会在同一波次中并行运行，而不是依次运行。它只提出建议、不会直接执行；
        汇总结果会写入发件箱供你阅读。`,
    };
    (g.workflows || []).forEach(w => {
      if (!w) return;
      h += `<h2>${esc(w.name)}——实时拓扑 <span class="arch-status"></span></h2>`;
      const tot = g.stats.quick + g.stats.full;
      const extra = w.name === "triage" && tot
        ? ` · 至今 ${g.stats.quick} 次快速 / ${g.stats.full} 次完整` : "";
      h += `<div class="card">${graphSVG(w)}
        <div class="meta" style="margin-top:8px">${NOTE[w.name] || ""}${extra} ·
        由引擎自身的 <code>describe()</code> 绘制，因此图示不会与代码脱节</div></div>`;
      if (w.name === "gather") h += graphRunPanel();
    });
    const gturns = (d.turns||[]).filter(t => t.graph && t.graph.route);
    h += `<h2>图工作流对话</h2>`;
    h += gturns.length
      ? gturns.slice(0,20).map(t => `<div class="card">
          <div class="u">${esc(t.user_message)}</div>
          <div class="meta" style="margin-top:4px"><span class="badge ${t.graph.route==="quick"?"":"retrieve"}">图 · ${esc(statusZh(t.graph.route))}</span>
            <span class="meta" style="margin:0">${esc(t.graph.reason||"")}</span></div>
          <div class="r">${renderMarkdown(t.reply||"")}</div></div>`).join("")
      : `<div class="card empty">还没有图工作流对话——${g.enabled
          ? '在聊天中输入“谢谢！”，观察它走快速路径'
          : "请先打开开关"}</div>`;
    return h;
  },
  memory(d, sub){
    sub = sub || "overview";
    const tabs = [["overview","总览"],["semantic","语义记忆",d.facts.length],
      ["episodic","情景记忆",d.episodes.length],["skills","技能",d.skills.length],
      ["soul","SOUL"],["consolidation","记忆整理",d.chat_pending]];
    let h = subtabBar("memory", tabs, sub);
    if (sub==="semantic") return h + memSemantic(d);
    if (sub==="episodic") return h + memEpisodic(d);
    if (sub==="skills") return h + memSkills(d);
    if (sub==="soul") return h + memSoul(d);
    if (sub==="consolidation") return h + memConsolidation(d);
    return h + memOverview(d);
  },
  settings(d){
    const st = d.settings || {providers:[]};
    return `<h2>实验性工具</h2><div class="card">
      <div class="meta" style="margin-bottom:8px">允许在聊天中把编程任务委派给本地子智能体。</div>
      <label class="fld">子智能体委派<select id="set-experimental" onfocus="markEditing()">
        <option value="" ${!st.experimental?"selected":""}>关闭</option>
        <option value="1" ${st.experimental?"selected":""}>开启</option>
      </select></label>
      <button class="save" onclick="saveSettings()">保存</button><span class="meta" id="set-msg"></span></div>
    <h2>图工作流</h2><div class="card">
      <div class="meta" style="margin-bottom:8px">默认关闭。开启后，<b>每条</b>消息都会先通过分流图：
        小模型对消息分类的同时并行加载今日日历——简单消息由小模型快速回复，真正的任务则把完全相同的循环作为节点运行。
        此开关只控制自动入口；按名称调用的工作流（如 <code>/gather</code>）始终可以运行。
        任何故障都会回退到普通循环。可在<a class="reveal" onclick="location.hash='graph'">图工作流</a>标签页实时观察。</div>
      <label class="fld">优先分流每轮对话
        <select id="set-graph-workflows" onfocus="markEditing()">
          <option value="" ${!st.graph_workflows?"selected":""}>关闭——每轮都运行经典循环（默认）</option>
          <option value="1" ${st.graph_workflows?"selected":""}>开启——分流图为每条消息选择路径</option>
        </select></label>
      <div style="margin-top:12px"><button class="save" onclick="saveSettings()">保存并切换</button>
        <span class="meta" style="margin-left:10px">在当前进程中重建智能体——无需重启</span></div>
    </div>`;
  },
  tools(d, sub){
    const t = d.tools || {catalog:[], mcp:{configured:false,servers:[],live:false}, apple_on:false};
    sub = sub || "available";
    const tabs = [["available","可用工具",t.catalog.length],["results","执行结果"],
      ["mcp","MCP",t.mcp.servers.length||null]];
    let h = subtabBar("tools", tabs, sub);
    if (sub === "results") return h + toolsResults(d);
    if (sub === "mcp") return h + toolsMCP(t);
    // Available: what the agent CAN do (grouped by origin), not just what it did.
    h += `<div class="meta" style="margin-bottom:12px">智能体在本轮可以调用的能力。
      一个工具只由模型读取的名称和描述、JSON Schema，以及一个 Python 函数组成。
      ${t.apple_on?"":"Apple 工具已关闭（设置 <code>KNOWME_APPLE_TOOLS=1</code> 可开启）。"}可通过
      <a class="reveal" onclick="location.hash='tools/mcp'">MCP</a> 连接更多工具。</div>`;
    const SRC = [["flagship","旗舰任务——日程安排"],["web","网页搜索"],
      ["self-management","自管理——编辑自身记忆"],
      ["apple","Apple 生态"],["mcp","MCP 服务器"],["other","其他"]];
    SRC.forEach(([key,label]) => {
      const items = t.catalog.filter(c => c.source === key);
      if (!items.length) return;
      h += `<h2>${label}</h2>`;
      h += items.map(c => `<div class="toolcard">
        <div class="tn">${esc(c.name)}<span class="srcpill ${key==="mcp"?"mcp":key==="apple"?"apple":""}">${esc(key)}</span></div>
        <div class="td">${esc(TOOL_DESC_ZH[c.name] || c.description)}</div></div>`).join("");
    });
    // Roadmap: whiteboard boxes not wired in yet — set expectations, don't over-promise.
    if ((t.planned||[]).length){
      h += `<h2>即将推出 <span class="meta" style="font-weight:400">· 已出现在架构图中，但尚未接通（设置 <code>KNOWME_EXPERIMENTAL=1</code> 可选择启用）</span></h2>`;
      h += t.planned.map(p => `<div class="toolcard" style="opacity:.7">
        <div class="tn">${esc(p.name)}<span class="srcpill apple">即将推出 · ${esc(p.box)}</span></div>
        <div class="td">${esc(TOOL_DESC_ZH[p.name] || p.description)}</div></div>`).join("");
    }
    return h;
  },
  database(d, sub){
    // The persistence layer itself — one SQLite file, real tables, FTS5 index.
    // "Data" in the nav (plainer than "state.db"), but we keep saying state.db
    // because that's literally the filename you can open.
    const db = d.db || {tables:[], all_tables:[], fts:[], size:0, path:""};
    const tables = db.tables || [];
    sub = sub || "overview";
    const tabs = [["overview","总览"],
      ...tables.map(t => [t.name, t.name, t.count]),
      ["query","SQL 控制台"]];
    let h = subtabBar("database", tabs, sub);
    if (sub === "query") return h + dbQueryView();
    if (sub !== "overview"){
      const t = tables.find(x => x.name === sub);
      if (!t) return h + `<div class="card empty">没有这个表</div>`;
      const notionNote = (t.name === "episodes" && d.episodes_source === "notion")
        ? `<div class="meta" style="margin-bottom:10px">情景记忆目前保存在 Notion 中——参见
            <a class="reveal" onclick="location.hash='memory/episodic'">记忆 ▸ 情景记忆</a>。
            下方内容是 state.db 中的旧本地副本。</div>` : "";
      return h + notionNote + `<div class="meta" style="margin-bottom:10px">${DB_DESC[t.name]||""}</div>` + dbTable(t);
    }
    const kb = (db.size/1024).toFixed(1);
    h += `<div class="card" style="border-color:var(--accent);background:var(--accent-soft)">
      <b>数据库与记忆。</b> <span class="r">这里是原始持久化层——真正的 SQLite 数据表。
      <a class="reveal" onclick="location.hash='memory'">记忆标签页</a>以更友好的方式展示相同数据（事实、情景、技能和人格）。
      同一个文件，两种观察层级。Hermes 使用 <code>MEMORY.md</code> 文件，而 KnowMe 使用这些可查询的数据表，
      并同时将其镜像为易读的 <code>MEMORY.md</code>。</span></div>`;
    h += `<div class="card">
      <div class="u" style="font-family:var(--mono);font-size:12.5px;word-break:break-all">${esc(db.path)}</div>
      <div class="meta">磁盘占用 ${kb} KB · SQLite + FTS5 · 可自行打开：<code>sqlite3 .knowme/state.db</code></div>
      <div class="meta" style="margin-top:8px">${reveal("state.db","在文件管理器中显示 state.db")} &nbsp;·&nbsp; ${reveal("","打开 .knowme 文件夹")}</div></div>`;
    h += `<h2>数据表——点击上方标签或下方某一行</h2>`;
    h += table(["表名","行数","保存内容"], tables.map(t =>
      `<tr><td><a class="reveal" onclick="location.hash='database/${esc(t.name)}'"><code>${esc(t.name)}</code></a></td>
        <td class="meta">${t.count}</td><td class="meta">${DB_DESC[t.name]||""}</td></tr>`));
    h += `<h2>FTS5——关键词索引</h2><div class="card"><code>*_fts</code> 虚拟表（以及对应的
      <code>*_fts_data</code>/<code>*_fts_idx</code> 影子表）让记忆支持关键词搜索——无需 Embedding，也无需向量数据库。
      检索门控查询的“关键词 top-k”就是它。
      <div class="meta" style="margin-top:8px">全部 ${db.all_tables.length} 个表：${db.all_tables.map(t=>`<code>${esc(t)}</code>`).join(" ")}</div></div>`;
    return h;
  },
  ops(d){
    const s = d.stats;
    const u = d.usage || {calls:0,total_in:0,total_out:0,total_cost:0,by_day:[],by_provider:[]};
    let h = `<div class="tiles">${[
        [money(u.total_cost),"累计花费","money"],[u.total_in.toLocaleString(),"累计输入 Token",""],
        [u.total_out.toLocaleString(),"累计输出 Token",""],[u.calls.toLocaleString(),"LLM 调用",""],
        [secs(s.latency_avg),"平均每轮耗时",""],[`${s.tool_errors}`,"工具错误",""],
      ].map(([v,l,c])=>`<div class="tile"><b class="${c}">${v}</b><span>${l}</span></div>`).join("")}</div>`;

    h += `<h2>费用 <span class="meta" style="font-weight:400">· 永久账本——重置演示数据后仍会保留</span></h2>`;
    h += `<div class="card"><span class="r">每次 LLM 调用的 Token 都会记录到
      <code>.knowme/usage.jsonl</code>（只追加，永不清空）。美元成本根据 Token × 当前价格估算；
      Token 数本身是真实记录。${reveal("usage.jsonl","打开 usage.jsonl")}</span></div>`;
    if ((u.by_provider||[]).length){
      h += table(["服务商","LLM 调用","输入 Token","输出 Token","估算成本"], u.by_provider.map(p =>
        `<tr><td><code>${esc(p.provider)}</code></td><td class="meta">${p.calls}</td>
          <td class="meta">${p.in.toLocaleString()}</td><td class="meta">${p.out.toLocaleString()}</td>
          <td class="meta">${money(p.cost)}</td></tr>`));
    }
    if ((u.by_day||[]).length){
      h += `<h2>每日费用</h2>`;
      h += table(["日期","LLM 调用","输入 Token","输出 Token","估算成本"], u.by_day.map(r =>
        `<tr><td class="meta">${esc(r.date)}</td><td class="meta">${r.calls}</td>
          <td class="meta">${r.in.toLocaleString()}</td><td class="meta">${r.out.toLocaleString()}</td>
          <td class="meta">${money(r.cost)}</td></tr>`));
    }

    h += `<h2>检索门控——哪些对话使用了记忆</h2>${gateSplit(s)}`;
    const decided = d.turns.filter(t => t.gate);
    if (decided.length){
      h += `<div class="meta" style="margin:8px 0">实际决策（跳过或检索），最近的在前：</div>`;
      h += table(["对话","决策","原因"], decided.slice(0,10).map(t =>
        `<tr><td>${esc((t.user_message||"").slice(0,44))}</td>
          <td><span class="pill ${t.gate.decision==="skip"?"skip":"pass"}">${esc(statusZh(t.gate.decision))}</span></td>
          <td class="meta">${esc(t.gate.reason||"")}</td></tr>`));
    }

    h += `<h2>发布门禁 <span class="meta" style="font-weight:400">· 判断是否可以发布</span></h2>`;
    h += `<div class="card"><span class="r">发布改动（新提示词、切换模型、调整检索）前，
      <code>make gate</code> 会运行两套评测：确定性测试必须 100% 通过，模型评判必须达到阈值。
      它由你手动运行，因此每次执行都会留下一条记录。下方历史会随每次运行持续增长。</span></div>`;
    h += d.eval_report ? `<div class="card">
        <span class="pill ${d.eval_report.deterministic}">确定性评测 · ${statusZh(d.eval_report.deterministic)}</span>
        <span class="pill ${d.eval_report.judge==="pass"?"pass":d.eval_report.judge==="fail"?"fail":"skip"}" style="margin-left:8px">模型评判 · ${statusZh(d.eval_report.judge)}</span>
        <div class="meta">上次运行：${esc(d.eval_report.ran_at)}——使用 <code>make gate</code> 重新运行</div></div>`
      : `<div class="card empty">尚未运行——执行 <code>make gate</code> 后这里会显示结果</div>`;

    if ((d.eval_history||[]).length){
      const cnt = s => s ? `${s.passed||0} 通过 · ${s.failed||0} 失败` : "—";
      h += `<h2>评测历史</h2>`;
      h += table(["时间","确定性评测","模型评判","数量"], d.eval_history.map(r =>
        `<tr><td class="meta">${esc((r.ran_at||"").replace("T"," ").slice(0,19))}</td>
         <td><span class="pill ${r.deterministic}">${esc(statusZh(r.deterministic))}</span></td>
         <td><span class="pill ${r.judge==="pass"?"pass":r.judge==="fail"?"fail":"skip"}">${esc(statusZh(r.judge))}</span></td>
         <td class="meta">确定性 ${cnt(r.suites&&r.suites.deterministic)} · 评判 ${cnt(r.suites&&r.suites.judge)}</td></tr>`));
    }

    h += `<h2>最慢的对话</h2>`;
    const slow = [...d.turns].filter(t=>t.latency_ms!=null).sort((a,b)=>b.latency_ms-a.latency_ms).slice(0,6);
    h += table(["对话","延迟","成本","工具"], slow.map(t =>
      `<tr><td>${esc((t.user_message||"").slice(0,48))}</td><td class="meta">${secs(t.latency_ms)}</td><td class="meta">${money(t.cost||0)}</td><td class="meta">${(t.tools||[]).map(x=>x.tool).join(", ")||"—"}</td></tr>`));

    h += `<h2>追踪 <span class="meta" style="font-weight:400">· 每轮对话都记录为 JSONL，始终开启</span></h2>`;
    if ((d.trace_errors||[]).length){
      h += d.trace_errors.map(e => `<div class="card"><span class="pill fail">追踪编码错误</span>
        <div class="meta" style="margin-top:8px"><code>${esc(e.file)}</code> — ${esc(e.error)}</div></div>`).join("");
    }
    h += `<div class="card"><span class="r"><code>traces/</code> 中有 ${s.trace_files} 个追踪文件${
      d.trace_file?`（最新：<code>${esc(d.trace_file)}</code>）`:""}。${reveal("traces","打开追踪文件夹")}。
      追踪就是按顺序记录“发生了什么”——以下是最近的记录：</span></div>`;
    h += (d.trace_tail||[]).length ? table(["事件","详情","时间"], d.trace_tail.map(e =>
        `<tr><td><code>${esc(e.type)}</code></td><td class="meta">${esc(String(e.detail).slice(0,60))}</td>
          <td class="meta">${esc((e.ts||"").replace("T"," ").slice(0,19))}</td></tr>`))
      : `<div class="card empty">还没有追踪记录——先和 KnowMe 聊一句吧</div>`;
    h += `<div class="meta" style="margin-top:8px">Span 瀑布图：<code>make trace</code> + <code>OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317</code>。</div>`;

    if (d.wake_scans.length){
      h += `<h2>语音——接近唤醒但未命中</h2>`;
      h += table(["听到的内容","时间"], d.wake_scans.map(w =>
        `<tr><td>${esc(w.heard)}</td><td class="meta">${esc((w.ts||"").replace("T"," ").slice(0,19))}</td></tr>`));
    }
    return h;
  },
};

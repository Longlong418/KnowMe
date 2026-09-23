// Coding Workspace: the page, in the order a person actually works.
//
//   1. what this page is pointed at, and what the agent may do here
//   2. one box for the task, one button to send it
//   3. the project on the left; what changed, on the right
//
// Three bands because the old page said the same thing three times (the root
// appeared in a banner, in a hint and in the task box) while the two things you
// actually need to know — can it write? is the CLI there? — were buried in
// equal-weight cards. The switches here are the only ones; the page stores
// nothing else, and no CLI credential ever passes through it.

let currentCodingRun = "";     // the receipt row whose detail is open
let currentCodingPatch = "";

const CODING_KIND_ZH = {baseline: "起点", write: "写文件", edit: "改文件",
                        command: "跑命令", delegate: "交给 CLI"};

const codingTime = iso => String(iso || "").slice(11, 16);
const codingIsChange = r => r.kind !== "baseline";
// The distinct FILES the agent has touched. Command rows keep the command in
// `target`, so counting every non-baseline row would report a pytest run as a
// file that changed.
const codingChangedFiles = runs =>
  new Set(runs.filter(r => r.kind === "write" || r.kind === "edit").map(r => r.target));

// What one receipt row says in the right-hand column: a command reports how it
// exited, an edit reports lines added and removed. Both are the answer to
// "did it actually work", which is the whole point of the receipt.
function codingDelta(r){
  if (r.kind === "command" || r.kind === "delegate"){
    // A command row with no return code is one that timed out (coding.py
    // records None only there) — saying "退出 0" about it would be a lie.
    if (r.returncode === 0) return `<span class="cd-ok">退出 0</span>`;
    if (r.returncode == null) return `<span class="cd-warn">超时</span>`;
    return `<span class="cd-bad">退出 ${r.returncode}</span>`;
  }
  if (!r.insertions && !r.deletions) return "";
  return `<span class="cd-add">+${r.insertions}</span> <span class="cd-del">−${r.deletions}</span>`;
}

// --------------------------------------------------------------- 1. the band

function codingWriteHint(write){
  return write
    ? "它会自己读文件、改文件、跑测试，改了哪里都会留在右边的「改动」里。"
    : "开关关着，它现在只能读和回答；打开上面的开关它才能动手。";
}

function codingStatusBand(coding, root, runs){
  const settings = coding.settings || {};
  const write = !!settings.allow_write;
  const enabled = (coding.backends || []).filter(b =>
    b.installed && (settings.enabled || {})[b.id]).map(b => b.label);
  const cli = !coding.execution_enabled ? "未启用"
    : (enabled.length ? enabled.join(" / ") : "没有启用任何后端");
  const changed = codingChangedFiles(runs);
  return `<div class="card coding-band">
    <div class="cb-path"><span class="cb-label">项目根</span>
      <code title="${esc(root)}">${esc(root || "未配置项目根目录")}</code></div>
    <div class="cb-right">
      <span class="pill skip" title="读文件、搜索、看 git 随时可用，不受开关影响">读 ✓</span>
      <label class="cb-switch ${write ? "on" : ""}" title="写文件、改文件、跑命令的总开关">
        <input type="checkbox" id="coding-write" ${write ? "checked" : ""}
               onchange="toggleCodingWrite(this.checked)">
        <b>允许写和跑命令</b>
        <small>${write ? "KnowMe 可以改这个目录里的文件" : "现在是只读的——改文件和跑命令都会被拒绝"}</small>
      </label>
      <span class="pill ${coding.execution_enabled && enabled.length ? "pass" : "fail"}"
            title="本机 Coding CLI：KnowMe 可以把大活交给它">本机 CLI ${esc(cli)}</span>
      ${changed.size ? `<span class="pill">${changed.size} 个文件有改动</span>` : ""}
    </div>
  </div>`;
}

// ---------------------------------------------------------- 2. the task card

function codingTaskCard(coding, root, write){
  const settings = coding.settings || {};
  const ready = (coding.backends || []).filter(b => b.installed && (settings.enabled || {})[b.id]);
  const options = ready.map(b => `<option value="${esc(b.id)}"
    ${settings.default_backend === b.id ? "selected" : ""}>${esc(b.label)}</option>`).join("");
  const stored = localStorage.getItem("knowme_coding_task") || "";
  return `<div class="card coding-task-card">
    <div class="ctc-head">
      <b>让它去做</b>
      <span class="meta" id="coding-write-hint">${codingWriteHint(write)}</span>
    </div>
    <textarea id="coding-task" class="editor" rows="3"
      placeholder="例如：修复登录接口的测试，并运行相关 pytest">${esc(stored)}</textarea>
    <div class="ctc-actions">
      <button class="save" onclick="startCodingTask()" ${root ? "" : "disabled"}>让它去做</button>
      <label class="fld-inline" title="它觉得这件事更适合本机 CLI 时，会用这个后端">后备 CLI
        <select id="coding-default-backend">${
          options || `<option value="">没有已启用的本地 CLI</option>`}</select>
      </label>
      <span class="meta">任务会带上项目目录，并说明它可以怎么动手。</span>
    </div>
  </div>`;
}

// The CLI plumbing, folded away: which backend is installed is something you
// look at once, and it used to take as much room as the task itself.
function codingBackendPanel(coding){
  const settings = coding.settings || {default_backend: "pi", enabled: {}};
  const cards = (coding.backends || []).map(b => `<label class="coding-backend ${b.installed ? "" : "unavailable"}">
    <input type="checkbox" data-coding-backend="${esc(b.id)}" ${b.enabled ? "checked" : ""} ${b.installed ? "" : "disabled"}>
    <span class="coding-backend-copy"><b>${esc(b.label)}</b><small>${esc(b.description)}</small>
      <em class="coding-status ${b.installed ? "ok" : "bad"}">${b.installed ? "已安装" : "未找到命令"}</em></span>
  </label>`).join("");
  return `<details class="card coding-config">
    <summary>本机 Coding Agent 设置${coding.execution_enabled ? "" : "（未启用）"}</summary>
    <div class="coding-config-body">
      <label class="coding-execution"><input id="coding-execution" type="checkbox"
        ${coding.execution_enabled ? "checked" : ""}> 允许 Coding Agent 调用本机 CLI</label>
      <div class="coding-backends">${cards || `<div class="empty">没有检测到本机 Coding CLI。</div>`}</div>
      <div class="meta coding-hint">开关只控制 KnowMe 是否允许选择该 CLI，不会保存或读取它们的登录凭证。</div>
      <button class="save ghost" onclick="saveCodingSettings()">保存这两个开关</button>
    </div>
  </details>`;
}

function codingControls(coding, root, runs){
  return codingStatusBand(coding, root, runs)
    + codingTaskCard(coding, root, !!((coding.settings || {}).allow_write))
    + codingBackendPanel(coding);
}

function codingTabs(sub, runs, entries){
  return subtabBar("coding", [
    ["files", "项目文件", entries.filter(e => e.kind === "file").length],
    ["changes", "改动", runs.filter(codingIsChange).length || null],
  ], sub);
}

// --------------------------------------------------------------- repainting
//
// Every action on this page paints itself now. render() used to be enough, but
// the view is skipped while you are on it (main.js — so a 5s poll cannot shut
// your folded directories), which makes render() a no-op here. Nothing is
// repainted wholesale: the task textarea holds what you are typing, and the
// file tree holds which directories you opened.

function codingRuns(){ return (D && D.coding_runs) || []; }
function codingRoot(){ return (D && D.workspace && D.workspace.root) || ""; }

// The band (the switch and the status pills), the tab counts, and the receipt —
// everything the server owns. Not the tree, not the textarea.
function repaintCoding(){
  const band = document.getElementById("coding-band");
  if (band) band.innerHTML = codingStatusBand(D.coding || {}, codingRoot(), codingRuns());
  const hint = document.getElementById("coding-write-hint");
  if (hint) hint.textContent = codingWriteHint(writeAllowed());
  const tabs = document.getElementById("coding-tabs");
  if (tabs) tabs.innerHTML = codingTabs(activeSub, codingRuns(), (D.workspace || {}).entries || []);
  // Only the receipt can change from an action; the file pane is left alone so
  // the tree keeps its open directories.
  if (activeSub === "changes"){
    const pane = document.getElementById("coding-pane");
    if (pane) pane.innerHTML = codingChanges(codingRuns());
  }
}

// Opening a file repaints the preview only — never the tree beside it.
function repaintCodingPreview(){
  const el = document.getElementById("coding-preview");
  if (el) el.innerHTML = codingPreview();
}

const writeAllowed = () => !!(((D || {}).coding || {}).settings || {}).allow_write;

async function toggleCodingWrite(checked){
  const result = await postJSON("/api/coding", {action: "save", allow_write: !!checked});
  if (!result.ok){ alert(result.error || "保存开关失败"); }
  else D.coding = result;
  repaintCoding();
}

async function saveCodingSettings(){
  const enabled = {};
  document.querySelectorAll("[data-coding-backend]").forEach(input => {
    enabled[input.dataset.codingBackend] = input.checked;
  });
  const execution = document.getElementById("coding-execution");
  const payload = {action: "save", enabled, execution_enabled: !!(execution && execution.checked)};
  // The select only lists READY backends, so an empty one means "no preference I
  // can express right now" — omit the key and the stored choice stands, rather
  // than sending "" and having the server reject the whole save.
  const select = document.getElementById("coding-default-backend");
  if (select && select.value) payload.default_backend = select.value;
  const result = await postJSON("/api/coding", payload);
  if (!result.ok){ alert(result.error || "保存 Coding Agent 设置失败"); return; }
  D.coding = result;
  repaintCoding();
}

// Start a task: hand it to the Coding Agent with the tools it actually has,
// rather than the old fixed order to call delegate_task. That line is why the
// page felt like a chat window with a courier built in — it told the agent to
// pass the work on instead of doing it.
function startCodingTask(){
  const taskEl = document.getElementById("coding-task");
  const select = document.getElementById("coding-default-backend");
  const task = (taskEl && taskEl.value || "").trim();
  const backend = select && select.value;
  const root = (D.workspace && D.workspace.root) || "";
  if (!task || !root) return;
  localStorage.setItem("knowme_coding_task", task);
  if (backend) localStorage.setItem("knowme_coding_backend", backend);

  const write = writeAllowed();
  const lines = [
    `请在项目 ${root} 中完成下面的编码任务：`,
    task,
    "",
    "你有自己的工具可以直接动手：list_files / read_file / search_files / write_file / "
      + "edit_file / run_command / git_status / git_diff。先用它们搞清楚现状，改完之后运行与改动"
      + "相关的测试，并把「改了哪些文件、测试结果」说清楚。",
  ];
  if (backend) {
    lines.push("如果这件事更适合交给本机 CLI（大范围重构、要跑很久），你可以自己调用 delegate_task "
      + `并说明理由，后端优先用 "${backend}"。`);
  }
  if (!write) {
    lines.push("注意：现在「允许写和跑命令」是关着的，你只能读和回答。如果这件事必须改文件，"
      + "先告诉用户去 Coding Workspace 页面打开那个开关，不要绕开它。");
  }
  lines.push("不要修改 .env，也不要自动提交 Git。");

  location.hash = "#agent/coding";
  let attempts = 0;
  const sendWhenReady = () => {
    const input = document.getElementById("dmsg");
    const send = document.getElementById("dsend");
    if (!input || !send){
      if (++attempts < 30) setTimeout(sendWhenReady, 100);
      return;
    }
    input.value = lines.join("\n\n");
    send.click();
  };
  sendWhenReady();
}

// ------------------------------------------------------- 3a. the project tree

// `entries` arrives as ONE flat list (coding_workspace.list_entries): path,
// name, kind, depth. Nesting it here rather than in Python keeps one traversal
// rule — the server's — and one presentation, and native <details> gives the
// folding away free: no state of ours to lose on a repaint, and the directory
// rows are finally clickable (they used to be drawn with a ▸ that did nothing).
//
// Children are attached to their PARENT PATH, not walked with a depth counter
// or a stack. The list is sorted by path, and '/' sorts after most name
// characters, so a sibling directory lands between a directory and its own
// children — `src.bak` sorts between `src` and `src/app.py` — and anything
// counting depth (or closing directories as it goes) files those children
// under the wrong parent.
function codingTree(entries){
  if (!entries.length) return `<div class="empty">没有可浏览的文本文件。</div>`;
  const kids = new Map();        // parent path -> its entries, in server order
  const directories = new Set();
  for (const entry of entries){
    const cut = entry.path.lastIndexOf("/");
    const parent = cut < 0 ? "" : entry.path.slice(0, cut);
    if (entry.kind === "directory") directories.add(entry.path);
    if (!kids.has(parent)) kids.set(parent, []);
    kids.get(parent).push(entry);
  }
  // Every ancestor is in the list — os.walk emits a directory before descending
  // into it — so this only fires if that stops being true. A file at the top of
  // the tree beats a file the browser hides without saying so.
  const root = kids.get("") || [];
  for (const parent of [...kids.keys()]){
    if (parent && !directories.has(parent)){ root.push(...kids.get(parent)); kids.delete(parent); }
  }
  kids.set("", root);
  const render = (parent, depth) => (kids.get(parent) || []).map(entry => {
    const pad = 8 + depth * 14;
    if (entry.kind !== "directory"){
      return `<div class="ct-file" style="padding-left:${pad}px" title="${esc(entry.path)}"
        onclick="openCodingFileEncoded('${encodeURIComponent(entry.path)}')">${esc(entry.name)}</div>`;
    }
    return `<details class="ct-dir" ${depth === 0 ? "open" : ""}>
      <summary style="padding-left:${pad}px" title="${esc(entry.path)}">${esc(entry.name)}</summary>
      <div class="ct-kids">${render(entry.path, depth + 1)}</div></details>`;
  }).join("");
  return render("", 0);
}

function codingPreview(){
  if (!currentCodingFile){
    return `<div class="card empty">从左边选一个文本文件。打开后它会自动进入当前 Agent 的
      Context Bridge，你可以在对话里直接问它。</div>`;
  }
  return `<div class="card coding-preview">
    <div class="cp-head"><code title="${esc(currentCodingFile)}">${esc(currentCodingFile)}</code>
      <button class="save ghost" onclick="closeCodingFile()">关闭</button></div>
    <pre>${esc(currentCodingContent)}</pre></div>`;
}

function codingFiles(ws){
  const entries = ws.entries || [];
  return `<div class="coding-grid">
    <section><h2>项目文件 <span class="meta">${entries.filter(e => e.kind === "file").length} 个文本文件</span></h2>
      <div class="card coding-tree">${codingTree(entries)}</div></section>
    <section><h2>文件预览</h2><div id="coding-preview">${codingPreview()}</div></section>
  </div>`;
}

// --------------------------------------------------------- 3b. the receipt

// The diff the agent produced, coloured. Every line goes through esc(): a patch
// is file content — text somebody else wrote — so a line reading `+<b>hi</b>`
// must show up as those characters, not as bold.
function codingDiff(text){
  const body = String(text || "").split("\n").map(line => {
    if (line.startsWith("@@")) return `<span class="dl-h">${esc(line)}</span>`;
    if (line.startsWith("+++") || line.startsWith("---")) return `<span class="dl-f">${esc(line)}</span>`;
    if (line.startsWith("+")) return `<span class="dl-add">${esc(line)}</span>`;
    if (line.startsWith("-")) return `<span class="dl-del">${esc(line)}</span>`;
    return esc(line);
  }).join("\n");
  return `<pre class="coding-diff">${body}</pre>`;
}

function codingRunDetail(){
  if (!currentCodingRun) return "";
  const row = (D.coding_runs || []).find(r => r.detail === currentCodingRun);
  const isDiff = currentCodingRun.endsWith(".diff");
  return `<div class="card coding-detail">
    <div class="cp-head"><b>${esc((row && row.target) || CODING_KIND_ZH[
      (row && row.kind) || ""] || "这次改动")}</b>
      <button class="save ghost" onclick="closeCodingRun()">收起</button></div>
    ${isDiff ? codingDiff(currentCodingPatch)
             : `<pre class="coding-log">${esc(currentCodingPatch)}</pre>`}</div>`;
}

function codingChanges(runs){
  if (!runs.length){
    return `<div class="card empty">这个项目还没有被改过。<br>
      Coding Agent 每写一个文件、每跑一条命令，这里都会留一条记录（谁改的、改了什么、±多少行）。</div>`;
  }
  const rows = runs.map(r => {
    const open = r.detail && r.detail === currentCodingRun;
    // The row id is encoded, so a stored name can never close the attribute or
    // the JS string around it. Note the quote after `flat`: the handler is a
    // SEPARATE attribute, not a third class name.
    const clickable = r.detail
      ? ` onclick="openCodingRun('${encodeURIComponent(r.detail)}')"` : "";
    return `<div class="cr-row ${open ? "on" : ""} ${r.detail ? "" : "flat"}"${clickable}>
      <span class="cr-time">${esc(codingTime(r.at))}</span>
      <span class="cr-kind k-${esc(r.kind)}">${esc(CODING_KIND_ZH[r.kind] || r.kind)}</span>
      <span class="cr-target" title="${esc(r.summary || r.target)}">${
        esc(r.kind === "baseline" ? (r.summary || "起点") : (r.target || r.summary || ""))}</span>
      <span class="cr-delta">${codingDelta(r)}</span>
      ${r.detail ? `<span class="cr-open">${open ? "▾" : "▸"}</span>` : ""}</div>`;
  }).join("");
  return `<div class="card coding-runs">${rows}</div>${codingRunDetail()}`;
}

// Read one stored diff. The name comes from the row, never from a path the user
// types, and the server checks it against its own id shape before opening it.
async function openCodingRun(encoded){
  const detail = decodeURIComponent(encoded);
  if (detail === currentCodingRun){ closeCodingRun(); return; }
  const res = await postJSON("/api/workspace", {action: "run", detail, agent_id: ACTIVE_AGENT});
  if (!res.ok){ alert("读不到这次改动的详情：" + (res.error || "未知错误")); return; }
  currentCodingRun = detail;
  currentCodingPatch = res.detail || "(这次没有留下内容)";
  if (activeView === "coding") repaintCoding();
}

function closeCodingRun(){
  currentCodingRun = "";
  currentCodingPatch = "";
  if (activeView === "coding") repaintCoding();
}

// The strip under the header of the Coding Agent's own thread. A thread that
// looks exactly like the chat page is a thread you cannot tell is working on
// your project — this says which project, whether it may write, and what it has
// touched, with a link to the full receipt.
function codingStrip(){
  const root = codingRoot();
  const runs = codingRuns();
  const changed = codingChangedFiles(runs);
  const write = writeAllowed();
  return `<div class="coding-strip">
    <span class="cs-root" title="${esc(root)}">⌘ ${esc(root || "未配置项目根目录")}</span>
    <span class="cs-flag ${write ? "on" : ""}">${write ? "可以改文件" : "只读"}</span>
    <span class="cs-flag ${changed.size ? "work" : ""}">${
      changed.size ? `项目里有 ${changed.size} 个文件被改过` : "还没有改动"}</span>
    <a class="cs-link" href="#coding/changes">看改动 →</a></div>`;
}

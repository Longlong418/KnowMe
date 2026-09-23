// Coding Workspace controls. The page stores only backend switches; credentials
// stay with each local CLI and are never sent through this API.

function codingControls(info, root){
  const settings = info.settings || {default_backend:"pi", enabled:{}};
  const cards = (info.backends || []).map(b => `<label class="coding-backend ${b.installed ? "" : "unavailable"}">
    <input type="checkbox" data-coding-backend="${esc(b.id)}" ${b.enabled ? "checked" : ""} ${b.installed ? "" : "disabled"}>
    <span class="coding-backend-copy"><b>${esc(b.label)}</b><small>${esc(b.description)}</small>
      <em class="coding-status ${b.installed ? "ok" : "bad"}">${b.installed ? "已安装" : "未找到命令"}</em></span>
  </label>`).join("");
  const options = (info.backends || []).filter(b => b.installed && b.enabled)
    .map(b => `<option value="${esc(b.id)}" ${settings.default_backend===b.id ? "selected" : ""}>${esc(b.label)}</option>`).join("");
  const execution = info.execution_enabled
    ? `<span class="pill pass">delegate_task 已启用</span>`
    : `<span class="pill fail">需要先开启 KNOWME_EXPERIMENTAL</span>`;
  return `<section class="coding-control-grid">
    <div class="card coding-task-card">
      <div class="coding-section-title"><b>开始一个编码任务</b>${execution}</div>
      <textarea id="coding-task" class="editor" rows="4" placeholder="例如：修复登录接口的测试，并运行相关 pytest">${esc(localStorage.getItem("knowme_coding_task") || "")}</textarea>
      <div class="coding-task-actions"><label class="fld-inline">本次后端
        <select id="coding-default-backend">${options || `<option value="">没有已启用的本地后端</option>`}</select>
      </label><button class="save" onclick="startCodingTask()" ${info.execution_enabled && options ? "" : "disabled"}>发送给 Coding Agent</button></div>
      <div class="meta coding-hint">任务会带上项目目录 <code>${esc(root || "")}</code>，并要求 Coding Agent 使用选中的本机 CLI。</div>
    </div>
    <div class="card">
      <div class="coding-section-title"><b>本机 Coding Agent</b><button class="save ghost" onclick="saveCodingSettings()">保存开关</button></div>
      <label class="coding-execution"><input id="coding-execution" type="checkbox" ${info.execution_enabled ? "checked" : ""}> 允许 Coding Agent 调用本机 CLI</label>
      <div class="coding-backends">${cards || `<div class="empty">没有检测到本机 Coding CLI。</div>`}</div>
      <div class="meta coding-hint">开关只控制 KnowMe 是否允许选择该 CLI，不会保存或读取它们的登录凭证。</div>
    </div>
  </section>`;
}

async function saveCodingSettings(){
  const enabled = {};
  document.querySelectorAll("[data-coding-backend]").forEach(input => { enabled[input.dataset.codingBackend] = input.checked; });
  const select = document.getElementById("coding-default-backend");
  const execution = document.getElementById("coding-execution");
  const result = await postJSON("/api/coding", {action:"save", enabled,
    default_backend:(select && select.value) || ((D.coding && D.coding.settings || {}).default_backend || "pi"),
    execution_enabled:!!(execution && execution.checked)});
  if (!result.ok){ alert(result.error || "保存 Coding Agent 设置失败"); return; }
  D.coding = result;
  render();
}

function startCodingTask(){
  const taskEl = document.getElementById("coding-task");
  const select = document.getElementById("coding-default-backend");
  const task = (taskEl && taskEl.value || "").trim();
  const backend = select && select.value;
  if (!task || !backend) return;
  localStorage.setItem("knowme_coding_task", task);
  localStorage.setItem("knowme_coding_backend", backend);
  const root = (D.workspace && D.workspace.root) || "";
  location.hash = "#agent/coding";
  let attempts = 0;
  const sendWhenReady = () => {
    const input = document.getElementById("dmsg");
    const send = document.getElementById("dsend");
    if (!input || !send){
      if (++attempts < 30) setTimeout(sendWhenReady, 100);
      return;
    }
    input.value = `请在项目 ${root} 中完成下面的编码任务：\n\n${task}\n\n必须调用 delegate_task，并使用 backend="${backend}"。完成后检查 git diff，并运行与改动相关的测试；不要修改 .env，也不要自动提交 Git。`;
    send.click();
  };
  sendWhenReady();
}

// knowme web — inline Memory/SOUL/skill editing actions.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

function editFact(id){
  const row = document.getElementById("fact-"+id); if(!row) return;
  editing = true;
  const cell = row.querySelector(".fc"); const cur = cell.textContent;
  cell.innerHTML = `<textarea class="editor" id="ef-${id}">${cur.replace(/</g,"&lt;")}</textarea>`;
  const act = row.lastElementChild;
  act.innerHTML = `<a class="reveal" onclick="saveFact(${id})">保存</a> · <a class="reveal" onclick="cancelFactEdit()">取消</a>`;
  document.getElementById("ef-"+id).focus();
}
// 「取消」必须走这里，不能写成 onclick="editing=false;refresh()"：那行代码在
// window 上求值，写的是 bootstrap 复制出去的那份 `editing`，而 render() 读的是
// util.js 里的闭包变量（见 static/README.md 的作用域说明）。结果就是点击后
// window.editing 确实变成了 false，页面上的编辑框却一直不消失。
function cancelFactEdit(){
  editing = false;
  refresh();
}
async function saveFact(id){
  const v = document.getElementById("ef-"+id).value.trim();
  await postJSON("/api/memory", {action:"update_fact", id, content:v, agent_id:ACTIVE_AGENT});
  editing = false; refresh();
}
// 「合并」要挑一条目标事实。以前这里弹一个 prompt 让人输目标的事实 ID —— 但事实 ID
// 从来不露在页面上，除了翻数据库没人知道它是几。所以改成：先弹出这张列表，
// 让人直接看着事实本身去挑，界面上只出现主题和正文，ID 一个字都不出现。
// 后端的接口没变，它收的仍然是两个 ID（merge_fact），只是这两个 ID 由行本身带过来。
function mergeFact(sourceId){
  const root = document.getElementById("memory-modal-root");
  const facts = (D && D.facts) || [];
  const src = facts.find(f => f.id === sourceId);
  if (!root || !src) return;
  markEditing();   // 打开期间别让 5 秒轮询把这张列表刷掉（和连接弹窗同一套）
  const others = facts.filter(f => f.id !== sourceId);
  root.innerHTML = `<div class="connmodal-back" onclick="closeMemoryModal()" onkeydown="memoryModalKeydown(event)">
    <section class="connmodal" role="dialog" aria-modal="true" aria-labelledby="memory-modal-title" onclick="event.stopPropagation()">
      <header class="connmodal-head">
        <div class="connmodal-title">
          <h3 id="memory-modal-title">把这一条合并到哪一条？</h3>
          <div class="meta">合并后，上面这条的正文会接到目标的后面，本条不再单独存在。</div>
        </div>
        <button class="connmodal-close" type="button" onclick="closeMemoryModal()" aria-label="关闭">关闭</button>
      </header>
      <div class="memfact src">
        <div class="memfact-sub">${esc(src.subject)}</div>
        <div class="memfact-body">${esc(src.content)}</div>
      </div>
      <div class="memtargets">${ others.map(f =>
        `<button class="memtarget" type="button" onclick="mergeFactInto(${sourceId}, ${f.id})">
          <span class="memfact-sub">${esc(f.subject)}</span>
          <span class="memfact-body">${esc(f.content)}</span>
        </button>`).join("") || `<div class="meta">没有别的事实可以合并。</div>` }</div>
    </section>
  </div>`;
  setTimeout(() => { const b = root.querySelector(".connmodal-close"); if (b) b.focus(); }, 0);
}
// 真正动手的那一步。说的是人话：哪一条并到哪一条、合并之后留下的是什么——
// 因为 store.merge() 确实是「目标的正文接上来源的正文 + 删掉来源那一行」，两件事都要说。
async function mergeFactInto(sourceId, targetId){
  const facts = (D && D.facts) || [];
  const src = facts.find(f => f.id === sourceId);
  const dst = facts.find(f => f.id === targetId);
  if (!src || !dst) return;
  const s = shortSubject(src.subject, 14), t = shortSubject(dst.subject, 14);
  if (!confirm(`把「${s}」合并进「${t}」？\n\n`
    + `合并后留下「${t}」一条，它的正文后面接上「${s}」的正文；`
    + `「${s}」这一条会被删掉。\n此操作无法撤销。`)) return;
  const r = await postJSON("/api/memory", {
    action:"merge_fact", id:sourceId, target_id:targetId, agent_id:ACTIVE_AGENT
  });
  // 失败就把列表留着（多半是内容不对，而不是目标选错了），人可以重挑一条；
  // 成功才关掉，并让页面重新拉一次数据。
  if (!r.ok){ alert(r.error || "合并失败"); return; }
  closeMemoryModal();
  refresh();
}
// 弹窗里的主题名可能很长，塞进 confirm 里一行装不下——按字数截断，末尾省略号。
function shortSubject(s, n){
  s = String(s == null ? "" : s);
  return s.length > n ? s.slice(0, n) + "…" : s;
}
function closeMemoryModal(){
  editing = false;   // 不解开这个，5 秒轮询就被永久冻住了（见 models.js 的 markEditing）
  const root = document.getElementById("memory-modal-root");
  if (root) root.innerHTML = "";
  if (activeView === "memory") render();
}
function memoryModalKeydown(event){
  if (event.key === "Escape") closeMemoryModal();
}
async function delMem(action, id){
  if(!confirm("确定要从记忆中删除吗？")) return;
  await postJSON("/api/memory", {action, id, agent_id:ACTIVE_AGENT});
  refresh();
}
// dirty-state: a Save button stays muted until its editor actually changes
function dirty(btnId){ editing = true; const b = document.getElementById(btnId); if (b) b.disabled = false; }
async function saveSoul(){
  const v = document.getElementById("soul").value;
  const r = await postJSON("/api/memory", {action:"save_soul", content:v});
  document.getElementById("soul-msg").textContent = r.error ? ("错误："+r.error) : "已保存——下一轮对话生效。";
  if (!r.error){ const b=document.getElementById("soul-save"); if(b) b.disabled=true; editing=false; }
}
async function saveSkill(i){
  const ta = document.getElementById("sk-"+i);
  const r = await postJSON("/api/memory", {action:"save_skill", path:ta.dataset.path, content:ta.value});
  document.getElementById("skmsg-"+i).textContent = r.error ? ("错误："+r.error) : "已保存——下一轮对话生效。";
  if (!r.error){ const b=document.getElementById("sksave-"+i); if(b) b.disabled=true; editing=false; }
}

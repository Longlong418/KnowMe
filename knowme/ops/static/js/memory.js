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
async function mergeFact(sourceId){
  const targetId = prompt("把这条事实合并到哪个事实 ID？");
  if (!targetId || !/^\d+$/.test(targetId) || Number(targetId) === Number(sourceId)) return;
  if (!confirm(`确认把事实 #${sourceId} 合并到 #${targetId} 吗？`)) return;
  const r = await postJSON("/api/memory", {
    action:"merge_fact", id:sourceId, target_id:Number(targetId), agent_id:ACTIVE_AGENT
  });
  if (!r.ok) alert(r.error || "合并失败");
  refresh();
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

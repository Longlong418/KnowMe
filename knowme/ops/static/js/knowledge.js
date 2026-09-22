// knowme web — the Knowledge Base (Sapphire-style notes with [[links]]).
// Moved out of main.js, which is the render loop and not a home for app code.
// Classic <script>, shared global scope (no build step, no modules).
// Load order + rules: static/README.md.
//
// This is the human's view, so it shows EVERY Agent's notes at once and labels
// where each one came from. The Agents' own tools stay scoped — a note you see
// here next to a Coding note is still invisible to the Coding agent. Only
// `create` sends an agent_id, because a new note has to be stamped with one.
// The sub-view markup lives in views.js; this file is the interaction.

let currentNoteId = null;
let currentNoteContent = "";

// One key, not one per Agent: the list is the same whichever Agent is active,
// so remembering a different note for each would reopen something at random.
function knowledgeStateKey(){
  return "knowme_kb_note";
}

async function restoreKnowledgeState(){
  if (currentNoteId || !document.getElementById("kb-note-detail")) return;
  const savedId = localStorage.getItem(knowledgeStateKey());
  if (!savedId) return;
  await viewKnowledgeNote(savedId, {remember: false});
}

async function refreshKnowledgeView(){
  // After a write the sidebar has to be rebuilt: the note list and the folder
  // filter are baked into the view markup, and render() only rebuilds when
  // nothing is selected. Clearing the selection is what lets it -- then
  // restoreKnowledgeState() re-opens the note we were just on, re-read from the
  // server, so the detail pane is not left showing the old title either.
  // (Assigning the same hash would not fire hashchange, which is why this calls
  // the data refresh in main.js directly instead of navigating.)
  currentNoteId = null;
  await refresh();
}

function newKnowledgeNote(){
  currentNoteId = null;
  currentNoteContent = "";
  localStorage.removeItem(knowledgeStateKey());
  const detail = document.getElementById("kb-note-detail");
  const welcome = document.getElementById("kb-empty-editor");
  const create = document.getElementById("kb-create-editor");
  if (detail) detail.style.display = "none";
  if (welcome) welcome.style.display = "none";
  if (create) create.style.display = "block";
  ["kb-title", "kb-folder", "kb-content"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  document.getElementById("kb-title")?.focus();
}

async function createKnowledgeNote(){
  const title = document.getElementById("kb-title")?.value?.trim();
  const folder = document.getElementById("kb-folder")?.value?.trim() || "default";
  const content = document.getElementById("kb-content")?.value || "";
  if (!title) return alert("请输入笔记标题");
  try {
    const res = await postJSON("/api/knowledge", {
      action: "create", title, folder, content, agent_id: ACTIVE_AGENT
    });
    if (res.ok) {
      editing = false;
      await refreshKnowledgeView();
    }
    else alert(res.error || "创建笔记失败");
  } catch (error) { alert("创建笔记失败：" + (error.message || error)); }
}

async function viewKnowledgeNote(noteId, options = {}){
  currentNoteId = noteId;
  if (options.remember !== false) localStorage.setItem(knowledgeStateKey(), noteId);
  try {
    const res = await postJSON("/api/knowledge", {
      action: "get", note_id: noteId
    });
    if (!res.ok) return alert(res.error || "笔记不存在");
    const note = res.note || {};
    const detail = document.getElementById("kb-note-detail");
    const welcome = document.getElementById("kb-empty-editor");
    const create = document.getElementById("kb-create-editor");
    if (detail) detail.style.display = "block";
    if (welcome) welcome.style.display = "none";
    if (create) create.style.display = "none";
    document.getElementById("kb-edit-title").value = note.title || "";
    document.getElementById("kb-edit-folder").value = note.folder || "default";
    document.getElementById("kb-edit-content").value = note.content || "";
    currentNoteContent = note.content || "";
    renderKnowledgePreview();
    const links = await postJSON("/api/knowledge", {
      action: "links", note_id: noteId
    });
    renderKnowledgeBacklinks(links.notes || []);
  } catch (error) { alert("打开笔记失败：" + (error.message || error)); }
}

async function saveKnowledgeNote(){
  const id = currentNoteId;
  const content = document.getElementById("kb-edit-content")?.value || "";
  const title = document.getElementById("kb-edit-title")?.value?.trim();
  const folder = document.getElementById("kb-edit-folder")?.value?.trim() || "default";
  if (!id) return;
  try {
    const res = await postJSON("/api/knowledge", {
      action: "update", note_id: id, content, title, folder
    });
    if (res.ok) {
      editing = false;
      await refreshKnowledgeView();
    }
    else alert(res.error || "保存笔记失败");
  } catch (error) { alert("保存笔记失败：" + (error.message || error)); }
}

async function deleteKnowledgeNote(){
  if (!currentNoteId) return;
  if (!confirm("确定删除这条笔记吗？此操作不可撤销。")) return;
  try {
    const res = await postJSON("/api/knowledge", {
      action: "delete", note_id: currentNoteId
    });
    if (!res.ok) return alert(res.error || "删除笔记失败");
    editing = false;
    closeKnowledgeDetail();
    await refreshKnowledgeView();
  } catch (error) { alert("删除笔记失败：" + (error.message || error)); }
}

function closeKnowledgeDetail(){
  currentNoteId = null;
  currentNoteContent = "";
  localStorage.removeItem(knowledgeStateKey());
  const detail = document.getElementById("kb-note-detail");
  const welcome = document.getElementById("kb-empty-editor");
  const create = document.getElementById("kb-create-editor");
  if (detail) detail.style.display = "none";
  if (welcome) welcome.style.display = "none";
  if (create) create.style.display = "block";
}

function renderKnowledgePreview(){
  const content = document.getElementById("kb-edit-content")?.value || "";
  const html = renderMarkdown(content).replace(/\[\[([^\]]+)\]\]/g, (_, title) => {
    const clean = title.trim();
    return `<a class="kb-link" href="#knowledge" onclick="openKnowledgeByTitle('${encodeURIComponent(clean)}');return false">[[${esc(clean)}]]</a>`;
  });
  const preview = document.getElementById("kb-preview");
  if (preview) preview.innerHTML = html || '<span class="meta">预览会显示在这里。</span>';
}

function renderKnowledgeBacklinks(notes){
  const el = document.getElementById("kb-links");
  if (!el) return;
  el.innerHTML = notes.length
    ? notes.map(n => `<a class="kb-backlink" href="#knowledge" onclick="viewKnowledgeNote('${esc(n.id)}');return false">${esc(n.title)}</a>`).join("")
    : '<span class="meta">还没有其他笔记链接到这里。</span>';
}

async function openKnowledgeByTitle(encodedTitle){
  const title = decodeURIComponent(encodedTitle || "");
  try {
    const res = await postJSON("/api/knowledge", {
      action: "search", query: title
    });
    const note = (res.notes || []).find(n => n.title === title) || res.notes?.[0];
    if (note) viewKnowledgeNote(note.id);
    else alert(`没有找到笔记「${title}」`);
  } catch (error) { alert("打开链接失败：" + (error.message || error)); }
}

function searchKnowledgeNotes(){
  const query = (document.getElementById("kb-search")?.value || "").trim().toLowerCase();
  let visible = 0;
  document.querySelectorAll(".kb-note-card").forEach(card => {
    const match = !query || (card.dataset.search || "").includes(query);
    card.style.display = match ? "block" : "none";
    if (match) visible++;
  });
  const empty = document.getElementById("kb-search-empty");
  if (empty) empty.style.display = visible ? "none" : "block";
}

async function filterKnowledgeNotes(){
  const folder = document.getElementById("kb-folder-filter")?.value || "";
  if (folder) localStorage.setItem("knowme_kb_folder", folder);
  else localStorage.removeItem("knowme_kb_folder");
  // 文件夹筛选是烘进视图标记里的（views.js 从 localStorage 读到它才过滤），所以
  // 换文件夹必须让侧栏重建一次。原来这里写的是 location.hash = "#knowledge"，
  // 而当前 hash 已经是 #knowledge —— 给同一个 hash 赋值是空操作，不触发
  // hashchange，于是点击后要等下一次 5 秒轮询才生效，看起来就是「点了没反应」。
  // refreshKnowledgeView() 就是为这个场景写的（见它的注释）。
  await refreshKnowledgeView();
}

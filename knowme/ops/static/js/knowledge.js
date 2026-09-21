// knowme dashboard — the Knowledge Base (Sapphire-style notes with [[links]]).
// Moved out of main.js, which is the render loop and not a home for app code.
// Classic <script>, shared global scope (no build step, no modules).
// Load order + rules: static/README.md.
//
// Every call carries agent_id: notes are agent-scoped on the server, so a note
// written while the Learning agent is active is not visible to Coding. The
// sub-view markup lives in views.js; this file is the interaction.

let currentNoteId = null;
let currentNoteContent = "";

function newKnowledgeNote(){
  currentNoteId = null;
  currentNoteContent = "";
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
    if (res.ok) { editing = false; location.hash = "#knowledge"; }
    else alert(res.error || "创建笔记失败");
  } catch (error) { alert("创建笔记失败：" + (error.message || error)); }
}

async function viewKnowledgeNote(noteId){
  currentNoteId = noteId;
  try {
    const res = await postJSON("/api/knowledge", {
      action: "get", note_id: noteId, agent_id: ACTIVE_AGENT
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
      action: "links", note_id: noteId, agent_id: ACTIVE_AGENT
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
      action: "update", note_id: id, content, title, folder, agent_id: ACTIVE_AGENT
    });
    if (res.ok) { editing = false; location.hash = "#knowledge"; }
    else alert(res.error || "保存笔记失败");
  } catch (error) { alert("保存笔记失败：" + (error.message || error)); }
}

async function deleteKnowledgeNote(){
  if (!currentNoteId) return;
  if (!confirm("确定删除这条笔记吗？此操作不可撤销。")) return;
  try {
    const res = await postJSON("/api/knowledge", {
      action: "delete", note_id: currentNoteId, agent_id: ACTIVE_AGENT
    });
    if (!res.ok) return alert(res.error || "删除笔记失败");
    editing = false;
    closeKnowledgeDetail();
    location.hash = "#knowledge";
  } catch (error) { alert("删除笔记失败：" + (error.message || error)); }
}

function closeKnowledgeDetail(){
  currentNoteId = null;
  currentNoteContent = "";
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
      action: "search", query: title, agent_id: ACTIVE_AGENT
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

function filterKnowledgeNotes(){
  const folder = document.getElementById("kb-folder-filter")?.value || "";
  if (folder) localStorage.setItem("knowme_kb_folder", folder);
  else localStorage.removeItem("knowme_kb_folder");
  location.hash = "#knowledge"; // Will refresh and filter
}

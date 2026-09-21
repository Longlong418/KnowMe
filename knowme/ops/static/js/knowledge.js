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

async function createKnowledgeNote(){
  const title = document.getElementById("kb-title")?.value?.trim();
  const folder = document.getElementById("kb-folder")?.value?.trim() || "default";
  const content = document.getElementById("kb-content")?.value || "";
  if (!title) return alert("请输入笔记标题");
  const res = await postJSON("/api/knowledge", {
    action: "create", title, folder, content, agent_id: ACTIVE_AGENT
  });
  if (res.ok) location.hash = "#knowledge";
}

async function viewKnowledgeNote(noteId){
  currentNoteId = noteId;
  const res = await postJSON("/api/knowledge", {
    action: "get", note_id: noteId, agent_id: ACTIVE_AGENT
  });
  if (res.ok){
    document.getElementById("kb-note-detail").style.display = "block";
    document.getElementById("kb-edit-title").value = res.note?.title || "";
    document.getElementById("kb-edit-content").value = res.note?.content || "";
    renderKnowledgePreview();
  }
}

async function saveKnowledgeNote(){
  const id = currentNoteId;
  const content = document.getElementById("kb-edit-content")?.value || "";
  const title = document.getElementById("kb-edit-title")?.value?.trim();
  if (!id) return;
  const res = await postJSON("/api/knowledge", {
    action: "update", note_id: id, content, title, agent_id: ACTIVE_AGENT
  });
  if (res.ok) location.hash = "#knowledge";
}

async function deleteKnowledgeNote(){
  if (!currentNoteId) return;
  if (!confirm("确定删除这条笔记吗？此操作不可撤销。")) return;
  const res = await postJSON("/api/knowledge", {
    action: "delete", note_id: currentNoteId, agent_id: ACTIVE_AGENT
  });
  closeKnowledgeDetail();
  location.hash = "#knowledge";
}

function closeKnowledgeDetail(){
  currentNoteId = null;
  currentNoteContent = "";
  document.getElementById("kb-note-detail").style.display = "none";
}

function renderKnowledgePreview(){
  const content = document.getElementById("kb-edit-content")?.value || "";
  const safe = esc(content);
  const html = safe.replace(/\[\[([^\]]+)\]\]/g,
    '<a href="#knowledge" style="color:var(--accent)">[$1]</a>');
  document.getElementById("kb-preview").innerHTML = "<pre>" + html + "</pre>";
}

function filterKnowledgeNotes(){
  const folder = document.getElementById("kb-folder-filter")?.value || "";
  if (folder) localStorage.setItem("knowme_kb_folder", folder);
  else localStorage.removeItem("knowme_kb_folder");
  location.hash = "#knowledge"; // Will refresh and filter
}

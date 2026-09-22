"""The Knowledge base must not lose the note you are writing.

Two rules, both of which the 5 second poll used to break -- and both of which
are invisible in a screenshot, because they are about what the page keeps when
it redraws itself:

  1. A poll must not rebuild the view while a note is open (your draft, your
     cursor, the note you were reading all live in that DOM).
  2. A write (create / save / delete) MUST rebuild it -- the note list and the
     folder filter are baked into the view markup, so without a rebuild the
     sidebar keeps showing the old title.

Together those are why the view is rebuilt after a write with the selection
cleared, and why the selected note is remembered in localStorage: the rebuild
re-opens it from the server, and a full page reload comes back to it too.

Like the Reader's lock, this runs the REAL knowledge.js against a DOM stub in
node.  The behaviour under test is control flow (which branch render() takes,
what a write triggers), which a headless browser would prove more slowly and
less precisely.  node is a hard requirement of this file, so skip without it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[2]
      / "knowme" / "ops" / "static" / "js")
MAIN_JS = JS / "main.js"
KNOWLEDGE_JS = JS / "knowledge.js"
VIEWS_JS = JS / "views.js"

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");       // main.js: the loop
const kbSrc = fs.readFileSync(process.argv[3], "utf8");     // knowledge.js: the app
const viewsSrc = fs.readFileSync(process.argv[4], "utf8");  // views.js: the markup
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

// node has no localStorage and no DOM. Both are real browser APIs the app uses
// to keep state the server does not have; the stubs are the smallest version of
// each that lets the real code run unchanged.
const storage = {};
const localStorage = {
  getItem: k => (k in storage ? storage[k] : null),
  setItem: (k, v) => { storage[k] = String(v); },
  removeItem: k => { delete storage[k]; },
};

const pendingTests = [];
pendingTests.push((function testKnowledgePane() {
  const els = {};
  function el(id) {
    return els[id] = els[id] || { id, value: "", innerHTML: "", textContent: "",
                                  style: {}, dataset: {}, focus() {} };
  }
  const document = {
    getElementById: id => el(id),
    querySelectorAll: () => [],
    querySelector: () => ({ scrollTop: 0 }),
  };
  const location = { hash: "#knowledge" };
  const esc = s => String(s);
  const renderMarkdown = t => String(t);
  const alert = m => { throw new Error("unexpected alert: " + m); };
  const confirm = () => true;
  let ACTIVE_AGENT = "learning";
  let editing = false;

  // The server, recorded. `gets` is the interesting one: it is how the test
  // knows which note the pane went and opened.
  const calls = [], gets = [];
  const NOTES = { n1: { id: "n1", title: "A", folder: "default", content: "正文" } };
  const postJSON = async (url, body) => {
    calls.push(body);
    if (body.action === "get") { gets.push(body.note_id); return { ok: true, note: NOTES[body.note_id] }; }
    if (body.action === "links") return { ok: true, notes: [] };
    return { ok: true };
  };

  // main.js's refresh() is the data poll: fetch /api/data, then render().
  let refreshes = 0;
  async function refresh(){ refreshes++; render(); }

  // render() is sliced out of main.js by shape, like the Reader's lock does.
  // The slice starts at setCount() because render() calls it: the counters moved
  // behind that helper when one of them was found to be able to crash the loop.
  const renderFn = src.slice(src.indexOf("function setCount("),
                             src.indexOf("\nlet lastFetch"));
  let activeView = null, activeSub = null, animating = false;
  let built = 0;
  const VIEWS = { knowledge: () => { built++; return "<kb>"; } };
  const TITLES = { knowledge: "知识库" };
  const D = { provider: "p", model: "m", chat_log: [], stats: { turns: 0, tool_errors: 0 },
              graph: { stats: { quick: 0, full: 0 } }, facts: [], episodes: [],
              calendar: [], outbox: [], db: { all_tables: [] }, home: "H",
              agents: [], current_sessions: {} };
  function selectAgent() {}
  function syncAgentChrome() {}
  function restoreReaderState() {}
  function wireChat() {}
  // ONE eval: the browser gives main.js and knowledge.js the same scope, and
  // render()'s knowledge branch reads currentNoteId, which knowledge.js declares
  // with `let`. Two separate evals would give it two private scopes and render()
  // would die on a ReferenceError that can never happen in the real page.
  eval(renderFn + "\n" + kbSrc);

  // render() calls restoreKnowledgeState() without awaiting it (it is a paint
  // detail, not something the caller waits on), so let the microtasks drain.
  const flush = () => new Promise(resolve => setTimeout(resolve, 0));
  const lastGet = () => gets[gets.length - 1];

  return (async () => {
    // --- a page load comes back to the note you were last in ------------------
    storage["knowme_kb_note"] = "n1";
    activeView = "knowledge";
    render();
    await flush();
    assert(lastGet() === "n1", "a page load re-opens the note you were last in");
    assert(el("kb-note-detail").style.display === "block",
           "and shows the detail pane, not the welcome panel");

    // --- the poll must not touch the view while that note is open -------------
    const builtBefore = built;
    render(); render();
    await flush();
    assert(built === builtBefore,
           "a poll does NOT rebuild the view (the draft and the cursor survive)");
    assert(gets.length === 1, "a poll does not re-fetch the note it already has");

    // --- saving rebuilds the list, and re-opens the note ---------------------
    // The title lives in the sidebar markup, so a save that only re-fetched the
    // data would leave the old title on screen until you navigated away.
    el("kb-edit-content").value = "改过的正文";
    await saveKnowledgeNote();
    await flush();
    assert(refreshes === 1, "saving asks main.js to refresh the data");
    assert(built > builtBefore, "saving rebuilds the view, so the sidebar is not stale");
    assert(lastGet() === "n1", "and re-opens the note that was being edited");
    assert(el("kb-edit-content").value === "正文", "with the content the server now has");

    // --- 新建: the create form, and no remembered note to jump back to --------
    newKnowledgeNote();
    assert(el("kb-create-editor").style.display === "block", "新建 shows the create form");
    assert(el("kb-empty-editor").style.display === "none", "and hides the welcome panel");
    assert(!("knowme_kb_note" in storage), "新建 forgets the remembered note");

    // --- creating a note must not re-open the one you were in before ---------
    const getsBefore = gets.length;
    el("kb-title").value = "B";
    el("kb-content").value = "新笔记";
    await createKnowledgeNote();
    await flush();
    assert(built > builtBefore + 1, "creating a note rebuilds the list it should appear in");
    assert(gets.length === getsBefore, "creating does not re-open an older note");
    assert(calls.some(c => c.action === "create" && c.title === "B" && c.agent_id === "learning"),
           "the create carries the note and the active agent");

    // --- deleting forgets the note, so a reload cannot chase a dead id -------
    await viewKnowledgeNote("n1");
    assert(storage["knowme_kb_note"] === "n1", "opening a note remembers it");
    await deleteKnowledgeNote();
    assert(!("knowme_kb_note" in storage), "deleting it forgets it");
    assert(el("kb-note-detail").style.display === "none", "and closes the detail pane");
  })();
})());

// ---- the list shows every Agent's notes, and says who wrote each ------------
// The regression: VIEWS.knowledge filtered the list to the ACTIVE Agent, so the
// knowledge base looked EMPTY whenever you were on an Agent that had not
// written a note yet. Notes are agent-stamped, so for this user that was most
// Agents — including the default one, which is the view you land on.
(function testEveryAgentIsShown() {
  const from = viewsSrc.indexOf("knowledge(d){");
  // The end marker is the "}" that CLOSES the method, so it has to be included
  // (+4 skips back over "\n  }") or the eval gets an unclosed function body.
  const to = viewsSrc.indexOf("\n  },\n  settings(d){");
  assert(from > 0 && to > from, "VIEWS.knowledge is still findable in views.js");
  const view = eval("({" + viewsSrc.slice(from, to + 4) + "})").knowledge;
  const esc = s => String(s);                         // the view escapes its own output
  // Declared because the view may (and once did) filter on it. Without it, a
  // reintroduced `=== ACTIVE_AGENT` filter would blow up the harness with a
  // ReferenceError instead of failing the claims below with a readable message.
  let ACTIVE_AGENT = "learning";

  const html = view({ knowledge_info: {
    notes: [
      { id: "n1", title: "Reader note", folder: "papers", content: "a", agent_id: "reader" },
      { id: "n2", title: "Learning note", folder: "notes", content: "b", agent_id: "learning" },
    ],
    all_folders: ["papers", "notes"],
  } });
  assert(html.includes("Reader note") && html.includes("Learning note"),
         "every Agent's notes are listed, not only the active Agent's");
  assert(html.includes(">reader<") && html.includes(">learning<"),
         "every card is labelled with the Agent that wrote it");
  assert(html.includes("papers") && html.includes("notes"),
         "the folder filter offers folders from every Agent's notes");
  assert(html.includes("2 条"), "the count is the number of notes actually shown");

  // A note with no agent stamp (an old row) must not vanish: it reads "default".
  const legacy = view({ knowledge_info: { notes: [
    { id: "n3", title: "Old note", folder: "default", content: "", agent_id: "" }] } });
  assert(legacy.includes("Old note"), "a note with no agent stamp is still listed");
  assert(legacy.includes(">default<"), "and is labelled with the default Agent");

  assert(view({ knowledge_info: {} }).includes("还没有笔记"),
         "a knowledge base with no notes at all still says so");
})();

// ---- the markup the app is written against ----------------------------------
// knowledge.js looks every one of these ids up by name; dropping one from
// views.js breaks a feature with no error anywhere, so they are a contract.
(function testMarkupContract() {
  for (const id of ["kb-note-detail", "kb-empty-editor", "kb-create-editor",
                    "kb-title", "kb-folder", "kb-content", "kb-edit-title",
                    "kb-edit-folder", "kb-edit-content", "kb-preview", "kb-links",
                    "kb-search", "kb-search-empty", "kb-folder-filter"])
    assert(viewsSrc.includes('id="' + id + '"'), "views.js provides #" + id);
  assert(/<div id="kb-create-editor"[^>]*display:none/.test(viewsSrc),
         "the create form starts hidden (else it shows above the welcome panel)");
})();

Promise.all(pendingTests)
  .then(() => process.exit(failures ? 1 : 0))
  .catch(err => { console.log("FAIL  the harness threw: " + (err && err.stack || err));
                  process.exit(1); });
"""


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node is not installed — the frontend has no other test runner")
    return exe


def test_the_knowledge_view_keeps_and_rebuilds_the_right_things(node, tmp_path):
    """render()'s knowledge branch, and what a write does to the open note."""
    for path in (MAIN_JS, KNOWLEDGE_JS, VIEWS_JS):
        assert path.exists(), f"missing frontend source: {path}"
    harness = tmp_path / "knowledge_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(MAIN_JS), str(KNOWLEDGE_JS), str(VIEWS_JS)],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=60)

    # Show the claims either way: a bare "exit 1" tells the user nothing.
    print(proc.stdout)
    assert proc.returncode == 0, f"frontend checks failed:\n{proc.stdout}\n{proc.stderr}"

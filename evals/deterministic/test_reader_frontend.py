"""The Reader must survive the dashboard's 5 second refresh.

Regression lock for a live bug: after picking a file the page alerted
"请选择一个文件", intermittently.  render() rebuilt the whole #view innerHTML on
every poll, which destroyed the <input type="file"> while the native picker was
still open; readerLoad() then re-queried the DOM by id and found the fresh,
empty replacement -- so the file the user had just chosen was nowhere.

The intermittency was the tell: it only happened when a poll landed inside the
few seconds the picker was open.

These tests run the REAL frontend source in node against a minimal DOM stub.
That is deliberate -- the bug lives in control flow (which branch render()
takes, what readerLoad() reads), not in how anything renders, so a headless
browser would prove less for a lot more machinery.  node is a hard requirement
of this file and a soft one elsewhere, so skip rather than fail without it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[2]
      / "knowme" / "ops" / "static" / "js")
MAIN_JS = JS / "main.js"
# The Reader moved out of main.js into its own file when it grew a document
# library and a PDF renderer. render() stayed put — the harness slices it out of
# main.js by shape — but the reader's own functions are read from reader.js.
READER_JS = JS / "reader.js"

# The harness reads main.js, lifts out the functions under test, and drives them
# on stubs.  It prints one PASS/FAIL line per claim and exits non-zero if any
# failed, so a failure here shows the user which claim broke, in English.
HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");        // main.js: the loop
const readerSrc = fs.readFileSync(process.argv[3], "utf8");  // reader.js: the app
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

// ---- render(): does the 5s poll rebuild the Reader's DOM? -------------------
(function testRender() {
  const renderFn = src.slice(src.indexOf("function setCount("),
                             src.indexOf("\nlet lastFetch"));
  let activeView = null, activeSub = null, animating = false;
  const VIEWS = { reader: () => "<reader>", loop: () => "<loop>" };
  const TITLES = { reader: "阅读器" };
  const D = { provider: "p", model: "m", chat_log: [], stats: { turns: 0, tool_errors: 0 },
              graph: { stats: { quick: 0, full: 0 } }, facts: [], episodes: [],
              calendar: [], outbox: [], db: { all_tables: [] }, home: "H",
              agents: [], current_sessions: {} };
  const els = {};
  const document = {
    querySelectorAll: () => [],
    querySelector: () => ({ scrollTop: 0 }),
    getElementById: id => (els[id] = els[id] || { textContent: "", innerHTML: "", style: {} }),
  };
  const window = { addEventListener() {}, getSelection: () => ({ toString: () => "" }) };
  const esc = s => String(s);
  const location = { hash: "#reader" };
  let restoreCalls = 0, wireCalls = 0;
  function restoreReaderState() { restoreCalls++; }
  function wireChat() { wireCalls++; }
  function syncAgentChrome() {}
  eval(renderFn);

  // The user is already on the Reader, with a document loaded. A poll arrives.
  activeView = "reader"; activeSub = null;
  els["view"] = { innerHTML: "<rendered>", style: {} };
  render();
  assert(els["view"].innerHTML === "<rendered>",
         "a poll does NOT rebuild the reader (the file input survives)");
  assert(restoreCalls === 1,
         "a poll still calls restoreReaderState() (the document stays painted)");
  assert(wireCalls === 1,
         "a poll still wires the Reader's ask panel (its composer is generated markup)");

  render(); render();
  assert(els["view"].innerHTML === "<rendered>", "repeated polls still do not rebuild");

  // Navigating away and back must still rebuild -- the guard is about the
  // poll, not about refusing to ever redraw this view.
  location.hash = "#loop";
  render();
  assert(els["view"].innerHTML === "<loop>", "navigating away rebuilds the target view");
  location.hash = "#reader";
  render();
  assert(els["view"].innerHTML === "<reader>", "navigating back rebuilds the reader");
})();

// ---- the Reader: repaint only when the document or its length changed -------
// Runs the REAL reader.js against a DOM stub rather than slicing a function out
// of it. The reader now collaborates with the library list, the chunker and a
// PDF branch, so driving it end to end proves more than lifting one function
// would -- and it does not break every time a helper is renamed.
const pendingTests = [];
pendingTests.push((function testReaderPane() {
  let els, paints, fetches = 0;
  function watch(el) {
    let v = el.innerHTML;
    Object.defineProperty(el, "innerHTML", { get: () => v, set(nv) { v = nv; paints++; } });
  }
  function reset() {
    els = {};
    for (const id of ["rc-content", "rc-title", "rc-meta", "rc-selection", "rc-text",
                      "rc-library", "rc-search", "rc-file-input", "rc-url-input"])
      els[id] = { id, innerHTML: "", dataset: {}, style: {}, textContent: "", value: "",
                  classList: { add() {}, remove() {}, toggle() {} },
                  appendChild() {}, insertAdjacentHTML() {} };
    // the library list is watched too: it must not flicker on the poll either
    paints = 0;
    watch(els["rc-content"]); watch(els["rc-library"]);
  }
  const VIEWS = {};
  const document = {
    getElementById: id => els[id] || null,
    createElement: () => ({ style: {}, append() {}, classList: { add() {} } }),
  };
  const window = { getSelection: () => ({ toString: () => "" }), devicePixelRatio: 1 };
  // node has no localStorage. The reader keeps the open document's id there so a
  // full page refresh comes back to the same document -- and readerOpen() writes
  // to it unconditionally, so without a stub the whole harness dies on a
  // ReferenceError instead of reporting on the logic under test.
  const storage = {};
  const localStorage = {
    getItem: k => (k in storage ? storage[k] : null),
    setItem: (k, v) => { storage[k] = String(v); },
    removeItem: k => { delete storage[k]; },
  };
  const esc = s => String(s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // markdown rendering is util.js's job and is tested by using the app; here it
  // only has to be a pure function of the text so the stamps can be compared.
  const renderMarkdown = t => `<md>${String(t)}</md>`;
  const ACTIVE_AGENT = "default", SESSION = "s1";
  // main.js owns the real render(); here it only has to report that it ran, so
  // toggleAskPanel() can be checked for asking for one. NOT a const: clearing
  // activeView is exactly how the reader makes render() rebuild the view.
  let activeView = "reader", renderCalls = 0;
  function render() { renderCalls++; }
  const alert = m => { throw new Error("unexpected alert: " + m); };
  const confirm = () => true;
  const D = { current_sessions: {} };
  let opened = null;
  const postJSON = async (url, body) => {
    fetches++;
    if (body.action === "list") return { ok: true, documents: [
      { id: "d1", title: "alpha", kind: "markdown", chars: 22, created_at: "2026-09-21" },
      { id: "d2", title: "beta", kind: "text", chars: 5, created_at: "2026-09-21" }] };
    if (body.action === "open") {
      opened = body.doc_id;
      return { ok: true, document: { id: body.doc_id, title: "alpha", kind: "markdown",
                                     chars: 22, created_at: "2026-09-21" },
               text: "para one\n\npara two", has_more: false,
               file_url: "/api/library/file?id=" + body.doc_id };
    }
    return { ok: true };
  };
  // Which document row the library list is lighting up, read out of its markup
  // ("class=rdoc on" is the only place the open document shows up in the list).
  const lit = html => (String(html).match(
    /class="rdoc on"\s+onclick="readerOpen\('([^']+)'\)/) || [])[1] || null;
  eval(readerSrc);

  reset();
  return (async () => {
    // 收起提问 must actually close the panel. The panel is part of the VIEW
    // markup, so it only follows by being rebuilt — and the poll guard
    // (`view === "reader" && !subChanged`) skips exactly that rebuild, so the
    // click used to do nothing at all. Clearing activeView is what asks for it.
    assert(askPanelOpen(), "the ask panel is open by default");
    toggleAskPanel();
    assert(!askPanelOpen(), "收起提问 closes the panel and remembers the choice");
    assert(activeView === null && renderCalls === 1,
           "…by asking render() for a rebuild the poll guard would otherwise skip");
    toggleAskPanel();
    assert(askPanelOpen() && renderCalls === 2, "clicking it again reopens the panel");

    await readerOpen("d1");
    assert(opened === "d1", "opening a document asks the server for it by id");
    assert(els["rc-content"].innerHTML.includes("para one"),
           "the document is painted from its extracted text");
    assert(els["rc-title"].textContent === "alpha", "the pane header names the document");
    assert(storage["knowme_reader_open_doc"] === "d1",
           "opening a document remembers its id for the next page load");
    await restoreReaderState();          // settle the one-time library fetch

    // The one that matters: a poll must not touch innerHTML, or a text selection
    // made with the mouse is dropped mid-drag. This is the ORIGINAL bug, and the
    // reason the reader has a stamp at all.
    const painted = paints, fetched = fetches;
    await restoreReaderState();
    await restoreReaderState();
    assert(paints === painted, "a poll repaints NOTHING (a live selection survives)");
    assert(fetches === fetched, "a poll does not re-fetch the document it already has");

    // A rebuilt view is a FRESH element with no stamp -- it MUST repaint, or the
    // document would silently go blank (which is how the last reader regressed).
    // Note the elements are genuinely new: restoring the old ones would keep
    // their stamps and quietly test nothing.
    reset();
    await restoreReaderState();
    assert(paints > 0 && els["rc-content"].innerHTML.includes("para one"),
           "a rebuilt view (fresh element, no stamp) repaints the document");

    // The library's row highlight has to follow the document you open. It did
    // not: the list's stamp was (query | row ids), and opening a DIFFERENT
    // document changes neither, so the early return kept the previous list with
    // the old row still lit — you click a second document and the highlight
    // stays on the first one.
    assert(lit(els["rc-library"].innerHTML) === "d1",
           "the library highlights the document that is open");

    // A different document, and the same document made longer, both repaint:
    // that second one is the 继续加载 button, and a stamp keyed on the id alone
    // would silently swallow it.
    await readerOpen("d2");
    assert(els["rc-content"].innerHTML.includes("para one"), "a different document repaints");
    assert(lit(els["rc-library"].innerHTML) === "d2",
           "and the highlight follows to the document you just opened");
    const before = paints;
    readerMore();
    assert(paints > before, "continuing a long document repaints it");

    // Deleting the open document empties the pane -- and clears the stamp, so
    // opening the SAME id again later still paints (a stale stamp would leave
    // the pane blank for a document that had been re-added).
    await readerDelete("d2");
    assert(els["rc-content"].innerHTML.includes("选一份文档"), "the empty state is shown");
    assert(els["rc-content"].dataset.stamp === "", "the empty state clears the stamp");
    assert(!("knowme_reader_open_doc" in storage),
           "deleting the open document forgets it (a reload must not chase a dead id)");

    // A full page refresh: brand-new module state, same localStorage. The pane
    // has to come back to the document being read rather than the empty state --
    // which is the whole point of the id in localStorage, and the reason the
    // restore is allowed to call readerOpen() from a poll's code path.
    await readerOpen("d1");
    eval(readerSrc);                    // reload: fresh currentDoc/libraryDocs
    reset();                            // ...and fresh elements, as a reload gives
    await restoreReaderState();
    assert(opened === "d1", "a reload re-opens the document you were reading");
    assert(els["rc-content"].innerHTML.includes("para one"),
           "and paints it, instead of coming up empty");
  })();
})());

// ---- the PDF pane: ctrl+wheel zooms the page, not the window ----------------
// pdf.js itself is stubbed (getDocument/TextLayer/canvas), because the claims
// here are arithmetic and control flow: does the wheel reach preventDefault, do
// the pages come back bigger, is the listener attached once, do the pages you
// loaded with 继续加载 survive. A real canvas would prove none of them.
pendingTests.push((function testPdfZoom() {
  const els = {};
  let prevented = 0;

  function stubEl(id) {
    const classes = new Set();
    return {
      id, dataset: {}, style: {}, children: [], value: "", textContent: "",
      clientWidth: 800, scrollTop: 0, listeners: {}, _html: "",
      classList: {
        add: c => classes.add(c), remove: c => classes.delete(c),
        toggle: (c, on) => (on === undefined
          ? (classes.has(c) ? classes.delete(c) : classes.add(c))
          : (on ? classes.add(c) : classes.delete(c))),
      },
      addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
      querySelector: () => null,
      querySelectorAll(sel) { return sel === ".pdf-page" ? this.children : []; },
      // a page knows where it sits, so the zoom has something to anchor to
      getBoundingClientRect() {
        return { top: (this.index || 0) * 900, height: parseFloat(this.style.height) || 800 };
      },
      appendChild(node) { node.index = this.children.length; this.children.push(node); return node; },
      append() {},
      insertAdjacentHTML() {},
      get innerHTML() { return this._html; },
      set innerHTML(v) { this._html = v; this.children = []; },
    };
  }
  for (const id of ["rc-content", "rc-title", "rc-meta", "rc-selection", "rc-text",
                    "rc-library", "rc-search", "rc-file-input", "rc-url-input"])
    els[id] = stubEl(id);

  const document = {
    getElementById: id => els[id] || null,
    querySelectorAll: () => [],
    documentElement: { scrollTop: 0 },
    // a page wrap needs a rect like a real one, or the zoom has nothing to
    // anchor to
    createElement: tag => (tag === "canvas"
      ? { style: {}, getContext: () => ({}), width: 0, height: 0 }
      : { className: "", style: {}, append() {},
          getBoundingClientRect() { return { top: (this.index || 0) * 900,
                                            height: parseFloat(this.style.height) || 800 }; } }),
  };
  const getComputedStyle = () => ({ overflowY: "visible" });
  const window = { devicePixelRatio: 1, getSelection: () => ({ toString: () => "" }),
                   addEventListener() {} };
  const storage = {};
  const localStorage = { getItem: k => (k in storage ? storage[k] : null),
                         setItem: (k, v) => { storage[k] = String(v); },
                         removeItem: k => { delete storage[k]; } };
  const esc = s => String(s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const renderMarkdown = t => String(t);
  const ACTIVE_AGENT = "default", SESSION = "s1", activeView = "reader";
  const VIEWS = {};                    // reader.js assigns VIEWS.reader at load
  const alert = m => { throw new Error("unexpected alert: " + m); };
  const confirm = () => true;
  const D = { current_sessions: {} };
  const postJSON = async (url, body) => {
    if (body.action === "list") return { ok: true, documents: [
      { id: "p1", title: "paper", kind: "pdf", chars: 10, created_at: "2026-09-21" }] };
    if (body.action === "open") return { ok: true, text: "one two", has_more: false,
      document: { id: body.doc_id, title: "paper", kind: "pdf", chars: 10,
                  created_at: "2026-09-21" },
      file_url: "/api/library/file?id=" + body.doc_id };
    return { ok: true };
  };
  // Drawing is promise-only once pdf.js is stubbed, so ONE macrotask boundary
  // drains the whole paint; only the zoom's debounce needs real time.
  const settle = ms => new Promise(r => setTimeout(r, ms === undefined ? 0 : ms));
  eval(readerSrc);

  // The two things pdf.js would provide: a document and a text layer.
  const fakePage = () => ({
    getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
    render: () => ({ promise: Promise.resolve() }),
    getTextContent: () => Promise.resolve({ items: [] }),
  });
  class FakeTextLayer { constructor() {} render() { return Promise.resolve(); } }
  loadPdfLib = async () => ({
    getDocument: () => ({ promise: Promise.resolve({
      numPages: 12, getPage: async () => fakePage() }) }),
    TextLayer: FakeTextLayer,
    GlobalWorkerOptions: {},
  });
  pdfUnsupportedReason = () => "";        // node's Uint8Array is not the contract here

  const pageWidth = () => parseFloat(els["rc-content"].children[0].style.width);
  const wheel = (ev) => els["rc-content"].listeners.wheel[0]({
    preventDefault: () => { prevented++; }, clientY: 0, ...ev });

  return (async () => {
    await readerOpen("p1");
    await settle();
    assert(els["rc-content"].children.length === 8,
           "a PDF paints its first batch of pages");
    await pdfMore();
    assert(els["rc-content"].children.length === 12, "继续加载 paints the rest of them");

    wirePdfZoom(els["rc-content"]);
    wirePdfZoom(els["rc-content"]);
    assert(els["rc-content"].listeners.wheel.length === 1,
           "the wheel listener is attached once, not once per repaint");

    const before = pageWidth();
    wheel({ ctrlKey: true, deltaY: -1 });
    await settle(260);                     // the redraw is debounced; let it run
    assert(prevented === 1,
           "ctrl+wheel does NOT zoom the browser page (preventDefault is called)");
    assert(pageWidth() > before, "ctrl+wheel zooms the PDF itself");
    assert(els["rc-content"].children.length === 12,
           "the pages you already loaded survive the zoom");

    wheel({ ctrlKey: false, deltaY: -1 });
    assert(prevented === 1, "a plain wheel is still the browser's own scrolling");

    for (let i = 0; i < 20; i++) wheel({ ctrlKey: true, deltaY: -1 });
    await settle(260);
    const capped = pageWidth();
    wheel({ ctrlKey: true, deltaY: -1 });
    await settle(260);
    assert(pageWidth() === capped, "the zoom stops at its maximum instead of growing forever");
    assert(capped > before, "...and it did grow before it stopped");
  })();
})());

// ---- readerLoad(): reads the file off the event target ----------------------
(function testReaderLoad() {
  assert(/function readerLoad\(input\)/.test(readerSrc),
         "readerLoad takes the element the change event fired on");
  assert(/input = input \|\| document\.getElementById\("rc-file-input"\)/.test(readerSrc),
         "readerLoad still falls back to looking the input up by id");
  assert(/onchange="readerLoad\(this\)"/.test(readerSrc),
         "the markup passes `this` into readerLoad");
})();

// ---- the Reader is defined exactly once, in its own file --------------------
// A second definition in views.js used to shadow-override nothing (main.js
// loads last, so IT won) and silently ate edits made in the wrong file. The
// same trap moved with the code: the reader now lives in reader.js, and a
// stale copy left behind in main.js would win (it loads later).
(function testSingleDefinition() {
  const dir = process.argv[2].replace(/main\.js$/, "");
  const viewsJs = fs.readFileSync(dir + "views.js", "utf8");
  assert(!/^\s*reader\s*\(d\)\s*\{/m.test(viewsJs),
         "views.js does not define a duplicate VIEWS.reader");
  assert((readerSrc.match(/VIEWS\.reader\s*=/g) || []).length === 1,
         "VIEWS.reader is assigned exactly once, in reader.js");
  assert(!/VIEWS\.reader\s*=/.test(src),
         "main.js no longer carries a stale VIEWS.reader");
  // the pages the reader opens with must exist in the shell, or the pane is
  // built against ids nothing renders
  const html = fs.readFileSync(dir + ".." + "/index.html", "utf8");
  for (const id of ["rc-content", "rc-library", "rc-file-input"])
    assert(html.includes('id="' + id + '"') || readerSrc.includes('id="' + id + '"'),
           "the shell or the view markup provides #" + id);
  // The Reader IS an agent, but not a chat you navigate to: it exists only in the
  // reader's own ask panel (ASK_AGENT). Listed beside General/Coding it read as a
  // fifth conversation, so the sidebar entry was removed on purpose.
  assert(!/data-agent="reader"/.test(html),
         "the sidebar lists no Reader agent (it belongs to the reader's ask panel)");
})();

// ---- the conversation panel (#agent/<id>) -----------------------------------
// Same class of bug as the Reader's, so it gets the same lock. The panel is
// generated markup that carries the composer and the log, and render() rebuilds
// it on every navigation -- if the 5s poll rebuilt it too, the draft in #dmsg
// and the scroll position would die mid-sentence every five seconds.
(function testAgentPollGuard() {
  const renderFn = src.slice(src.indexOf("function setCount("),
                             src.indexOf("\nlet lastFetch"));
  let activeView = null, activeSub = null, animating = false, editing = false;
  const VIEWS = { agent: (d, sub) => "<agent:" + sub + ">", loop: () => "<loop>" };
  const TITLES = { agent: "对话" };
  const D = { provider: "p", model: "m", chat_log: [], stats: { turns: 0, tool_errors: 0 },
              graph: { stats: { quick: 0, full: 0 } }, facts: [], episodes: [],
              calendar: [], outbox: [], db: { all_tables: [] }, home: "H",
              agents: [{ id: "default" }, { id: "coding" }], current_sessions: {} };
  const els = {};
  const document = {
    querySelectorAll: () => [],
    querySelector: () => ({ scrollTop: 0 }),
    getElementById: id => (els[id] = els[id] || { textContent: "", innerHTML: "", style: {} }),
  };
  const window = { addEventListener() {}, getSelection: () => ({ toString: () => "" }) };
  const esc = s => String(s);
  const location = { hash: "#agent/coding" };
  let ACTIVE_AGENT = "default";
  const switched = [];
  let wired = 0;
  // The real selectAgent() is async but assigns ACTIVE_AGENT before its first
  // await, and render() relies on exactly that -- so a synchronous stand-in is
  // faithful to the contract being tested.
  function selectAgent(id) { switched.push(id); ACTIVE_AGENT = id; }
  function syncAgentChrome() {}
  function restoreReaderState() {}
  function wireChat() { wired++; }
  eval(renderFn);

  els["view"] = { innerHTML: "", style: {} };
  render();
  assert(switched.length === 1 && switched[0] === "coding",
         "a #agent/<id> hash that disagrees with the state switches agents");
  assert(els["view"].innerHTML === "<agent:coding>",
         "the conversation is built for the agent named in the hash");
  assert(wired === 1, "the generated panel is bound after it is built");

  render(); render();
  assert(els["view"].innerHTML === "<agent:coding>",
         "a poll does NOT rebuild the conversation (the #dmsg draft survives)");
  assert(wired === 3, "a poll still re-binds and repaints the log");
  assert(switched.length === 1, "render() stops switching once hash and state agree");

  location.hash = "#agent/default";
  render();
  assert(els["view"].innerHTML === "<agent:default>", "changing agent rebuilds the panel");
})();

// ---- the panel's element ids are a contract with the rest of the app --------
// #dmsg/#dsend are bound by wireChat(), #teletoggle by applyTele(), #modelchip
// by syncModelChip(), and .chatlog is what body.no-tele hides. All four are
// looked up by id/class from another file, so dropping one breaks a feature
// with no error anywhere.
(function testPanelContract() {
  const chatSrc = fs.readFileSync(
    process.argv[2].replace(/main\.js$/, "chat.js"), "utf8");
  const from = chatSrc.indexOf("function activeAgentData(){");
  const to = chatSrc.indexOf("\n};", chatSrc.indexOf("VIEWS.agent = function(){")) + 3;
  let ACTIVE_AGENT = "coding";
  let D = { agents: [{ id: "coding", name: "Coding", icon: "⌘", status: "idle" }],
            current_sessions: {} };
  const VIEWS = {};
  const esc = s => String(s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  eval(chatSrc.slice(from, to));
  const html = VIEWS.agent(D, "coding");

  for (const [needle, why] of [
    ['class="chatlog"', ".chatlog is what body.no-tele hides for the stats toggle"],
    ['id="dmsg"', "wireChat() binds the composer by id"],
    ['id="dsend"', "wireChat() binds Send by id"],
    ['id="teletoggle"', "applyTele() syncs the stats button by id"],
    ['id="modelchip"', "syncModelChip() syncs the model pill by id"],
  ]) assert(html.includes(needle), why);
  assert(html.includes("Coding"), "the header names the active agent");
  assert(!/id="dock/.test(chatSrc), "no dock ids survive in the panel");

  // The one-directional routing that keeps this from recursing: openAgent()
  // writes the hash, selectAgent() only switches state, and render() is the
  // only thing that maps one onto the other.
  const selectFn = chatSrc.match(/async function selectAgent\(agentId\)\{[\s\S]*?\n\}/);
  assert(selectFn && !/location\.hash/.test(selectFn[0]),
         "selectAgent never writes the hash (that is what keeps it from recursing)");
  assert(chatSrc.match(/function openAgent\(agentId\)\{[\s\S]*?\n\}/)[0]
           .includes('"#agent/" + agentId'),
         "openAgent routes to #agent/<id>");
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


def test_reader_survives_the_five_second_refresh(node, tmp_path):
    """The whole lock: render(), the reader pane and readerLoad()."""
    for path in (MAIN_JS, READER_JS):
        assert path.exists(), f"missing frontend source: {path}"
    harness = tmp_path / "reader_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(MAIN_JS), str(READER_JS)],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=60)

    # Show the claims either way: a bare "exit 1" tells the user nothing.
    print(proc.stdout)
    assert proc.returncode == 0, f"frontend checks failed:\n{proc.stdout}\n{proc.stderr}"

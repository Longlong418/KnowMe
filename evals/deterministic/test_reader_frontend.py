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

MAIN_JS = (Path(__file__).resolve().parents[2]
           / "knowme" / "ops" / "static" / "js" / "main.js")

# The harness reads main.js, lifts out the functions under test, and drives them
# on stubs.  It prints one PASS/FAIL line per claim and exits non-zero if any
# failed, so a failure here shows the user which claim broke, in English.
HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

// ---- render(): does the 5s poll rebuild the Reader's DOM? -------------------
(function testRender() {
  const renderFn = src.slice(src.indexOf("function render(){"),
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
  let restoreCalls = 0;
  function restoreReaderState() { restoreCalls++; }
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

// ---- renderReaderContent(): repaint only when the document changed ----------
(function testContent() {
  const fn = src.match(/function renderReaderContent\(\)\{[\s\S]*?\n\}/)[0];
  const esc = s => String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  let els, paints;
  function watch(el) {
    let v = el.innerHTML;
    Object.defineProperty(el, "innerHTML", { get: () => v, set(nv) { v = nv; paints++; } });
  }
  function reset() {
    els = {};
    for (const id of ["rc-content", "rc-selection", "rc-text"])
      els[id] = { id, innerHTML: "", dataset: {}, style: {}, textContent: "" };
    paints = 0;
    watch(els["rc-content"]); watch(els["rc-selection"]);
  }
  const document = { getElementById: id => els[id] || null };
  const window = { getSelection: () => ({ toString: () => "" }) };
  let currentDoc = null;
  eval(fn);

  reset();
  currentDoc = { name: "a.md", content: "para one\n\npara two" };
  let n = paints;
  renderReaderContent();
  assert(paints === n + 1 && els["rc-content"].innerHTML.includes("para one"),
         "loading a document paints it once");

  // The one that matters: a poll must not touch innerHTML, or a text selection
  // made with the mouse is dropped mid-drag.
  n = paints;
  renderReaderContent();
  assert(paints === n, "a poll repaints NOTHING (a live selection survives)");

  // A rebuilt view is a fresh element with no stamp -- it MUST repaint, or the
  // document would silently go blank.
  reset();
  n = paints;
  renderReaderContent();
  assert(paints === n + 1 && els["rc-content"].innerHTML.includes("para one"),
         "a rebuilt view (fresh element, no stamp) repaints the document");

  currentDoc = { name: "b.md", content: "something else" };
  n = paints;
  renderReaderContent();
  assert(paints === n + 1 && els["rc-content"].innerHTML.includes("something else"),
         "a different document repaints");

  currentDoc = null;
  renderReaderContent();
  assert(els["rc-content"].innerHTML.includes("还没有文档"), "the empty state is shown");
  assert(els["rc-content"].dataset.stamp === undefined, "the empty state clears the stamp");
})();

// ---- readerLoad(): reads the file off the event target ----------------------
(function testReaderLoad() {
  assert(/function readerLoad\(input\)/.test(src),
         "readerLoad takes the element the change event fired on");
  assert(/input = input \|\| document\.getElementById\("rc-file-input"\)/.test(src),
         "readerLoad still falls back to looking the input up by id");
  assert(/onchange="readerLoad\(this\)"/.test(src),
         "the markup passes `this` into readerLoad");
})();

// ---- only main.js defines VIEWS.reader -------------------------------------
// A second definition in views.js used to shadow-override nothing (main.js
// loads last, so IT won) and silently ate edits made in the wrong file.
(function testSingleDefinition() {
  const viewsJs = fs.readFileSync(
    process.argv[2].replace(/main\.js$/, "views.js"), "utf8");
  assert(!/^\s*reader\s*\(d\)\s*\{/m.test(viewsJs),
         "views.js no longer defines a duplicate VIEWS.reader");
  assert((src.match(/VIEWS\.reader\s*=/g) || []).length === 1,
         "VIEWS.reader is assigned exactly once, in main.js");
})();

process.exit(failures ? 1 : 0);
"""


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node is not installed — the frontend has no other test runner")
    return exe


def test_reader_survives_the_five_second_refresh(node, tmp_path):
    """The whole lock: render(), renderReaderContent() and readerLoad()."""
    assert MAIN_JS.exists(), f"missing frontend source: {MAIN_JS}"
    harness = tmp_path / "reader_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(MAIN_JS)],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=60)

    # Show the claims either way: a bare "exit 1" tells the user nothing.
    print(proc.stdout)
    assert proc.returncode == 0, f"frontend checks failed:\n{proc.stdout}\n{proc.stderr}"

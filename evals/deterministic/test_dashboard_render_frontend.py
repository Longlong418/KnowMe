"""render() must not be able to die on a sidebar counter.

Regression lock for the bug that was breaking the whole dashboard. Commit
6ee4900 removed the duplicate 记忆数据 nav entry — and took its
<span id="n-mem"> with it, while render() still wrote that id unconditionally.
The result was a TypeError on line 85 of main.js, on EVERY call, which killed
everything below it: restoreReaderState(), restoreKnowledgeState() and
wireChat() were never reached again.

That is exactly what the user saw, and why it looked like three unrelated bugs:

  the reader forgot its document on refresh, because the restore never ran
  the knowledge base jumped back to "create a note", for the same reason
  the knowledge base was "unstable", because the crash depended on which view
  the failing counter belonged to and when the poll landed

So the lock is not really about counters. It is about the loop: a cosmetic
counter that has lost its element must degrade to a counter that is not
painted, and the rest of render() must still run. node is required here and
soft elsewhere, so this skips rather than fails without it.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = (Path(__file__).resolve().parents[2] / "knowme" / "ops" / "static")
JS = STATIC / "js"
MAIN_JS = JS / "main.js"
INDEX_HTML = STATIC / "index.html"

# Reads the real main.js, lifts render() out by shape (the same slice the
# reader's harness uses), and drives it on a DOM stub. One PASS/FAIL line per
# claim; exits non-zero if any failed.
HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

const renderFn = src.slice(src.indexOf("function setCount("),
                           src.indexOf("\nlet lastFetch"));

// A fresh render() environment. `missing` is the counter id whose element has
// been deleted from the shell -- the user's bug, reproduced on demand.
function boot(missing) {
  let activeView = null, activeSub = null, animating = false, editing = false;
  const els = {};
  const paints = [];
  const document = {
    querySelectorAll: () => [],
    querySelector: () => ({ scrollTop: 0 }),
    getElementById(id) {
      if (id === missing) return null;               // the deleted nav entry
      return (els[id] = els[id] || { id, textContent: "", innerHTML: "", style: {} });
    },
  };
  const window = { addEventListener() {}, getSelection: () => ({ toString: () => "" }) };
  const esc = s => String(s);
  const location = { hash: "#reader" };
  const VIEWS = { reader: () => "<reader>", knowledge: () => "<knowledge>" };
  const TITLES = { reader: "阅读器" };
  const D = { provider: "p", model: "m", chat_log: [{}, {}], stats: { turns: 7, tool_errors: 0 },
              graph: { stats: { quick: 1, full: 2 } }, facts: [{}, {}], episodes: [{}],
              calendar: [{}], outbox: [{}], db: { all_tables: ["a", "b"] },
              home: "H", agents: [], current_sessions: {} };
  const calls = { restore: 0, knowledge: 0, wire: 0 };
  function restoreReaderState() { calls.restore++; }
  function restoreKnowledgeState() { calls.knowledge++; }
  function wireChat() { calls.wire++; }
  function syncAgentChrome() {}
  eval(renderFn);
  return { els, D, location, calls, render, get activeView() { return activeView; } };
}

// ---- the crash: one missing counter must not take the loop down -------------
(function testMissingCounterCannotCrash() {
  let ok = true, err = null;
  let env;
  try { env = boot("n-mem"); env.render(); }
  catch (e) { ok = false; err = e; }
  assert(ok, "render() survives a missing sidebar counter (no TypeError)"
             + (err ? ": " + err.message : ""));
  assert(env.calls.restore === 1,
         "and STILL runs restoreReaderState() — the reader keeps its document");
  assert(env.calls.wire === 1, "and STILL wires the reader's ask panel");
  // the other six counters are painted: the fix skips a missing element, it
  // does not abandon the block
  assert(env.els["n-loop"] && env.els["n-loop"].textContent === 7,
         "the counters that DO exist are still painted");
  assert(env.els["n-graph"] && env.els["n-graph"].textContent === 3,
         "and each one is given the value it used to get");
})();

// ---- the knowledge view's restore is on the same critical path --------------
(function testKnowledgeRestoreStillRuns() {
  const env = boot("n-mem");
  env.location.hash = "#knowledge";
  env.render();
  assert(env.calls.knowledge === 1,
         "restoreKnowledgeState() runs after a missing counter (the note stays open)");
})();

// ---- the normal case is unchanged ------------------------------------------
(function testCountersStillWorkWhenPresent() {
  const env = boot(null);
  env.render();
  const want = { "n-gw": 2, "n-loop": 7, "n-graph": 3, "n-mem": 3, "n-tools": 2,
                 "n-db": 2, "n-ops": "!" };
  for (const [id, value] of Object.entries(want))
    assert(env.els[id] && env.els[id].textContent === value,
           `#${id} is painted with the value it used to get (${value})`);
})();

// ---- and the shell provides every id the loop paints ------------------------
// This is the half that actually failed: the JS was fine, the <span> was gone.
(function testShellProvidesEveryCounter() {
  const html = fs.readFileSync(process.argv[3], "utf8");
  const ids = [...src.matchAll(/setCount\("([^"]+)"/g)].map(m => m[1]);
  assert(ids.length >= 7, "main.js still paints the sidebar counters");
  for (const id of ids)
    assert(html.includes('id="' + id + '"'),
           "index.html provides #" + id + " for the counter the loop paints");
})();

process.exit(failures ? 1 : 0);
"""


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node is not installed — the frontend has no other test runner")
    return exe


def test_a_missing_counter_cannot_kill_the_render_loop(node, tmp_path):
    """The whole lock: the crash, the critical path below it, and the shell."""
    for path in (MAIN_JS, INDEX_HTML):
        assert path.exists(), f"missing frontend source: {path}"
    harness = tmp_path / "render_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(MAIN_JS), str(INDEX_HTML)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    print(proc.stdout)
    assert proc.returncode == 0, f"render() checks failed:\n{proc.stdout}\n{proc.stderr}"


def test_the_render_slice_is_still_findable():
    """The harness slices render() out by shape, so a rename must fail loudly.

    Without this, moving render() or renaming setCount would make the harness
    eval nothing at all and pass every claim vacuously.
    """
    src = MAIN_JS.read_text(encoding="utf-8")
    assert "function setCount(" in src, "render()'s counter helper kept its name"
    assert "\nlet lastFetch" in src, "the slice's end marker is still there"
    assert re.search(r"^function render\(\)\{", src, re.M), "render() is still a function"

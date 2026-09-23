"""DETERMINISTIC EVAL — the two Coding pages, driven in node.

Three claims, all of them about the browser and none of them visible in a
screenshot:

  1. THE POLL MUST NOT REBUILD `#coding`. It was the one view without a guard
     in render() (main.js), so every 5 seconds replaced the whole #view
     innerHTML — which closed every directory you had folded open, scrolled the
     file tree back to the top, and collapsed the patch you were reading. Only
     the task textarea survived, and only because it is mirrored into
     localStorage.

  2. THE FILE TREE IS A REAL TREE. Directory rows carry a ▸ and used to do
     nothing when clicked, and the flat os.walk list was nested with a depth
     counter — which files children under the WRONG parent, because '/'
     sorts after most name characters and a sibling directory (`src.bak`) lands
     between `src` and `src/app.py`.

  3. A PATCH IS FILE CONTENT, SO IT IS UNTRUSTED TEXT. A line reading
     `+<b>hi</b>` in the 改动 tab must show those characters, not bold. The
     escaping happens in the helper (each diff line goes through the real
     esc() from util.js), so this runs the actual coding.js, not a paraphrase.

node is a hard requirement here, like the Reader's and Knowledge's locks; skip
without it.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[2]
      / "knowme" / "ops" / "static" / "js")
MAIN_JS = JS / "main.js"
CODING_JS = JS / "coding.js"
UTIL_JS = JS / "util.js"

# render()'s branch for #coding. Lifted out of main.js by shape, the way the
# Reader/Knowledge locks do it, and driven on a DOM stub: the claim is about
# which branch runs, and a headless browser would prove it more slowly.
POLL_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

const renderFn = src.slice(src.indexOf("function setCount("),
                           src.indexOf("\nlet lastFetch"));

function boot(){
  let activeView = null, activeSub = null, animating = false, editing = false;
  let paints = 0;
  const els = {};
  const document = {
    querySelectorAll: () => [],
    querySelector: () => ({ scrollTop: 0 }),
    getElementById: id => (els[id] = els[id] || { id, textContent: "", innerHTML: "", style: {} }),
  };
  const window = { addEventListener() {}, getSelection: () => ({ toString: () => "" }) };
  const location = { hash: "#coding" };
  const esc = s => String(s);
  const view = () => "<coding" + (++paints) + ">";
  const VIEWS = { coding: (d, sub) => view() + (sub || ""), reader: view, knowledge: view };
  const TITLES = { coding: "Coding" };
  const D = { provider: "p", model: "m", chat_log: [], stats: { turns: 0, tool_errors: 0 },
              graph: { stats: { quick: 0, full: 0 } }, facts: [], episodes: [],
              calendar: [], outbox: [], db: { all_tables: [] }, home: "H",
              agents: [], current_sessions: {} };
  function syncAgentChrome() {}
  function wireChat() {}
  function restoreReaderState() {}
  function restoreKnowledgeState() {}
  eval(renderFn);
  return { els, render, location, count: () => paints };
}

// The bug: rerender with the same hash, the way the 5s poll does.
(function testThePollDoesNotRebuildCoding() {
  const env = boot();
  env.render();
  assert(env.count() === 1, "the first render paints #coding");
  const painted = env.els["view"].innerHTML;
  env.render();                       // <- the poll
  assert(env.count() === 1,
         "a 5s poll does NOT rebuild #coding (your open directories survive)");
  assert(env.els["view"].innerHTML === painted,
         "and the #view element is left exactly as it was");
})();

// ...but navigating to a tab, or to another view, still has to rebuild: the
// file list and the receipt are different markup, not a repaint of one.
(function testNavigationStillRebuilds() {
  const env = boot();
  env.render();
  env.render();
  assert(env.count() === 1, "still one paint before navigating");
  env.location.hash = "#coding/changes";   // the 改动 tab
  env.render();
  assert(env.count() === 2, "switching to the 改动 tab DOES rebuild the view");
})();

process.exit(failures ? 1 : 0);
"""

# coding.js evaluated as a whole, with the real esc() from util.js in scope.
# Nothing in coding.js runs at load time — it is all declarations — so the only
# thing this harness has to provide is esc.
TREE_HARNESS = r"""
const fs = require("fs");
const util = fs.readFileSync(process.argv[2], "utf8");
const codingSrc = fs.readFileSync(process.argv[3], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

const escSrc = util.match(/^const esc = [\s\S]*?;\s*$/m);
assert(escSrc, "esc() found in util.js");
const app = eval(escSrc[0] + "\n" + codingSrc
                 + "\n; ({codingTree, codingDiff, codingChanges, codingTime})");
// Reached through `app` rather than destructured: the functions above are
// function declarations, so a direct eval leaks them into THIS scope, and a
// destructuring `const codingTree = ...` next to one is a redeclaration.

const entry = (path, kind, depth, size) => ({ path, kind, depth, size,
  name: path.slice(path.lastIndexOf("/") + 1) });

// coding_workspace.list_entries' order: sorted by (path.lower(), kind).
const ENTRIES = [
  entry(".gitignore", "file", 0, 10),
  entry("README.md", "file", 0, 10),
  entry("src", "directory", 1),
  entry("src.bak", "directory", 1),
  entry("src/app.py", "file", 1, 10),
  entry("src/lib", "directory", 2),
  entry("src/lib/util.py", "file", 2, 10),
];

(function testDirectoriesAreFoldableAndNestTheirFiles() {
  const html = app.codingTree(ENTRIES);
  const srcStart = html.indexOf(">src<");
  assert(srcStart > -1, "a directory renders as a <summary> (a row you can click)");
  assert(html.includes('<details class="ct-dir" open>'),
         "top-level directories start unfolded, and are <details> so they fold");
  assert(html.includes(">README.md<"), "files at the root are rendered");
  // Nesting. `src.bak` sorts between `src` and `src/app.py` in the server's
  // flat, path-sorted list, which is exactly what a stack or a depth counter
  // gets wrong — so what is pinned here is the SHAPE: everything of src's
  // inside src's own <details>, and none of it inside src.bak's.
  const srcBlock = html.slice(srcStart, html.indexOf("</details>", srcStart));
  for (const name of [">app.py<", ">lib<", ">util.py<"])
    assert(srcBlock.includes(name), `${name} is nested inside src`);
  assert(/padding-left:22px/.test(srcBlock), "src's children are indented one level in");
  const bakBlock = html.slice(html.indexOf(">src.bak<"));
  assert(!bakBlock.slice(0, bakBlock.indexOf("</details>")).includes("app.py"),
         "src.bak does not steal src's children (it is a sibling, not their parent)");
})();

(function testFilesAreClickableAndEscaped() {
  const html = app.codingTree([entry("a<b>.py", "file", 0, 10)]);
  assert(html.includes("openCodingFileEncoded('a%3Cb%3E.py')"),
         "a file row opens the file through the Context Bridge");
  assert(!html.includes("a<b>.py"), "and its name is escaped, not injected");
})();

(function testAPatchIsEscapedLineByLine() {
  const patch = ["--- a/x.py", "+++ b/x.py", "@@ -1,2 +1,2 @@", "-old", "+<b>hi</b>",
                 "+x = \"<script>alert(1)</script>\""].join("\n");
  const html = app.codingDiff(patch);
  assert(!html.includes("<b>hi</b>"), "a +line containing markup stays literal text");
  assert(!html.includes("<script>"), "so does a line containing a script tag");
  assert(html.includes("&lt;b&gt;hi&lt;/b&gt;"), "it is escaped, not dropped");
  assert(html.includes('class="dl-add"') && html.includes("&lt;b&gt;hi"),
         "added lines are classed for the green +");
  assert(html.includes('class="dl-del"') && html.includes("-old"),
         "removed lines are classed for the red −");
  assert(html.includes('class="dl-h"'), "hunks are classed");
  assert(html.includes('class="dl-f"'),
         "the ---/+++ headers are classed apart from real +/- lines");
  assert(!/<span class="dl-del">-\s*$/.test(html), "headers are not counted as deletions");
})();

(function testReceiptRowsAreClickable() {
  // A row that cannot be clicked is a receipt you cannot read. The first
  // version put the handler INSIDE the class attribute (no closing quote), so
  // the browser read `onclick="…"` as a class name, invented attributes from
  // the rest, and the row did nothing at all — with no error anywhere.
  const row = { id: "a", at: "2026-09-23T11:59:11", kind: "write", target: "x.py",
                summary: "新建文件", insertions: 1, deletions: 0, returncode: null,
                session_id: "s", detail: "0f1e2d3c4b5a69788796a5b4c3d2e1f0.diff" };
  const html = app.codingChanges([row]);
  assert(/class="cr-row[^"]*"\s+onclick="openCodingRun\('/.test(html),
         "a row with a patch carries a real onclick attribute");
  assert(!/class="[^"]*onclick=/.test(html),
         "and the handler is not swallowed into the class attribute");
  assert(html.includes("openCodingRun('0f1e2d3c4b5a69788796a5b4c3d2e1f0.diff')"),
         "with the stored file name, encoded");
  // The baseline row has no patch, so it must NOT pretend to be clickable.
  const baseline = { ...row, kind: "baseline", target: "", detail: "", summary: "起点 abc12345" };
  assert(!app.codingChanges([baseline]).includes("onclick"),
         "a row with nothing to show is not given a dead handler");
})();

process.exit(failures ? 1 : 0);
"""


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node is not installed — the frontend has no other test runner")
    return exe


def _run(node, tmp_path, harness, name, *sources):
    for path in sources:
        assert path.exists(), f"missing frontend source: {path}"
    script = tmp_path / f"{name}.js"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run([node, str(script), *[str(s) for s in sources]],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    print(proc.stdout)   # a bare "exit 1" tells the user nothing
    assert proc.returncode == 0, f"{name} failed:\n{proc.stdout}\n{proc.stderr}"


def test_the_poll_does_not_rebuild_the_coding_view(node, tmp_path):
    """The 4.3 bug: #coding was the one view with no guard in render()."""
    _run(node, tmp_path, POLL_HARNESS, "coding_poll", MAIN_JS)


def test_the_tree_nests_correctly_and_diffs_are_escaped(node, tmp_path):
    """The real coding.js against the real esc()."""
    assert CODING_JS.exists()
    _run(node, tmp_path, TREE_HARNESS, "coding_tree", UTIL_JS, CODING_JS)


def test_the_diff_helper_is_still_the_one_rendering_the_patch():
    """The escaping above only protects the receipt while codingRunDetail keeps
    routing patches through codingDiff. If someone inlines a <pre> around the
    patch text instead, the test above would keep passing while the page went
    back to rendering file content as markup."""
    src = CODING_JS.read_text(encoding="utf-8")
    assert re.search(r"isDiff \? codingDiff\(", src), (
        "the receipt no longer renders .diff details through codingDiff() — "
        "file content would reach the page unescaped")

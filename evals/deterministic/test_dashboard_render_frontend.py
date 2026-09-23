"""render() must not be able to die on a sidebar counter.

Regression lock for the bug that was breaking the whole web. Commit
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

The file also owns the other half of the same loop: what the poll does to the
chat log. Repainting it follows the newest message, which is what makes
streaming feel live — but wireChat() repaints on every poll, so an
unconditional jump to the bottom pulled the reader off the older message they
had just scrolled up to read. syncLogClass() now decides from where the log
already was.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = (Path(__file__).resolve().parents[2] / "knowme" / "ops" / "static")
JS = STATIC / "js"
MAIN_JS = JS / "main.js"
RENDER_JS = JS / "render.js"
UTIL_JS = JS / "util.js"
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


# The other half of the loop: what repainting the chat log does to the reader's
# scroll position. Reads the real render.js and drives syncLogClass() on a log
# element whose scroll geometry the test controls.
SCROLL_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

const SESSION = "dashboard-test";
const fn = src.slice(src.indexOf("function detailsKey("),
                     src.indexOf("\nconst CHAT_EMPTY"));
// A NEW string every time, so what these claims measure is the scroll rule and
// not the "nothing changed, don't touch the DOM" shortcut in front of it.
let painted = 0;
const renderChatLogFor = () => "<rendered" + (++painted) + ">";
const log = { scrollTop: 0, scrollHeight: 1200, clientHeight: 400, innerHTML: "",
              querySelectorAll: () => [] };
const document = { querySelectorAll: sel => (sel === ".asklog" ? [log] : []) };
eval(fn);

// Geometry: 1200px of log in a 400px window -> "at the bottom" means scrollTop 800.
const BOTTOM = log.scrollHeight - log.clientHeight;
assert(BOTTOM === 800, "the stub log is taller than its window (so it can scroll)");

// ---- scrolled up: the poll must leave you where you are --------------------
log.scrollTop = 0;
syncLogClass("asklog", [], "");
assert(log.innerHTML.startsWith("<rendered"), "the poll still repaints the log");
assert(log.scrollTop === 0,
       "a poll does NOT drag you back to the bottom when you have scrolled up");

// ---- at the bottom: streaming still follows ---------------------------------
log.scrollTop = BOTTOM;
syncLogClass("asklog", [], "");
assert(log.scrollTop === log.scrollHeight,
       "a poll DOES follow the newest message while you are at the bottom");

// ---- a log with nothing in it yet reads as 'at the bottom' ------------------
// This is the rebuilt-view case: render() replaces #view, so the log element is
// fresh and empty. Treating it as "you scrolled away" would open every
// conversation at its oldest message.
log.scrollTop = 0; log.scrollHeight = 0; log.clientHeight = 0;
syncLogClass("asklog", [], "");
assert(log.scrollTop === 0, "a fresh, empty log follows the newest message");

// ---- force: the callers that KNOW something new happened --------------------
log.scrollHeight = 1200; log.clientHeight = 400; log.scrollTop = 0;
syncLogClass("asklog", [], "", true);
assert(log.scrollTop === log.scrollHeight,
       "`force` jumps to the bottom even when you had scrolled up (you pressed send)");

process.exit(failures ? 1 : 0);
"""


# ...and the other thing the poll does to the log: it replaces every node in it.
# A <details> holds its open/closed state in the node, so the 详情 you just
# expanded closed itself a few seconds later — the user's 轨迹点开之后又折叠回去,
# which looked exactly like a page refresh. The stub model below is the DOM's
# real shape: assigning innerHTML is what rebuilds the node list, and
# reading it back never does.
DETAILS_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

let SESSION = "dashboard-one";
const fn = src.slice(src.indexOf("function detailsKey("),
                     src.indexOf("\nconst CHAT_EMPTY"));
// Two <details> per turn, in document order: a step's 详情 and the folded tool
// block under the reply. Both are the user's own open/closed state, and the
// real log is keyed the same way — positionally, over an append-only list.
let nodes = [];
let chat = [];
let paints = 0;
const renderChatLogFor = (c) => { chat = c || []; return "<log:" + chat.length + ">"; };
const log = {
  scrollTop: 0, scrollHeight: 1200, clientHeight: 400, _html: "",
  get innerHTML(){ return this._html; },
  set innerHTML(v){
    this._html = v;
    nodes = chat.flatMap(() => [{ open: false }, { open: false }]);
    paints++;
  },
  querySelectorAll: sel => (sel === "details" ? nodes : []),
};
const document = { querySelectorAll: sel => (sel === ".chatlog" ? [log] : []) };
eval(fn);

// ---- the reported bug: 点开的详情在重画之后还是开着的 -----------------------
syncLogClass("chatlog", ["turn 1", "turn 2"], "");
assert(paints === 1 && nodes.length === 4, "the log repaints into both boxes per turn");
nodes[1].open = true;                                   // 你点开了第一条的「详情」
nodes[3].open = true;                                   // 还有第二条下面折叠的工具结果
syncLogClass("chatlog", ["turn 1", "turn 2", "turn 3"], "");   // 又来了一条消息
assert(paints === 2, "a new message really does repaint the log");
assert(nodes.length === 6, "…and the repaint rebuilds every node from scratch");
assert(nodes[1].open, "the 详情 you opened is still open after the repaint");
assert(nodes[3].open, "…and so is the folded tool block");
assert(!nodes[0].open && !nodes[2].open && !nodes[4].open && !nodes[5].open,
       "the ones you left closed stay closed");

// ---- and an idle poll does not touch the log at all -------------------------
const kept = nodes;
syncLogClass("chatlog", ["turn 1", "turn 2", "turn 3"], "");
assert(nodes === kept && paints === 2,
       "a poll on a thread that is not moving leaves the DOM alone");

// ---- an 'open' from another conversation must not follow you ----------------
nodes[3].open = true;
SESSION = "dashboard-two";                              // 你切到另一条对话
syncLogClass("chatlog", ["别的对话"], "");
assert(nodes.length === 2 && !nodes[0].open && !nodes[1].open,
       "switching conversations does not reopen a box you left open in the last one");

process.exit(failures ? 1 : 0);
"""


# The stored tool block, folded into the reply card (render.js). A `list_files`
# result is a whole directory listing; as text in the bubble it pushed the answer
# off the screen ("工具调用的具体大结果会直接显示在下边，而且特别长"). The reply and
# the block are split apart now, and the block is ONE collapsed <details> whose
# body is previewed — still there, still complete, one click away.
#
# util.js is eval'd with the slice in ONE call on purpose: `let`/`const` inside
# an eval stay in that eval's scope, so a second eval() would not see esc().
TOOLS_HARNESS = r"""
const fs = require("fs");
const util = fs.readFileSync(process.argv[2], "utf8");
const src = fs.readFileSync(process.argv[3], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

const block = src.slice(src.indexOf("const TOOLS_BLOCK"),
                       src.indexOf("\nfunction renderChatLogFor"));
assert(block.includes("function toolsBlock(") && block.includes("function toggleTools("),
       "the slice still contains the whole tool-block section (render.js moved?)");
eval(util + "\n" + block);

// A row exactly as core/context/tool_entries.py writes it: the reply, the
// marker on its own line, then one `- tool(args) -> output` per call. The
// output is the reason this exists: 40 lines of file names.
const LISTING = "docs/\nevals/\nknowme/\nsql/\n" + "a-very-long-name.py\n".repeat(40);
const record = "我就能直接动手。\n[tools used]\n- list_files({}) -> " + LISTING;

// ---- the split -------------------------------------------------------------
const split = splitTools(record);
assert(split.reply === "我就能直接动手。", "splitTools keeps the reply and nothing else");
assert(!split.reply.includes("[tools used]") && !split.reply.includes("a-very-long-name"),
       "…so the listing cannot reach the bubble");
assert(split.tools.startsWith("- list_files({}) ->"), "the block keeps its entries");
assert(!split.tools.startsWith("["), "…with the [tools used] header line dropped");

const legacy = splitTools("Booked them.\n[tools used: search_web({'q': 'x'}) -> lots]");
assert(legacy.reply === "Booked them." && legacy.tools === "search_web({'q': 'x'}) -> lots",
       "the older one-line shape splits too (rows are never rewritten)");

const plain = splitTools("没有工具，就是聊天。");
assert(plain.reply === "没有工具，就是聊天。" && plain.tools === "",
       "a reply with no tool block is left exactly as it was");

// ---- the fold --------------------------------------------------------------
const html = toolsBlock(record);
assert(html.startsWith('<details class="turn-tools tele">'),
       "the block is ONE <details> — collapsed, and behind the 统计 toggle");
assert(!html.includes("<details open"), "…and collapsed means collapsed");
assert(html.includes("工具调用 · 1 次"), "its summary says how many calls there were");

const preview = html.match(/<pre class="tt-short">([\s\S]*?)<\/pre>/)[1];
assert(preview.length < split.tools.length, "you get a preview, not the whole result");
assert(preview.endsWith("…"), "…and it is visibly cut off, not silently truncated");
assert(html.includes('<pre class="tt-full" hidden>'), "the rest is in the DOM, hidden");
assert(html.includes(">显示全部<"), "with a button that offers it");

const small = toolsBlock("Booked them.\n[tools used]\n- create_event({'t': 'y'}) -> ok");
assert(!small.includes("tt-full") && !small.includes("显示全部"),
       "a short result is shown whole — no button for nothing");
assert(!toolsBlock("就是聊天。"), "no block, no box");

// ---- tool output is not HTML ------------------------------------------------
const nasty = toolsBlock("看看。\n[tools used]\n- read_file({}) -> <b>粗体</b></pre><img src=x onerror=alert(1)>");
assert(!nasty.includes("<b>粗体") && nasty.includes("&lt;b&gt;粗体"),
       "tool output is escaped — it is a file's contents, not markup");
assert(!nasty.includes("<img"), "…so it cannot close the <pre> or inject a tag");

// ---- the card: the reply is the reply, the telemetry is the fold ------------
// IN the eval, unlike everything above it: historicalCard is a `const`, and a
// const never leaves the scope of the eval() that declared it — the function
// declarations next to it do leak, which is exactly why the rest of this file
// can call splitTools() from out here and this part cannot.
eval(util + "\n" + block + "\n" + `
  const card = historicalCard({reply: record});
  const body = card.slice(card.indexOf('<div class="r">'), card.indexOf("<details"));
  assert(body.includes("我就能直接动手。"), "the card shows the reply");
  assert(!body.includes("a-very-long-name"), "…and not one line of the listing");
  const copied = card.match(/data-text="([^"]*)"/)[1];
  assert(copied.includes("我就能直接动手。") && !copied.includes("tools used"),
         "复制 copies the reply, not the telemetry stored next to it");
`);

// ---- the toggle flips both ways ---------------------------------------------
const shortEl = {hidden: false}, fullEl = {hidden: true};
const btn = {textContent: "显示全部",
             closest: () => ({querySelector: sel => sel === ".tt-short" ? shortEl : fullEl})};
toggleTools(btn);
assert(shortEl.hidden && !fullEl.hidden, "显示全部 reveals the rest of the result");
assert(btn.textContent === "收起", "…and offers the way back");
toggleTools(btn);
assert(!shortEl.hidden && fullEl.hidden && btn.textContent === "显示全部",
       "收起 puts the preview back");

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


def test_the_poll_does_not_steal_the_scroll_position(node, tmp_path):
    """The log follows the newest message only if you were already there."""
    assert RENDER_JS.exists(), f"missing frontend source: {RENDER_JS}"
    harness = tmp_path / "scroll_harness.js"
    harness.write_text(SCROLL_HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(RENDER_JS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    print(proc.stdout)
    assert proc.returncode == 0, f"log scroll checks failed:\n{proc.stdout}\n{proc.stderr}"


def test_the_tool_block_is_folded_and_previewed(node, tmp_path):
    """The reply bubble shows the reply; the stored tool block folds under it."""
    for path in (UTIL_JS, RENDER_JS):
        assert path.exists(), f"missing frontend source: {path}"
    harness = tmp_path / "tools_harness.js"
    harness.write_text(TOOLS_HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(UTIL_JS), str(RENDER_JS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    print(proc.stdout)
    assert proc.returncode == 0, f"tool block checks failed:\n{proc.stdout}\n{proc.stderr}"


def test_the_render_slice_is_still_findable():
    """The harness slices render() out by shape, so a rename must fail loudly.

    Without this, moving render() or renaming setCount would make the harness
    eval nothing at all and pass every claim vacuously.
    """
    src = MAIN_JS.read_text(encoding="utf-8")
    assert "function setCount(" in src, "render()'s counter helper kept its name"
    assert "\nlet lastFetch" in src, "the slice's end marker is still there"
    assert re.search(r"^function render\(\)\{", src, re.M), "render() is still a function"


def test_the_tool_block_slice_is_still_findable():
    """Same reason as above, for TOOLS_HARNESS's slice — and the wiring: both
    cards must ASK for the split, or the block leaks back into the bubble (the
    bug that was reported) even though splitTools() itself is perfect."""
    src = RENDER_JS.read_text(encoding="utf-8")
    assert "const TOOLS_BLOCK" in src, "the tool-block section kept its first line"
    assert "\nfunction renderChatLogFor" in src, "…and the slice's end marker is there"
    assert "splitTools(t.reply)" in src, "the turn card splits the stored reply"
    assert "splitTools(m.reply)" in src, "…and so does the card without meta"
    # …and the block still reaches the page, folded. Splitting without folding
    # would be a silent delete of the user's own record.
    assert "toolsBlock(t.reply)" in src, "the turn card folds the block under the reply"
    assert "toolsBlock(m.reply)" in src, "…and so does the card without meta"


def test_the_details_survive_a_repaint(node, tmp_path):
    """轨迹点开之后几秒自己合上：5 秒的轮询把整个日志 innerHTML 重写了一遍，
    而 <details> 的开合状态在节点上 —— 节点换了，盒子就关了。

    scroll_harness.js 盯着这个函数怎么放滚动条；这一个盯着它怎么处理节点。
    """
    assert RENDER_JS.exists(), f"missing frontend source: {RENDER_JS}"
    harness = tmp_path / "details_harness.js"
    harness.write_text(DETAILS_HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(RENDER_JS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    print(proc.stdout)
    assert proc.returncode == 0, f"log details checks failed:\n{proc.stdout}\n{proc.stderr}"


def test_the_log_slice_is_still_findable():
    """Same guard for the log harnesses — same silent-vacuity failure mode.

    Both slice from `function detailsKey(`, so renaming the helpers or moving
    them below syncLogClass must fail loudly rather than eval nothing.
    """
    src = RENDER_JS.read_text(encoding="utf-8")
    assert "function detailsKey(" in src, "the log repaint's key helper is still there"
    assert "function syncLogClass(" in src, "the log repaint kept its name"
    assert "\nconst CHAT_EMPTY" in src, "the slice's end marker is still there"
    assert "scrollTop" in src, "the repaint still positions the log"
    assert src.index("function detailsKey(") < src.index("function syncLogClass("), \
        "the helpers must sit inside the slice, above the function that uses them"

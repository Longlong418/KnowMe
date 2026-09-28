"""Two pages, two ways of catching up with the server — and the one direction
each of them is allowed to move.

Both functions live in the browser and both decide "should this page go and
re-read something it did not see happen". Neither can be checked end to end by
the suite (that needs Chrome plus a turn that outlives a page load), so the
decision itself is pinned here, against the real files.

  1. chat.js's threadIsBehind says whether the OPEN conversation is behind the
     server. The poll carries a message count per conversation, so the answer is
     free — but the comparison must be ONE-WAY. A turn that FAILED is shown on
     the page and never reaches chat_log (measured: chat_log stays empty, the
     page holds the user's message plus an 「错误：」 card). Compared both ways,
     every poll then finds "0 rows vs 2 on screen" and deletes what the user is
     reading — measured in .claude/probe_error_card.py, where the whole log
     turned back into the empty-state text.

  2. deepresearch.js's researchNote finds the report to show. Its id comes from
     localStorage, which is only written AFTER a run finishes in that browser —
     so a page that reloads mid-run, or a different browser, has no id at all
     and the report it is owed sits in the knowledge base, unread. The fallback
     is "the newest 深度研究 note", which leans on the server's ordering
     (tools/knowledge.py: ORDER BY updated_at DESC) and on the title prefix
     shared with ops/deep_research.py's _note_title.

     While a run is in flight the fallback must NOT fire: the running run's
     report is not stored yet, so "newest" is still the PREVIOUS run's report and
     showing it would pass last time's answer off as this time's.

  3. deepresearch.js's researchHistory lists the reports from earlier runs, which
     the page otherwise had no way to reach: it only ever showed the newest one,
     and reading an older report meant going to the knowledge base. The list is
     drawn only when there is more than one (a single row naming the report
     already on screen is noise), and picking a row beats the two rules above --
     including the mid-run silence, because a note picked by hand was not
     "the newest", it was asked for.

The real files run against stubs in node, same as test_graph_backedge.py.
node is a hard requirement of this file.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "knowme" / "ops" / "static" / "js"

# chat.js and deepresearch.js both author their view at load time (VIEWS.agent,
# VIEWS.deepresearch) and both read localStorage at load time. Everything they
# touch is stubbed; `D`, `CHAT` and `graphRun` are injected as parameters because
# the files that declare them (util.js does declare D, and does so for real)
# either are not loaded here or must stay under the harness's control.
HARNESS = r"""
const fs = require("fs");
const jsDir = process.argv[2];
const read = f => fs.readFileSync(jsDir + "/" + f, "utf8");

let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

const store = {};                       // localStorage, per harness run
const localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: k => { delete store[k]; },
};
const VIEWS = {};
const CHAT = [];
// `nodes` is here because researchPickNote repaints, and researchRestoreHot walks
// graphRun.nodes to put the "running" highlight back on a rebuilt chart.
let graphRun = {running: false, digest: "", waves: [], nodes: {}, noteId: ""};

const source = read("util.js") + "\n" + read("chat.js") + "\n" + read("deepresearch.js") + "\n"
  + "module.exports = {threadIsBehind, researchNote, researchReportText,"
  + " researchHistory, researchPickNote,"
  + " setD: v => { D = v; }, setRunning: v => { graphRun = Object.assign({}, graphRun, v); }};";
const mod = {exports: {}};
// Every getElementById in repaintResearch returns null: the assertions below are
// about what the page DECIDES to show, not about the DOM it writes it into.
new Function("module", "exports", "document", "localStorage", "VIEWS", "CHAT", "graphRun", source)(
  mod, mod.exports,
  {addEventListener: () => {}, querySelectorAll: () => [], querySelector: () => null,
   getElementById: () => null},
  localStorage, VIEWS, CHAT, graphRun);
const G = mod.exports;
const setChat = n => { CHAT.length = 0; for (let i = 0; i < n; i++) CHAT.push({role: "user"}); };

// --- 1: the poll may only ever move the page FORWARD ----------------------
G.setD({sessions_by_agent: {default: [{id: "s1", messages: 4}]}});
setChat(2);
assert(G.threadIsBehind("s1") === true, "server ahead of the page: go and re-read");
setChat(4);
assert(G.threadIsBehind("s1") === false, "same count: nothing to do");
// The load-bearing one. A failed turn is on the page and nowhere else.
setChat(6);
assert(G.threadIsBehind("s1") === false,
       "page ahead of the server (a failed turn): the poll must NOT replace it");
// A thread the server has no row for is not "behind" either: it is a thread
// that has just been created, and its first row will arrive with the turn.
setChat(0);
assert(G.threadIsBehind("nope") === false, "unknown thread: nothing to pull");
assert(G.threadIsBehind("s1") === true, "and an empty page on a known thread still pulls");

// --- 2: the report you are owed, found from the title it was stored under --
const NOTES = [
  {id: "n3", title: "深度研究：固态电池 · 2026-09-28", content: "这一趟的报告"},
  {id: "n2", title: "深度研究：半固态 · 2026-09-20", content: "上一趟的"},
  {id: "n1", title: "会议纪要", content: "无关的"},
];
G.setRunning({running: false});
G.setD({knowledge_info: {notes: NOTES}});
assert(G.researchNote() && G.researchNote().id === "n3",
       "the report is the newest 深度研究 note — not the newest note, and not one"
       + " picked out by an id this browser may never have written down");
assert(G.researchReportText() === "这一趟的报告",
       "the report shown is that note's body");

// Mid-run: the stored report is still the PREVIOUS run's, so there is nothing
// honest to show yet. "还没有报告" is the truth at that moment.
G.setRunning({running: true});
assert(G.researchNote() === null && G.researchReportText() === "",
       "while a run is in flight the stored report stays quiet");
G.setRunning({running: false});

// A finished run's own report wins over anything found in the knowledge base.
G.setRunning({digest: "刚跑完的这一份"});
assert(G.researchReportText() === "刚跑完的这一份",
       "the run that just finished is the report, not the stored one");
G.setRunning({digest: ""});

// The run that finished while this page was away (or a reload mid-run): its
// note is now the newest one, so the page follows it instead of holding on to
// whatever it was showing. This is the whole point of the fallback.
G.setD({knowledge_info: {notes: [
  {id: "n4", title: "深度研究：钠离子电池 · 2026-09-28", content: "刷新之后才跑完的那一份"},
].concat(NOTES)}});
assert(G.researchReportText() === "刷新之后才跑完的那一份",
       "a report that landed while the page was away replaces the one on screen");

// --- 3: the reports from earlier runs --------------------------------------
// One report is not a history: the only row would name the report already on
// screen, which is the noise this list exists to avoid.
G.setD({knowledge_info: {notes: [{id: "n1", title: "会议纪要", content: "无关的"}]}});
assert(G.researchNote() === null && G.researchReportText() === "",
       "no research report stored: show nothing rather than someone else's note");
assert(G.researchHistory() === "", "no research report stored: no history either");

G.setD({knowledge_info: {notes: NOTES}});
const hist = G.researchHistory();
assert(hist.includes("固态电池 · 2026-09-28"), "the history lists the newest report");
assert(hist.includes("半固态 · 2026-09-20"),
       "…and the ones from earlier runs, which this page could not reach before");
const labels = [...hist.matchAll(/>([^<]*)<\/button>/g)].map(m => m[1]);
assert(labels.length === 2 && !labels.some(l => l.includes("深度研究")),
       "each row reads as its own topic and date; the page's prefix is dropped from"
       + " the label (it survives in the tooltip)");
assert(hist.includes('title="深度研究：固态电池 · 2026-09-28"'),
       "…and the full title is still there to hover over — including a topic that"
       + " got cut to 60 characters by the workflow");
assert(hist.indexOf("固态电池") < hist.indexOf("半固态"),
       "newest first, the order the server hands them over in");
// Exactly one row is marked as the one on screen, and it is the newest.
const marked = () => (G.researchHistory().match(/dr-hist-row on"/g) || []).length;
assert(marked() === 1 && /dr-hist-row on"[^>]*researchPickNote\('n3'\)/.test(hist),
       "the row on screen is marked, and it is the newest one");

G.researchPickNote("n2");
assert(G.researchNote() && G.researchNote().id === "n2"
       && G.researchReportText() === "上一趟的",
       "picking an older row shows that report instead");
assert(/dr-hist-row on"[^>]*researchPickNote\('n2'\)/.test(G.researchHistory()),
       "…and the mark follows the pick");
G.researchPickNote("n2");
assert(G.researchNote().id === "n3", "clicking the same row again goes back to the newest");

// A pick outlives the mid-run rule above: that rule is about what "newest"
// silently means, and a report someone asked for by hand is not that.
G.setRunning({running: true});
G.researchPickNote("n2");
assert(G.researchNote() && G.researchNote().id === "n2",
       "a picked report stays on screen while a run is in flight");
G.researchPickNote("n2");
assert(G.researchNote() === null,
       "…but with nothing picked, a run in flight still shows nothing: the stored"
       + " report is the previous run's, and that is what the rule protects");
G.setRunning({running: false});

// A report that arrives while an old one is picked: the run that just finished
// is what the page is for, and the note it wrote is its own, so the newest wins.
G.setRunning({digest: "刚跑完的这一份"});
assert(G.researchNote().id === "n3" && G.researchReportText() === "刚跑完的这一份",
       "a finished run's own report wins, and stops the picked one from being"
       + " titled underneath it");
G.setRunning({digest: ""});

if (failures) console.log("\n" + failures + " FAILED");
process.exit(failures ? 1 : 0);   // without this, a FAILED run still exits 0
"""


@pytest.fixture(scope="module")
def catchup_run(tmp_path_factory) -> str:
    """Run the node harness once; the assertions below read its output."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    tmp = tmp_path_factory.mktemp("catchup")
    harness = tmp / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    result = subprocess.run(  # noqa: S603 — fixed argv, paths we just wrote
        [shutil.which("node"), str(harness), str(JS)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert result.returncode == 0, (
        "the catch-up harness reported failures:\n"
        f"{result.stdout.strip()}\n{result.stderr.strip()}"
    )
    return result.stdout


def test_every_assertion_in_the_harness_ran_and_passed(catchup_run: str):
    """Both halves matter: a harness that checked nothing passes for free."""
    assert "FAIL" not in catchup_run, catchup_run
    assert catchup_run.count("PASS") >= 10, catchup_run


def test_the_prefix_the_page_matches_on_is_the_one_the_workflow_writes():
    """researchNote matches notes by title prefix. If ops/deep_research.py ever
    renames the report, the fallback silently stops finding anything — and the
    only symptom would be a blank report page after a refresh. The two files are
    a pair, so they are compared here rather than trusted."""
    from knowme.ops.deep_research import _note_title

    page = (JS / "deepresearch.js").read_text(encoding="utf-8")
    found = re.search(r'RESEARCH_NOTE_PREFIX\s*=\s*"([^"]+)"', page)
    assert found, "deepresearch.js no longer declares RESEARCH_NOTE_PREFIX"
    assert _note_title("固态电池的产业化进度").startswith(found.group(1)), \
        f"the page looks for notes starting with {found.group(1)!r}, but the " \
        f"workflow writes {_note_title('固态电池的产业化进度')!r}"

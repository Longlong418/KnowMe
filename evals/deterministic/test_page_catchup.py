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

     The list is FOLDED by default, because it only ever grows: ten runs in, an
     open list is a wall of rows above the report it exists to help you reach.

  4. deepresearch.js's researchCardsRun decides which run the round cards belong
     to. A report and a process that describe different runs is the failure mode
     here, in both directions: the report on the right can be an old one you
     picked, and it can be the one a run that just finished wrote -- and the
     cards have to follow the REPORT, not the click.

     The process of an old run is not in this page's memory (the browser moved
     on) and not in /api/data (that is polled by every open tab), so it is asked
     for by note id and replayed. What a replay RENDERS is
     test_graph_backedge.py's business -- it loads the real graph.js and asserts
     a replay lands in byte-identical cards. Here the two are stubs: this file is
     about the decision, not the markup.

     The report the page found by itself gets its process the same way, from
     researchAutoLoad: an empty cards column next to a report reads as "that run
     kept nothing". Two things about it are asserted here and nowhere else —
     that it asks ONCE (it runs out of repaintResearch, which every 5-second
     poll calls), and that it does NOT count as a pick: a pick outranks "newest"
     by rule 2, so a page that auto-loaded through researchPickNote would pin
     itself to whichever report it happened to find first.

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

// What the server kept for a past run, by note id (ops/deep_research.py's
// write_frames). Empty means "a run from before that existed, or one whose note
// write failed" — which is an ordinary answer, not a failure.
const RECEIPTS = {};
const fetched = [];
let held = null, release = null;
const fetch = async url => {
  fetched.push(url);
  const id = decodeURIComponent(String(url).split("note_id=")[1] || "");
  if (held === id) await new Promise(r => { release = r; });   // see the "in flight" assertion
  return {json: async () => ({note_id: id, frames: RECEIPTS[id] || []})};
};
// graph.js is deliberately NOT loaded here: what a replay RENDERS is
// test_graph_backedge.py's business (it loads the real graph.js). What this file
// is about is the page's DECISION — which record's process belongs next to the
// report on screen — so the two functions are stubs that record what they were
// handed rather than draw it.
const graphNewRun = wf => ({workflow: wf, running: true, waves: [], nodes: {},
                            visit: {}, detail: {}, replayed: []});
const graphApplyEvent = (ev, run) => { run.replayed.push(ev.kind); };

const source = read("util.js") + "\n" + read("chat.js") + "\n" + read("deepresearch.js") + "\n"
  + "module.exports = {threadIsBehind, researchNote, researchReportText,"
  + " researchHistory, researchHistToggle, researchPickNote, researchCards,"
  + " researchCardsRun, researchPastLoading, repaintResearch,"
  + " setD: v => { D = v; }, setRunning: v => { graphRun = Object.assign({}, graphRun, v); },"
  + " liveRun: () => graphRun, pick: () => researchPick};";
const mod = {exports: {}};
// Every getElementById in repaintResearch returns null: the assertions below are
// about what the page DECIDES to show, not about the DOM it writes it into.
new Function("module", "exports", "document", "localStorage", "VIEWS", "CHAT", "graphRun",
             "fetch", "graphNewRun", "graphApplyEvent", source)(
  mod, mod.exports,
  {addEventListener: () => {}, querySelectorAll: () => [], querySelector: () => null,
   getElementById: () => null},
  localStorage, VIEWS, CHAT, graphRun, fetch, graphNewRun, graphApplyEvent);
const G = mod.exports;
const setChat = n => { CHAT.length = 0; for (let i = 0; i < n; i++) CHAT.push({role: "user"}); };

// researchPickNote goes to the network, so the whole body is async.
(async () => {

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

const OLD = {id: "n0", title: "深度研究：钠离子电池 · 2026-09-15", content: "更早的一趟"};
G.setD({knowledge_info: {notes: [NOTES[0]]}});
assert(G.researchHistory() === "",
       "one report is not a history either: its only row would name the report"
       + " already on screen");

G.setD({knowledge_info: {notes: [NOTES[0], NOTES[1], OLD]}});
// Folded up by default. The list only ever grows, so a list that is open is a
// wall of rows sitting above the report it is supposed to help you reach — and
// the report is the thing the page is for.
const folded = G.researchHistory();
const rows = h => [...h.matchAll(
  /class="dr-hist-row[^"]*"[^>]*>([^<]*)<\/button>/g)].map(m => m[1]);
assert(rows(folded).length === 0 && !folded.includes("dr-hist-list"),
       "the records start folded: nothing is listed until you ask");
assert(folded.includes("研究记录") && folded.includes("3 份"),
       "the folded line says how many records there are");
assert(folded.includes("当前：固态电池 · 2026-09-28"),
       "…and which one is on screen, so the line is not just a count");

G.researchHistToggle();
const hist = G.researchHistory();
assert(rows(hist).join("|") === "固态电池 · 2026-09-28|半固态 · 2026-09-20|钠离子电池 · 2026-09-15",
       "…and expands to one row per report, newest first — the order the server"
       + " hands them over in (updated_at DESC) — each reading as its own topic and"
       + " date, with the page's own 「深度研究：」 prefix dropped from the label");
assert(hist.includes('title="深度研究：固态电池 · 2026-09-28"'),
       "…while the full title stays for hovering, including a topic the workflow"
       + " cut to 60 characters");
// Exactly one row is marked as the one on screen, and it is the newest.
const marked = () => (G.researchHistory().match(/class="dr-hist-row on"/g) || []).length;
assert(marked() === 1 && /class="dr-hist-row on"[^>]*researchPickNote\('n3'\)/.test(hist),
       "the row on screen is marked, and it is the newest one");

// --- the process behind an old report --------------------------------------
// The report of an old run is a note, so the page always had it. What that run
// DID is not: the round cards were built from frames the browser received, and
// the browser moved on. The server kept them under the note id; the page asks
// for them when you pick that record, and replays them into a finished run.
RECEIPTS["n2"] = [{kind: "node_start", node: "plan", visit: 1},
                  {kind: "plan_ready", node: "plan", subquestions: ["半固态的界面"]},
                  {kind: "node_end", node: "plan", ms: 20}];
held = "n2";
const pending = G.researchPickNote("n2");
assert(G.researchPastLoading() === true && G.researchCards().includes("正在取"),
       "while the record's process is in flight the column says it is being fetched");
held = null; release();
await pending;
assert(G.researchNote() && G.researchNote().id === "n2"
       && G.researchReportText() === "上一趟的",
       "picking an older row shows that report instead");
assert(/class="dr-hist-row on"[^>]*researchPickNote\('n2'\)/.test(G.researchHistory()),
       "…and the mark follows the pick");
assert(fetched.some(u => u.includes("note_id=n2")),
       "…and its process was asked for by note id — it is not in this page's memory"
       + " after a reload, and /api/data is polled so it cannot carry every past run");
assert(G.researchPastLoading() === false, "the fetch is over");
assert(G.researchCardsRun().replayed.join() === "node_start,plan_ready,node_end",
       "…into cards built from exactly the frames the server kept, in order");
assert(G.researchCardsRun().workflow === "deep_research"
       && G.researchCardsRun().running === false
       && G.researchCardsRun() !== G.liveRun(),
       "…as a finished run of its own, not the one this page is watching");

// The cards follow the REPORT, not the click. A run that just finished is what
// the report column shows, and if the cards stayed on the old record it would
// read as "this run kept no process".
G.setRunning({digest: "刚跑完的这一份", waves: [1]});
assert(G.researchNote().id === "n3" && G.researchCardsRun() === G.liveRun(),
       "a finished run's own report takes the cards back with it");
G.setRunning({digest: "", waves: []});

await G.researchPickNote("n2");
assert(G.researchNote().id === "n3", "clicking the same row again goes back to the newest");
assert(G.researchCardsRun() === G.liveRun(), "…and so do the cards");

// A record with nothing kept: a run from before the frames were stored, or one
// whose note write failed. The page says which of the two it is looking at
// rather than drawing an empty column that reads as "this run did nothing".
await G.researchPickNote("n0");
assert(G.researchPastLoading() === false
       && G.researchCards().includes("没有留下过程记录"),
       "a record with no kept process says so: " + G.researchCards());
await G.researchPickNote("n0");

// A pick outlives the mid-run rule above: that rule is about what "newest"
// silently means, and a report someone asked for by hand is not that.
G.setRunning({running: true});
await G.researchPickNote("n2");
assert(G.researchNote() && G.researchNote().id === "n2",
       "a picked report stays on screen while a run is in flight");
await G.researchPickNote("n2");
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

// --- 5: the record the report column picked up by itself -------------------
// Refresh the page and the report is found in the knowledge base (rule 2), but
// the process behind it is not: the browser that watched the run is gone. The
// left column is that same record's process, so it goes and gets it — an empty
// column next to a report reads as "that run kept nothing".
const flush = () => new Promise(r => setTimeout(r, 0));
RECEIPTS["n3"] = [{kind: "node_start", node: "plan", visit: 1},
                  {kind: "node_end", node: "plan", ms: 5}];
fetched.length = 0;
G.setRunning({running: false, digest: ""});
// Driven through repaintResearch, not researchAutoLoad: the poll is what calls
// this in the app, so a version that decided correctly and was never wired in
// would pass every other assertion here and fetch nothing on a real page.
G.repaintResearch();
await flush();
assert(G.researchCardsRun().replayed.join() === "node_start,node_end",
       "the report the page found by itself brings its own process with it");
assert(G.pick() === "",
       "…without counting as a pick: a report that arrives later still takes over");
const asked = fetched.length;
G.repaintResearch();
await flush();
assert(fetched.length === asked,
       "…and it asks ONCE. This runs out of repaintResearch, which every 5-second"
       + " poll calls — an unguarded version asks again forever (asked "
       + asked + " then " + fetched.length + ")");

// The window that this cost a browser trip to find: a run has finished, the note
// it wrote has NOT reached the page yet (the poll is 5 seconds), and the frames
// that carry `done` have not arrived either. So `notes[0]` is still the previous
// report — and researchPast is holding exactly that report's process, because
// the auto-load fetched it on the way in. Both columns then describe a different
// run: the previous one's rounds beside this one's report.
// Measured on a real page: the cards still read /5 (the previous run's marker)
// at the moment the run ended, and only /7 (this run's) after a reload.
G.setRunning({digest: "刚跑完的这一份", waves: [1]});
assert(G.researchCardsRun() === G.liveRun(),
       "a run that just finished owns the cards even while the note it wrote is"
       + " still missing from the page — the frames arrive before the poll does");
// Same window, the other way round: the `done` frame is still in flight while
// the poll has already moved notes[0] to a newer report.
G.setD({knowledge_info: {notes: [
  {id: "n8", title: "深度研究：又一份 · 2026-09-30", content: "轮询刚送到的"},
].concat(NOTES)}});
G.setRunning({digest: "", waves: [1]});
const afterRun = fetched.length;
G.repaintResearch();
await flush();
assert(fetched.length === afterRun,
       "…and with a run of this page's own on screen it fetches nothing: the"
       + " process it would get is the one for a report it is not showing (asked "
       + afterRun + " then " + fetched.length + ")");
G.setRunning({waves: []});

G.setRunning({running: true});
const quiet = fetched.length;
G.repaintResearch();
await flush();
assert(fetched.length === quiet && G.researchCardsRun() === G.liveRun(),
       "a run in flight draws its own process — nothing to fetch for it");
G.setRunning({running: false});

// A report that lands later is the newest, and the cards follow it. The
// auto-load must not behave like a pick: a pick outranks "newest" (rule 2), so
// an auto-load that set one would pin the page to the report it happened to
// find first.
RECEIPTS["n9"] = [{kind: "graph_start", nodes: []}, {kind: "route"}];
G.setD({knowledge_info: {notes: [
  {id: "n9", title: "深度研究：更新的一份 · 2026-09-29", content: "后来的"},
].concat(NOTES)}});
G.repaintResearch();
await flush();
assert(G.researchNote().id === "n9"
       && G.researchCardsRun().replayed.join() === "graph_start,route",
       "a newer report takes the cards with it, process and all");

if (failures) console.log("\n" + failures + " FAILED");
process.exit(failures ? 1 : 0);   // without this, a FAILED run still exits 0
})();
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
    assert catchup_run.count("PASS") >= 35, catchup_run


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

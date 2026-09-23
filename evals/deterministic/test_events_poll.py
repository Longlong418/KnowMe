"""DETERMINISTIC EVAL — what the live poll costs when there is nothing to say.

/api/events is what lights the diagram up, and the browser asked it every 450 ms
from EVERY page. Two things were wrong with that, and they are the two locks
here:

  server-side  it answered by reading and json-parsing the whole of today's
               trace file before it could say "nothing new". Measured on an
               85 KB / 111-line trace, over a keep-alive connection: 7.7 ms a
               call, of which 5.3 ms was re-reading a file that had not changed
               (a bare 404 on the same server is 1.1 ms, so that IS the read).
               At 2.2 polls a second it was 62 seconds of server time an hour,
               almost always to report nothing. "Nothing new" is now answered
               from the file's SIZE — the trace is append-only, so an unchanged
               size means an unchanged line count — with no read at all.
  client-side  the poll ran on every page, but only 总览 and 图工作流 have a
               diagram for those events to light up. On 对话 — where the user
               actually spends their time — it was 2.2 requests a second that
               could not paint anything, and that is the larger half: on a page
               with no diagram the whole cost is waste, not just the read.

The node half is the same shape as the other frontend harnesses: it lifts
render() and the poll out of the real sources by text and drives them on a DOM
stub. node is required here and soft elsewhere, so it skips rather than fails.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from knowme.ops.web import data

JS = Path(__file__).resolve().parents[2] / "knowme" / "ops" / "static" / "js"
MAIN_JS = JS / "main.js"
DIAGRAM_JS = JS / "diagram.js"


def _today_trace(home: Path) -> Path:
    return home / "traces" / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"


def test_a_poll_with_nothing_new_does_not_read_the_trace_file(tmp_path, monkeypatch):
    """The common case — asked again, nothing appended — must not cost a read."""
    home = tmp_path / "home"
    trace = _today_trace(home)
    trace.parent.mkdir(parents=True)
    trace.write_text(json.dumps({"type": "turn_start"}) + "\n", encoding="utf-8")
    monkeypatch.setenv("KNOWME_HOME", str(home))
    # Module state outlives the test; the cache must not be someone else's. Read
    # defensively so that removing the cache makes this fail on the assertion
    # below — the behaviour — instead of on an AttributeError up here.
    getattr(data, "_TRACE_LINES", {}).clear()

    reads = []
    real = data.iter_trace_lines

    def counting(path):
        reads.append(path)
        return real(path)

    monkeypatch.setattr(data, "iter_trace_lines", counting)

    opening = data.events_since(None)
    assert opening == {"events": [], "cursor": 1}, \
        "the opening poll reports the tail so the browser starts fresh"
    assert len(reads) == 1, "…and looks at the file exactly once"

    for _ in range(5):
        assert data.events_since(1)["events"] == []
    assert len(reads) == 1, (
        f"five polls with nothing new read the trace {len(reads)} times — "
        f"the whole point is that they read it zero more"
    )

    with trace.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "turn_end", "reply": "hi"}) + "\n")

    turn = data.events_since(1)
    assert turn["events"] == [{"type": "turn_end", "reply": "hi"}], \
        "a turn that really happened still arrives"
    assert turn["cursor"] == 2
    assert data.events_since(2)["events"] == []


def test_a_stale_cursor_resyncs_instead_of_replaying(tmp_path, monkeypatch):
    """A resumed browser (cursor=None) and a rotated file (cursor past the end)
    both mean 'start from now'. Neither may hand back a queue of old turns."""
    home = tmp_path / "home"
    trace = _today_trace(home)
    trace.parent.mkdir(parents=True)
    trace.write_text("\n".join(json.dumps({"type": "turn_start", "n": n}) for n in range(3))
                     + "\n", encoding="utf-8")
    monkeypatch.setenv("KNOWME_HOME", str(home))
    getattr(data, "_TRACE_LINES", {}).clear()

    for cursor in (None, -1, 3, 99):
        answer = data.events_since(cursor)
        assert answer == {"events": [], "cursor": 3}, f"cursor={cursor!r} must resync to the tail"

    assert len(data.events_since(0)["events"]) == 3, "…and a real cursor still gets the events"


HARNESS = r"""
const fs = require("fs");
const mainSrc = fs.readFileSync(process.argv[2], "utf8");
const diagramSrc = fs.readFileSync(process.argv[3], "utf8");
let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};

// The same slice the other frontend harnesses lift render() out with.
const renderFn = mainSrc.slice(mainSrc.indexOf("function setCount("),
                               mainSrc.indexOf("\nlet lastFetch"));
// diagram.js from its event state to the end: the state, the animation helpers
// (unused here — the slice only has to stay contiguous) and the poll itself.
const pollFn = diagramSrc.slice(diagramSrc.indexOf("let evCursor"));

// A fresh render()+poll environment. `diagram.on` stands in for the DOM: it is
// what render() asks when it decides whether to poll, and flipping it is what
// navigating to another page does to the chart.
function boot(hasDiagram){
  let activeView = null, activeSub = null, animating = false, editing = false;
  const els = {}, intervals = [], cleared = [], urls = [];
  const diagram = { on: hasDiagram };
  const document = {
    querySelectorAll: () => [],
    querySelector(sel){ return sel === ".arch" && !diagram.on ? null : { scrollTop: 0 }; },
    getElementById(id){
      return (els[id] = els[id] || { id, textContent: "", innerHTML: "", style: {} });
    },
  };
  const window = { addEventListener(){}, getSelection: () => ({ toString: () => "" }) };
  const location = { hash: "#overview" };
  const esc = s => String(s);
  const ACTIVE_AGENT = "default";
  const VIEWS = { overview: () => "<overview>", agent: () => "<agent>" };
  const TITLES = { overview: "总览", agent: "对话" };
  const D = { provider: "p", model: "m", chat_log: [], stats: { turns: 0, tool_errors: 0 },
              graph: { stats: { quick: 0, full: 0 } }, facts: [], episodes: [], calendar: [],
              outbox: [], db: { all_tables: [] }, home: "H", agents: [], current_sessions: {} };
  function syncAgentChrome(){}
  function wireChat(){}
  // Timers and fetch are stubbed so the poll can be observed without a clock:
  // intervals counts how many are live, cleared records the stop.
  function setInterval(fn, ms){ intervals.push({ fn, ms }); return intervals.length; }
  function clearInterval(id){ cleared.push(id); }
  // What the server answers a cursor-less request with: the current tail, no
  // events (events_since's "start from now").
  function fetch(url){
    urls.push(url);
    return Promise.resolve({ json: () => Promise.resolve({ events: [], cursor: 9 }) });
  }
  const GRAPH_KINDS = new Set();     // graph.js owns this table
  const animateGraphStage = () => {};
  eval(pollFn);
  eval(renderFn);
  return { render, els, intervals, cleared, urls, diagram, location };
}

const idle = () => new Promise(r => setImmediate(r));

(async function(){
  // ---- 对话, where the time is actually spent, must not poll at all ---------
  {
    const env = boot(false);
    env.location.hash = "#agent";
    env.render();
    assert(env.intervals.length === 0 && env.urls.length === 0,
           "对话 never polls /api/events — no diagram, nothing to light up");
  }

  // ---- 总览 does, and starts from now -------------------------------------
  {
    const env = boot(true);
    env.location.hash = "#overview";
    env.render();
    assert(env.intervals.length === 1, "总览 starts the event poll");
    await idle();
    assert(env.urls.length === 1 && env.urls[0] === "/api/events",
           "…and asks at once, with no cursor, so it starts fresh");
  }

  // ---- the 5s re-renders must reuse it, not stack intervals ---------------
  {
    const env = boot(true);
    env.render(); env.render(); env.render();
    assert(env.intervals.length === 1, "the 5s re-renders reuse the one interval");
  }

  // ---- leaving stops it ----------------------------------------------------
  {
    const env = boot(true);
    env.render();
    env.diagram.on = false;          // the rebuild replaced the chart with 对话
    env.location.hash = "#agent";
    env.render();
    assert(env.cleared.length === 1, "leaving 总览 clears the interval");
  }

  // ---- coming back resumes from NOW, not from where it left off -----------
  {
    const env = boot(true);
    env.render();
    await idle();
    env.diagram.on = false; env.location.hash = "#agent"; env.render();
    env.diagram.on = true;  env.location.hash = "#overview"; env.render();
    await idle();
    assert(env.intervals.length === 2, "coming back starts a fresh interval");
    assert(env.urls[env.urls.length - 1] === "/api/events",
           "…and that first ask carries NO cursor: the turns you missed are not replayed");
    env.intervals[1].fn();
    await idle();
    assert(env.urls[env.urls.length - 1] === "/api/events?cursor=9",
           "…only that one — the polls after it carry the cursor again");
  }

  process.exit(failures ? 1 : 0);
})();
"""


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node is not installed — the frontend has no other test runner")
    return exe


def test_the_event_poll_runs_only_where_a_diagram_is_on_screen(node, tmp_path):
    for path in (MAIN_JS, DIAGRAM_JS):
        assert path.exists(), f"missing frontend source: {path}"
    harness = tmp_path / "events_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")

    proc = subprocess.run([node, str(harness), str(MAIN_JS), str(DIAGRAM_JS)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    print(proc.stdout)
    assert proc.returncode == 0, f"event poll checks failed:\n{proc.stdout}\n{proc.stderr}"


def test_the_poll_slice_is_still_findable():
    """Both harnesses slice the real sources by shape, so a rename must fail
    loudly — otherwise they eval nothing and pass every claim vacuously."""
    main = MAIN_JS.read_text(encoding="utf-8")
    diagram = DIAGRAM_JS.read_text(encoding="utf-8")
    assert "function setCount(" in main, "render()'s counter helper kept its name"
    assert "\nlet lastFetch" in main, "the slice's end marker is still there"
    assert "let evCursor" in diagram, "the poll's state kept its first line"
    assert "function setEventPolling(" in diagram, "the start/stop hook is still here"
    assert "function pollEvents(" in diagram, "…along with the poll it drives"
    # …and render() is what calls it: nothing else starts the poll any more.
    assert "setEventPolling(!!document.querySelector" in main, \
        "render() is still what decides whether the poll runs"
    assert "setInterval(pollEvents" not in main, \
        "main.js must not start the event poll behind render()'s back"

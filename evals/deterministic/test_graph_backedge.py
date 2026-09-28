"""The topology chart must be able to DRAW A CYCLE — and must not redraw a DAG.

deep_research loops (research → research), and the chart is data-driven off
Graph.describe(), so the chart had to learn the shape. Three things had to be
true at once, and each is a separate way this could go wrong:

  1. THE LAYOUT MUST NOT LAYER THE BACK EDGE. graphLayout is a longest-path
     relaxation, `layer[dst] = max(layer[dst], layer[src] + 1)`. Handed a self-
     loop it feeds its own output back in, so the node gains a column on every
     pass. Measured before the fix: `research` landed at layer 8 of an 11-column
     canvas and the loop was nowhere in the picture.

  2. THE LOOP NEEDS ITS OWN CURVE. edgeLine computes x1/x2 from `pos[e.src]` and
     `pos[e.dst]`, and for a self-loop those are the SAME object — the path is
     zero-width and hides behind the box it is supposed to be coming back to.

  3. NOTHING MAY MOVE IN THE CHARTS THAT ALREADY EXISTED. This is the load-
     bearing one. triage and gather are DAGs, so for them no edge is a back edge
     and every number the layout computes is the number it computed before — but
     that is the author's argument, and the golden file is the fact. Both charts
     are compared BYTE FOR BYTE against the output of the code from before this
     change (evals/deterministic/data/graph_charts_frozen.json, frozen from
     `git show HEAD:.../graph.js`).

Also locked here: one card per VISIT. A node that runs three times used to
produce three identical cards all reporting the last round's time, because
graphApplyEvent keyed them by node name alone. The key is now `name#visit`, and
visit 1 deliberately keeps the plain name — which is what keeps triage's and
gather's cards byte-identical as well.

The real graph.js runs against a DOM stub in node, same as the Reader's and the
Knowledge pane's frontend tests. node is a hard requirement of this file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "knowme" / "ops" / "static" / "js"
FROZEN = Path(__file__).resolve().parent / "data" / "graph_charts_frozen.json"

# One harness. `hot` is stubbed to RECORD what it was asked to light, because
# the route handler is one of the things under test; everything else graph.js
# touches is either real (util.js's esc) or an inert stub.
HARNESS = r"""
const fs = require("fs");
const jsDir = process.argv[2];
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const read = f => fs.readFileSync(jsDir + "/" + f, "utf8");

let failures = 0;
const assert = (ok, msg) => {
  if (!ok) failures++;
  console.log((ok ? "PASS  " : "FAIL  ") + msg);
};
const lit = [];

// graphRun is exported as an ACCESSOR, not a value: graphResetRun REASSIGNS it,
// so a captured object would be the previous run's — and every assertion about
// the cards would read an empty state and pass for the wrong reason.
const source = read("util.js") + "\n" + read("graph.js") + "\n"
  + "module.exports = {graphSVG, graphLayout, graphApplyEvent, graphResetRun,"
  + " graphRunPanel, graphCol, animateGraphStage, graphState: () => graphRun};";
const mod = {exports: {}};
new Function("module", "exports", "document", "performance", "render", "hot", source)(
  mod, mod.exports,
  {querySelector: () => ({}), querySelectorAll: () => []},   // .arch-status, and the guard
  {now: () => 0},
  () => {},
  (sel, cls, ms) => lit.push({sel, cls, ms}));
const G = mod.exports;

// --- 3: the DAG charts are the ones that were there before -----------------
for (const name of ["triage", "gather"]) {
  const wf = payload[name].wf;
  const layout = G.graphLayout(wf);
  assert(layout.back.size === 0, name + ": no edge is a back edge (it is a DAG)");
  const svg = G.graphSVG(wf);
  const was = payload[name].frozen;
  if (svg === was) {
    assert(true, name + ": SVG identical to the frozen pre-change chart (" + svg.length + " chars)");
  } else {
    let i = 0;
    while (i < svg.length && svg[i] === was[i]) i++;
    assert(false, name + ": SVG MOVED — first difference at byte " + i
      + "\n    was: " + JSON.stringify(was.slice(Math.max(0, i - 45), i + 45))
      + "\n    now: " + JSON.stringify(svg.slice(Math.max(0, i - 45), i + 45)));
  }
}

// --- 1: the cycle is layered, not stretched -------------------------------
const dr = payload.deep_research.wf;
const drLayout = G.graphLayout(dr);
assert([...drLayout.back].join() === "research|research",
       "deep_research: the self-loop is recognised as a back edge");
{
  // Sorted, because key insertion order is not the claim — the COLUMNS are.
  const got = Object.keys(drLayout.layer).sort()
    .map(k => k + "=" + drLayout.layer[k]).join(" ");
  assert(got === "END=5 START=0 plan=1 research=2 save=4 synthesize=3",
         "deep_research: every node sits one column after its furthest predecessor — " + got);
}
assert(drLayout.cols.length === 6,
       "deep_research: 6 columns, not one per relaxation pass (got " + drLayout.cols.length + ")");

// --- 2: the loop is drawn as a loop --------------------------------------
const svg = G.graphSVG(dr);
const LOOPRE = new RegExp('<path class="flow dash" data-edge="g-deep_research-research-research"'
  + '\\s*d="([^"]+)"');
const loopPath = LOOPRE.exec(svg);
assert(loopPath !== null, "deep_research: the back edge is drawn, with its own curve");
const PATHRE = new RegExp("^M(\\d+) (\\d+) C(\\d+) (\\d+) (\\d+) (\\d+) (\\d+) (\\d+)"
  + " C(\\d+) (\\d+) (\\d+) (\\d+) (\\d+) (\\d+)$");
const boxRE = new RegExp('<g class="node" data-node="g-deep_research-research">\\s*'
  + '<rect class="bx" x="(\\d+)" y="(\\d+)" width="(\\d+)" height="(\\d+)"');
if (loopPath) {
  const m = PATHRE.exec(loopPath[1].trim());
  assert(m !== null, "deep_research: the arc is a well-formed two-segment curve");
  const box = boxRE.exec(svg);
  assert(box !== null, "deep_research: the research node has a box");
  if (m && box) {
    const d = m.map(Number);
    // M(1,2) — out of the right edge; then the two control points and the end of
    // the first segment (3..8), the second segment's three (9..14).
    const rx = d[1], ry = d[2], tx = d[13], ty = d[14];
    const apex = d[6];                        // the loop's highest point
    const bx = +box[1], by = +box[2], bw = +box[3], bh = +box[4];
    assert(rx === bx + bw && ry === by + bh / 2,
           "deep_research: the arc leaves the box's right edge ("
           + rx + "," + ry + " vs " + (bx + bw) + "," + (by + bh / 2) + ")");
    assert(tx === bx + bw / 2 && ty === by,
           "deep_research: the arc arrives at the box's top edge ("
           + tx + "," + ty + " vs " + (bx + bw / 2) + "," + by + ")");
    assert(apex < by, "deep_research: the arc rises ABOVE the box (apex " + apex
           + " < top " + by + ") — otherwise it is hidden behind the node");
    assert(apex > 0, "deep_research: the arc stays inside the viewBox (apex " + apex + ")");
  }
}

// --- no node is pushed off the canvas ------------------------------------
{
  const vb = /viewBox="0 0 (\d+) (\d+)"/.exec(svg);
  const W = +vb[1], H = +vb[2];
  const rects = [...svg.matchAll(/<rect class="bx" x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"/g)];
  assert(rects.length === 6, "deep_research: 6 boxes drawn (got " + rects.length + ")");
  let inside = true;
  for (const r of rects) {
    if (+r[1] < 0 || +r[2] < 0 || +r[1] + +r[3] > W || +r[2] + +r[4] > H) inside = false;
  }
  assert(inside, "deep_research: every box is inside the " + W + "x" + H + " viewBox");
}

// --- every node in the new chart carries a Chinese label ------------------
for (const raw of ["plan", "research", "save"]) {
  assert(!svg.includes(">" + raw + "<"),
         "deep_research: node \"" + raw + "\" is not left as a bare English name");
}
assert(svg.includes("拆解子问题") && svg.includes("研究一轮") && svg.includes("存档"),
       "deep_research: its nodes carry Chinese labels");

// --- one card per VISIT ---------------------------------------------------
const drive = (workflow, events) => {
  G.graphResetRun(workflow);
  events.forEach(G.graphApplyEvent);
  return G.graphState();
};
{
  const dag = drive("t", [
    {kind: "graph_start", nodes: ["a", "b"]},
    {kind: "node_start", node: "a", visit: 1},
    {kind: "node_end", node: "a", ms: 10, keys: ["x"]},
    {kind: "node_start", node: "b", visit: 1},
    {kind: "node_end", node: "b", ms: 20, keys: ["y"]},
  ]);
  assert(JSON.stringify(Object.keys(dag.nodes)) === '["a","b"]',
         "a DAG: cards are keyed by the plain node name, exactly as before — "
         + JSON.stringify(Object.keys(dag.nodes)));
  assert(dag.waves.length === 2 && dag.waves.every(w => w.nodes.length === 1),
         "a DAG: one card per wave, unchanged");
  assert(!G.graphCol("a", dag.waves[0]).includes("第 1 轮"),
         "a DAG: the card header carries no round chip");
  // The card MARKUP, captured from `git show HEAD:.../graph.js` driving these
  // same four events. Three of the four lines of graphCol changed; this is the
  // proof that none of them changed what a DAG card looks like.
  const CARD_A = '<div class="cmp-col"><div class="cmp-h"><b>a</b>\n      <span class="chip">10ms</span></div>\n    <div class="wavebar"><i style="width:100%"></i></div>\n    <div class="meta"></div>\n    <div class="meta"><span class="chip">x</span></div>\n  </div>';
  assert(G.graphCol("a", dag.waves[0]) === CARD_A,
         "a DAG: the card markup is byte-identical to the pre-change card"
         + (G.graphCol("a", dag.waves[0]) === CARD_A ? ""
            : "\n    was: " + JSON.stringify(CARD_A)
              + "\n    now: " + JSON.stringify(G.graphCol("a", dag.waves[0]))));
}
{
  const loop = drive("deep_research", [
    {kind: "graph_start", nodes: ["plan", "research", "synthesize", "save"]},
    {kind: "node_start", node: "plan", visit: 1},
    {kind: "node_end", node: "plan", ms: 5},
    {kind: "node_start", node: "research", visit: 1},
    {kind: "node_end", node: "research", ms: 100},
    {kind: "node_start", node: "research", visit: 2},
    {kind: "node_end", node: "research", ms: 200},
    {kind: "node_start", node: "research", visit: 3},
    {kind: "node_end", node: "research", ms: 300},
  ]);
  // graph_start seeds a "waiting" placeholder for every declared node, so the
  // cards that actually EXIST are the ones with a status.
  const cards = Object.keys(loop.nodes).filter(k => loop.nodes[k].status !== "waiting");
  assert(JSON.stringify(cards) === '["plan","research","research#2","research#3"]',
         "a loop: every visit keeps its own card — " + JSON.stringify(cards));
  assert(loop.nodes["research"].ms === 100 && loop.nodes["research#2"].ms === 200
         && loop.nodes["research#3"].ms === 300,
         "a loop: each card reports ITS round's time, not the last round's");
  assert(JSON.stringify(loop.waves.map(w => w.nodes))
         === '[["plan"],["research"],["research#2"],["research#3"]]',
         "a loop: each round is a wave of its own — "
         + JSON.stringify(loop.waves.map(w => w.nodes)));
  // The bar is scaled to the card's OWN wave. Looking the wave up by name (the
  // old `waves.find(w => w.nodes.includes(name))`) always found round 1.
  assert(G.graphCol("research#3", loop.waves[3]).includes('style="width:100%"'),
         "a loop: a lone node is 100% of its own wave, not of a wave it already left");
  assert(G.graphCol("research#3", loop.waves[3]).includes("第 3 轮"),
         "a loop: the card says which round it was");
  assert((G.graphRunPanel().match(/cmp-col/g) || []).length === 4,
         "a loop: the run panel draws one card per round (got "
         + (G.graphRunPanel().match(/cmp-col/g) || []).length + ")");
  assert(G.graphRunPanel().includes("100ms") && G.graphRunPanel().includes("300ms"),
         "a loop: the run panel shows each round's own time");
}

// --- what each node DID, on the card -------------------------------------
//
// The engine cannot supply this: node_end carries the KEYS of the dict a node
// returned and never the values, so a card built from engine events alone can
// say "plan produced `subquestions message`" and not which sub-questions those
// were. It rides two events the workflow emits itself, and the branches that
// read them must be driven HERE — runGraph's frame loop swallows a throw, so a
// broken branch renders a bare card and says nothing anywhere else.
{
  const run = drive("deep_research", [
    {kind: "graph_start", nodes: ["plan", "research", "synthesize", "save"]},
    {kind: "node_start", node: "plan", visit: 1},
    {kind: "plan_ready", node: "plan",
     subquestions: ["固态电池的能量密度", "固态电池的量产时间"]},
    {kind: "node_end", node: "plan", ms: 428, keys: ["subquestions", "message"]},
    {kind: "node_start", node: "research", visit: 1},
    {kind: "research_round", node: "research", round: 1, agents: [
      {subquestion: "固态电池的能量密度", searches: 2, reads: 3, iterations: 8,
       max_iterations: 8, hit_limit: true, error: "", ms: 2100},
      {subquestion: "固态电池的量产时间", searches: 1, reads: 0, iterations: 2,
       max_iterations: 8, hit_limit: false, error: "", ms: 640},
      {subquestion: "固态电池的成本", searches: 0, reads: 0, iterations: 0,
       max_iterations: 8, hit_limit: false, error: "RuntimeError: boom", ms: 12},
    ]},
    {kind: "node_end", node: "research", ms: 2900, keys: ["reply", "sources"]},
  ]);
  const planCard = G.graphCol("plan", run.waves[0]);
  assert(planCard.includes("固态电池的能量密度") && planCard.includes("固态电池的量产时间"),
         "the plan card says what the topic was split into, not just that it was split");
  assert((planCard.match(/sa-row/g) || []).length === 2,
         "the plan card lists one line per sub-question");

  const roundCard = G.graphCol("research", run.waves[1]);
  assert((roundCard.match(/sa-row/g) || []).length === 3,
         "the round card lists one line per sub-agent (got "
         + (roundCard.match(/sa-row/g) || []).length + ")");
  assert(roundCard.includes("固态电池的量产时间"), "each line names the question it owns");
  // Counted, not quoted: the numbers come off the tool records and off run_loop's
  // receipt, never off what an agent said it did.
  assert(roundCard.includes("找 2 · 读 3 · 往返 8/8") && roundCard.includes("没查完"),
         "a starved agent's line says so: " + roundCard);
  assert(roundCard.includes("找 1 · 读 0 · 往返 2/8") && roundCard.includes("0.6s"),
         "an idle agent's line shows what it actually spent: " + roundCard);
  assert(roundCard.includes("失败"), "a crashed agent is not dropped off the card");
  assert(!planCard.includes("没查完"), "the plan card is a plan, not a receipt");

  // A fresh run must not inherit the last one's detail. graphResetRun replaces
  // the run object wholesale, so a missing `detail: {}` in it would quietly show
  // the previous topic's sub-questions on a brand new run's cards.
  const again = drive("deep_research", [
    {kind: "graph_start", nodes: ["plan"]},
    {kind: "node_start", node: "plan", visit: 1},
    {kind: "node_end", node: "plan", ms: 5},
  ]);
  assert(!G.graphCol("plan", again.waves[0]).includes("sa-row"),
         "the previous run's detail leaked into the next run's card");
}

// --- a route lights the EDGE, never the target node ---------------------
lit.length = 0;
G.animateGraphStage({type: "route", workflow: "deep_research",
                     router: "research", target: "research"});
assert(lit.filter(h => h.sel.includes("[data-node=")).length === 0,
       "a route does not light the target node — node_start owns that light, and a"
       + " 1400ms timer here switched it off mid-run. lit: "
       + JSON.stringify(lit.map(h => h.sel)));
assert(lit.some(h => h.sel === '[data-edge="g-deep_research-research-research"]'),
       "a route lights its edge (the self-loop's back edge included)");

if (failures) console.log("\n" + failures + " FAILED");
process.exit(failures ? 1 : 0);   // without this, a FAILED run still exits 0
"""


def _topologies() -> dict:
    from knowme.graph.workflows.deep_research import deep_research_topology
    from knowme.graph.workflows.gather import gather_topology
    from knowme.graph.workflows.triage import triage_topology

    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    return {
        "triage": {"wf": triage_topology(), "frozen": frozen["triage"]},
        "gather": {"wf": gather_topology(), "frozen": frozen["gather"]},
        "deep_research": {"wf": deep_research_topology()},
    }


@pytest.fixture(scope="module")
def chart_run(tmp_path_factory) -> str:
    """Run the node harness once; every test below reads its output."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    tmp = tmp_path_factory.mktemp("chart")
    payload = tmp / "payload.json"
    payload.write_text(json.dumps(_topologies(), ensure_ascii=False), encoding="utf-8")
    harness = tmp / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    result = subprocess.run(  # noqa: S603 — fixed argv, paths we just wrote
        [shutil.which("node"), str(harness), str(JS), str(payload)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert result.returncode == 0, (
        "the chart harness reported failures:\n"
        f"{result.stdout.strip()}\n{result.stderr.strip()}"
    )
    return result.stdout


def test_every_assertion_in_the_harness_ran_and_passed(chart_run: str):
    """Both halves matter: a harness that checked nothing passes for free."""
    assert "FAIL" not in chart_run, chart_run
    assert chart_run.count("PASS") >= 15, chart_run


def test_deep_research_is_served_to_the_chart():
    """The chart draws whatever /api/data serves, so a workflow missing from that
    list has no picture no matter how good graphSVG is."""
    from knowme.ops.web import data as web_data

    served = [w["name"] for w in web_data.collect("default")["graph"]["workflows"]]
    assert served == ["triage", "gather", "deep_research"], served


def test_the_frozen_charts_are_not_empty():
    """The golden file IS the lock. One that lost its contents would make the
    byte-for-byte comparison pass against an empty string."""
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    assert set(frozen) == {"triage", "gather"}, list(frozen)
    for name, chart in frozen.items():
        assert len(chart) > 2000 and "graphchart" in chart, (name, len(chart))

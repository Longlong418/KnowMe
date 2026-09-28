"""DETERMINISTIC EVAL — the deep research workflow: it goes round, and it stops.

Three claims, each of which is a different way this could be a lie:

  1. THE LOOP IS BOUNDED BY CODE, NOT BY THE MODEL. `stop_reason()` counts
     sources the TOOLS returned; a model that writes "gaps: none, we're done"
     cannot end the run early, and a model that would happily keep searching
     cannot make it run forever. Both directions are driven here with scripted
     callables — no model, no network.

  2. STOPPING STILL PRODUCES A REPORT. The failure that matters is not "it ran
     too long", it is "it hit a guard rail and the wave came back EMPTY". An
     empty wave ends the run before synthesize, so a run that searched for two
     minutes delivers nothing at all and only a line in `errors`. `max_visits`
     is therefore set one ABOVE the round budget it is not enforcing, and the
     round that raises still lands somewhere.

  3. IT CAN ONLY TOUCH THE WEB. The sub-agent is handed an allowlist of exactly
     two tools (knowme/ops/deep_research.py). Nothing here may grow a calendar,
     a send, or a filesystem: the whole run's writes are one knowledge note and
     one markdown file in the outbox.

Everything runs offline: injected callables, no model, no network.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest

from knowme.graph import run_graph
from knowme.graph.workflows.deep_research import (
    MAX_ROUNDS,
    MAX_VISITS,
    build_deep_research_graph,
    deep_research_topology,
    parse_plan,
    sources_from_tool_calls,
    stop_reason,
)


def _graph(**overrides):
    """A deep research run with harmless stubs; override one callable per test."""
    base = {
        "plan_fn": lambda s: '["q1", "q2"]',
        "research_fn": lambda s: {"reply": "found things",
                                  "sources": [f"https://new-{s.get('round', 0)}.example/a"]},
        "synth_fn": lambda s: "REPORT",
        "save_fn": lambda s: {"note_id": "n1", "draft_path": "/tmp/r.md"},
    }
    base.update(overrides)
    return build_deep_research_graph(**base)


def _run(**overrides) -> tuple[dict, list[str]]:
    """Run it and hand back (final state, the engine's path of nodes).

    `budget` is the page's pair of knobs. It is not a graph override — it belongs
    in the initial state, which is exactly where run_deep_research puts it.
    """
    budget = overrides.pop("budget", None)
    seen: list[tuple[str, dict]] = []
    state = run_graph(_graph(**overrides), {"topic": "固态电池", "budget": budget},
                      observer=lambda kind, ev: seen.append((kind, ev)))
    end = next(ev for kind, ev in seen if kind == "graph_end")
    return state, end["path"]


# --- 1: the loop is bounded, and the bound is code ---------------------------

def test_a_model_that_says_it_is_done_does_not_get_to_stop_it():
    """The tempting design is to route on the model's own "what is still
    missing" report. That hands control flow to the model, and a model that
    would rather finish writes "缺口：无". Here the round says exactly that on
    every turn while its tools keep returning NEW urls — and the loop runs the
    full budget anyway. Model prose cannot fake the count."""
    rounds: list[int] = []

    def lying_round(state):
        rounds.append(state.get("round") or 0)
        return {"reply": "缺口：无，可以收工了。",
                "sources": [f"https://brand-new-{len(rounds)}.example/x"]}

    state, path = _run(research_fn=lying_round)
    assert len(rounds) == MAX_ROUNDS, f"the loop ran {len(rounds)} rounds, not {MAX_ROUNDS}"
    assert path == ["plan", *["research"] * MAX_ROUNDS, "synthesize", "save"], path
    assert state["sources_new"] == 1


def test_a_round_that_finds_nothing_stops_the_loop():
    """The other direction: the same three pages re-read is the failure this
    exists to avoid. A round whose URLs are all already in `seen` ends it."""
    rounds: list[int] = []

    def stale_round(state):
        rounds.append(state.get("round") or 0)
        return {"reply": "同一批链接", "sources": ["https://only-one.example/a"]}

    state, path = _run(research_fn=stale_round)
    assert len(rounds) == 2, f"expected one repeat that found nothing new (got {rounds})"
    assert state["sources_new"] == 0
    assert state["seen"] == ["https://only-one.example/a"]
    assert path == ["plan", "research", "research", "synthesize", "save"], path


def test_stop_reason_is_pure_code_over_counts():
    """It is called OUTSIDE the engine's try/except (engine.py:189), so it must
    never raise: a KeyError here takes graph_end down with it and the page never
    hears that the run is over."""
    assert stop_reason({"round": 1, "sources_new": 3}) == ""
    assert stop_reason({"round": 1, "sources_new": 0}) == "这一轮没有找到新的信源"
    assert stop_reason({"round": MAX_ROUNDS, "sources_new": 9}).startswith("到轮次上限")
    assert stop_reason({}) != ""          # an empty state stops, it does not raise
    assert stop_reason({"round": None, "sources_new": None}) != ""


def test_the_round_budget_is_one_below_max_visits():
    """max_visits is the bug detector, not the brake. If they were equal, the
    run would end on an empty wave — with no report and only a line in errors.
    (describe() does not carry max_visits: it is not a drawing, it is a guard.)"""
    assert MAX_VISITS > MAX_ROUNDS
    assert _graph().nodes["research"].max_visits == MAX_VISITS


def test_the_pages_round_knob_lowers_the_ceiling_the_router_reads():
    """The knob the user asked for, driven where it is actually read. The same
    scripted round that runs MAX_ROUNDS times by default stops after two, and
    `errors` being empty is the load-bearing part: the ROUTER stopped it, the
    guard never fired."""
    rounds: list[int] = []

    def always_new(state):
        rounds.append(state.get("round") or 0)
        return {"reply": "found things",
                "sources": [f"https://new-{len(rounds)}.example/a"]}

    state, path = _run(research_fn=always_new, budget={"rounds": 2})

    assert len(rounds) == 2, f"expected the page's 2 rounds, got {len(rounds)}"
    assert path == ["plan", "research", "research", "synthesize", "save"], path
    assert state["digest"] == "REPORT", "a shortened run still writes a report"
    assert state["errors"] == {}, state["errors"]


def test_a_dirty_round_knob_cannot_take_the_run_down_with_it():
    """`stop_reason` is called OUTSIDE the engine's try/except (engine.py:189),
    so anything it raises takes `graph_end` with it and the page never hears the
    run is over. The budget comes from a POST body: assume nothing about it."""
    for dirty in ({"rounds": None}, {"rounds": 0}, {"rounds": "3"}, {"rounds": True},
                  "not a dict", None):
        state = {"round": 1, "sources_new": 9, "budget": dirty}
        assert stop_reason(state) == "", dirty          # falls back, keeps going
        state["round"] = MAX_ROUNDS
        assert stop_reason(state).startswith("到轮次上限"), dirty

    # And a GOOD one is read, not passed over: "it fell back to the default" and
    # "it never looked at the field" give the same answer on a dirty value, so
    # the two directions have to be asserted side by side.
    assert stop_reason({"round": 2, "sources_new": 9, "budget": {"rounds": 2}}) \
        .startswith("到轮次上限")
    assert stop_reason({"round": 2, "sources_new": 9, "budget": {"rounds": 5}}) == ""


def test_max_visits_is_a_parameter_and_its_default_has_not_moved():
    """The binder raises it with the round knob (rounds + 1), so it has to be an
    argument. The default must stay MAX_VISITS: deep_research_topology() passes
    no max_visits at all, and test_graph_describe compares that drawing to a live
    describe() output."""
    assert _graph().nodes["research"].max_visits == MAX_VISITS
    assert _graph(max_visits=7).nodes["research"].max_visits == 7


# --- 2: stopping still produces a report -------------------------------------

def test_the_run_drains_to_a_report_and_the_guards_are_never_touched():
    """A full run: three rounds, then synthesize and save. `errors` empty is the
    load-bearing assertion — it is the only way to see from outside that neither
    max_visits nor max_steps fired, i.e. that the ROUTER stopped it."""
    state, path = _run()
    assert path == ["plan", "research", "research", "research", "synthesize", "save"], path
    assert state["digest"] == "REPORT"
    assert state["errors"] == {}, state["errors"]
    assert state["note_id"] == "n1" and state["draft_path"] == "/tmp/r.md"


def test_a_round_that_blows_up_still_leaves_something_to_write_up():
    """An API that dies on round 1 must not turn into 'no report'. The round
    writes honest text about what happened, the router sees 0 new sources, and
    synthesize runs on what there is."""
    def broken_round(state):
        raise RuntimeError("search backend on fire")

    state, path = _run(research_fn=broken_round)
    assert "synthesize" in path and path[-1] == "save", path
    assert state["digest"] == "REPORT"
    assert "search backend on fire" in state["findings"], state["findings"]
    assert state["errors"] == {}, state["errors"]


def test_the_topic_survives_a_planner_that_returns_prose():
    """parse_plan fails open: a planner that ignores the JSON instruction costs
    focus, never the run (triage's classify sets the same precedent)."""
    assert parse_plan('["a", "b"]', "T") == ["a", "b"]
    assert parse_plan("- first question\n- second question", "T") == \
        ["first question", "second question"]
    assert parse_plan("I could not produce a plan.", "T") == \
        ["I could not produce a plan."]   # prose is better than nothing
    assert parse_plan("", "T") == ["T"]   # ...and nothing falls back to the topic


# --- 3: it can only touch the web --------------------------------------------

def test_the_subagent_is_handed_exactly_two_tools():
    """Read the binder's source, because the capability claim has no runtime
    signal: it is `subset(SUBAGENT_TOOLS)` or it is a lie."""
    from knowme.ops import deep_research as binder

    assert binder.SUBAGENT_TOOLS == {"search_web", "read_webpage"}
    subsets = [n for n in ast.walk(ast.parse(inspect.getsource(binder)))
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "subset"]
    assert len(subsets) == 1, "expected exactly one tools.subset call"
    (arg,) = subsets[0].args
    assert isinstance(arg, ast.Name) and arg.id == "SUBAGENT_TOOLS", ast.dump(arg)
    # And the registry is never used any other way: a bare `knowme.tools.execute`
    # would run a tool the allowlist never approved.
    code = _code_lines(binder)
    for banned in ("knowme.tools.execute", "knowme.tools.schemas", "knowme.tools.register"):
        assert not [ln for ln in code if banned in ln], banned


def test_the_pure_workflow_cannot_reach_the_model_or_the_disk():
    """The workflow module takes callables and returns dicts. The moment it
    imports a client or opens a file it stops being testable without a network,
    which is the property every test above depends on."""
    from knowme.graph.workflows import deep_research as workflow

    code = _code_lines(workflow)
    for banned in ("import anthropic", "open(", "sqlite3", "Path(", "requests."):
        assert not [ln for ln in code if banned in ln], banned


@pytest.mark.parametrize("topic", ["../../etc/passwd", "..\\..\\win.ini", "a/b/c",
                                   "我的研究 / 2026", ""])
def test_the_report_always_lands_inside_the_outbox(tmp_path, topic):
    """Same posture as gather's _draft: resolve, then check, then write. _slug
    strips everything a traversal needs, so the guard is belt and braces — the
    property worth asserting is the one a user can see: whatever the topic says,
    the file is in outbox/."""
    from knowme.ops import deep_research as binder

    dest = binder._write_report(tmp_path, {"topic": topic}, "body").replace("\\", "/")
    assert dest.startswith(str(tmp_path).replace("\\", "/") + "/outbox/"), dest
    assert "__" not in dest and ".." not in dest.split("/outbox/")[1]


def test_the_outbox_guard_refuses_a_path_that_escapes(tmp_path, monkeypatch):
    """...and the guard is not dead code. The slug has to be forced, because
    _slug cannot produce this: a file NAME of `research-x/../../y-<date>.md`
    resolves out of the outbox, which is exactly what it is there to catch."""
    from knowme.ops import deep_research as binder

    monkeypatch.setattr(binder, "_slug", lambda topic, limit=40: "x/../../y")
    out = binder._write_report(tmp_path, {"topic": "t"}, "body")
    assert out.startswith("refused"), out
    assert not list(tmp_path.glob(f"y-{date.today().isoformat()}.md"))


def test_a_report_that_cites_nothing_still_gets_its_sources(tmp_path):
    """A model that forgets the Sources list should not cost the reader the
    links: the URLs the run actually touched are appended."""
    from knowme.ops import deep_research as binder

    dest = binder._write_report(tmp_path, {"topic": "t", "seen": ["https://a.example/x"]},
                                "the body")
    text = (tmp_path / "outbox" / f"research-t-{date.today().isoformat()}.md").read_text("utf-8")
    assert dest.endswith(".md") and "the body" in text
    assert "https://a.example/x" in text and "## Sources" in text


def test_no_topic_means_no_run():
    """A research run with no topic is a paid round trip to find nothing. The
    refusal has to happen BEFORE KnowMe() is built — this asserts the shape of
    the answer, which is what the CLI and `/deep_research` both print."""
    from knowme.ops.deep_research import USAGE, run_deep_research

    assert run_deep_research(message="   ") == {"digest": USAGE}


# --- the signal itself --------------------------------------------------------

def test_sources_are_read_off_the_tool_records_not_the_reply():
    calls = [
        {"tool": "search_web", "args": {"query": "x"},
         "output": "Title\nhttps://one.example/a\nsnippet\nhttps://two.example/b.\n"},
        {"tool": "read_webpage", "args": {"url": "https://three.example/c"}, "output": "text"},
        {"tool": "search_web", "args": {"query": "y"}, "output": "https://one.example/a"},
    ]
    assert sources_from_tool_calls(calls) == [
        "https://one.example/a",      # trailing full stop trimmed
        "https://two.example/b",
        "https://three.example/c",    # named only in the argument
    ]


def test_the_topology_matches_the_graph_that_runs():
    """The chart is drawn from describe(), so it cannot drift — as long as the
    stub build and the real build stay the same shape."""
    assert deep_research_topology() == _graph().describe()


def _code_lines(module) -> list[str]:
    """Source minus docstrings and comments — these modules DISCUSS the banned
    names at length in prose explaining why they are banned, and a test that
    trips over its own explanation is a bad test (test_gather_workflow.py's
    helper, same reason)."""
    src = inspect.getsource(module)
    doc: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            doc.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return [ln for i, ln in enumerate(src.splitlines(), 1)
            if i not in doc and not ln.strip().startswith("#")]


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

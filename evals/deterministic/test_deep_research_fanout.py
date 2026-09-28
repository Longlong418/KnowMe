"""DETERMINISTIC EVAL — one sub-agent per sub-question, running in parallel.

The workflow test next door drives `build_deep_research_graph` with SCRIPTED
callables, so it never touches `knowme/ops/deep_research.py` — the module where
the real `research_fn` lives. These tests are the other half: they build the
REAL bound graph and patch only the seams that would call a model (`run_loop`
and `_one_shot`), so the fan-out itself is what is under test.

What could be a lie about "we now run a sub-agent per sub-question":

  1. IT FANS OUT. N sub-questions must cost N loop turns, each carrying its own
     sub-question and its own budget — not one turn handed a list.
  2. ONE DEAD AGENT COSTS ONE SUB-QUESTION. `f.result()` re-raises, so a worker
     that blows up would take the whole round (and its paid-for searches) down
     with it. The round must still carry the other agents' notes.
  3. THE SIGNAL SURVIVES THE MERGE. The router counts URLs off `tool_calls`
     (rule 1 in the workflow's docstring), so a URL two agents both read must
     not count twice; and a sub-agent that ran out of iterations must arrive in
     the report labelled as such — its reply is a canned apology, not a finding.
  4. IT IS STILL ONE LOCK. `agent_lock` is a plain `threading.Lock`: a worker
     re-acquiring what its parent holds DEADLOCKS rather than fails, so this is
     the one property that has to be asserted by counting, never by waiting.

Everything runs offline: no model, no network, no real tools.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from knowme.core.loop import LoopResult
from knowme.graph import run_graph
from knowme.ops import deep_research as binder

QUESTIONS = ["固态电池的能量密度", "固态电池的量产时间", "固态电池的成本"]
PLAN_JSON = json.dumps(QUESTIONS, ensure_ascii=False)
SHARED = "https://shared.example/a"


class _FakeTools:
    """`knowme.tools`, reduced to the one method the binder may call."""

    def __init__(self):
        self.subset_calls: list[frozenset] = []
        self.registry = object()   # its identity is what one test asserts on

    def subset(self, names):
        self.subset_calls.append(names)
        return self.registry


class _FakeKnowme:
    """Only the attributes build_bound_graph touches. `client` is read but never
    used — `run_loop` is what would call it, and that is patched out.
    `tracer.event` is what run_deep_research writes EVERY event to (the page only
    gets the forwarded kinds), so a run driven through that entry point needs it."""

    def __init__(self, home):
        self.client = object()
        self.settings = SimpleNamespace(small_model="small", model="big", home=home)
        self.tools = _FakeTools()
        self.tracer = SimpleNamespace(event=lambda kind, ev: None)


class _LockProbe:
    """A stand-in for `agent_lock` that records every acquisition.

    The real lock is non-reentrant, so the failure this guards against is a
    hang, not an exception — a test that waited for it would hang too. Counting
    is the only way to assert it without racing.
    """

    def __init__(self):
        self.taken: list[str] = []

    def __enter__(self):
        self.taken.append(threading.current_thread().name)
        return self

    def __exit__(self, *exc):
        return False


def _call(url: str) -> dict:
    return {"tool": "read_webpage", "args": {"url": url}, "output": f"page: {url}"}


def _run(monkeypatch, tmp_path, answer, plan: str = PLAN_JSON, budget=None,
         via_page: bool = False):
    """Run the real bound graph and hand back (final state, records, fake).

    `answer(**kwargs) -> LoopResult` stands in for one sub-agent's whole turn. It
    is handed exactly what run_loop would get, so a test can read the prompt,
    fire the observer, or raise. Records hold every turn's kwargs, every event
    that reached the observer, and the lock probe.

    `budget` is what the page sends; `via_page` drives `run_deep_research` — the
    real door a dashboard request comes through — instead of calling run_graph
    here. That is the only way to see the FORWARDED set, because this helper
    otherwise sits on the far side of it and sees every kind there is.
    """
    records = {"loops": [], "events": [], "lock": _LockProbe()}

    def fake_run_loop(**kwargs):
        records["loops"].append(kwargs)
        return answer(**kwargs)

    def collect(kind, ev):
        records["events"].append((kind, ev))

    monkeypatch.setattr(binder, "run_loop", fake_run_loop)
    # Both one-shot calls go through here; the prompts are distinguishable.
    monkeypatch.setattr(binder, "_one_shot",
                        lambda knowme, model, prompt, max_tokens:
                        plan if "research plan" in prompt else "REPORT")
    monkeypatch.setattr(binder, "agent_lock", records["lock"])
    # save_fn's note write needs a live sqlite connection. The report file goes
    # to tmp_path for real; the note is not what these tests are about.
    monkeypatch.setattr(binder, "create_note", lambda *a, **k: {"id": "n1"})

    knowme = _FakeKnowme(tmp_path)
    if via_page:
        state = binder.run_deep_research(knowme=knowme, observer=collect,
                                         message="固态电池", budget=budget)
    else:
        state = run_graph(binder.build_bound_graph(knowme, budget),
                          {"topic": "固态电池", "budget": budget}, observer=collect)
    return state, records, knowme


def _question_of(kwargs) -> str:
    """Which sub-question this worker owns. Exactly one is in its prompt."""
    prompt = kwargs["messages"][0]["content"]
    return next(q for q in QUESTIONS if q in prompt)


def _round_rows(records) -> list[list[dict]]:
    """The per-sub-agent rows of every round, in round order — the payload the
    page's round cards are drawn from. `agents` rides the existing
    `research_round` event rather than a kind of its own, so one round is still
    one event (the manual verifier counts them to work out how many rounds ran)."""
    return [ev["agents"] for kind, ev in records["events"] if kind == "research_round"]


def _notes(**kwargs):
    """A sub-agent that touches no tools: the round finds nothing new and the
    run ends after one pass, which is what keeps the call counts readable."""
    return LoopResult(reply=f"notes for {_question_of(kwargs)}")


# --- 1: it fans out -----------------------------------------------------------

def test_one_loop_turn_per_subquestion(monkeypatch, tmp_path):
    """Three sub-questions, three turns — and each turn is handed ONE question.
    A turn fed all three would still be a single call: that is the bug this
    replaces, and it would pass any test that only counted sources."""
    _, records, _ = _run(monkeypatch, tmp_path, _notes)

    assert len(records["loops"]) == len(QUESTIONS)
    for kwargs, question in zip(records["loops"], QUESTIONS):
        prompt = kwargs["messages"][0]["content"]
        assert question in prompt
        assert sum(other in prompt for other in QUESTIONS) == 1, prompt
        # Its own full budget, not a quarter of a shared one.
        assert kwargs["max_iterations"] == binder.MAX_ITERATIONS


def test_the_registry_is_built_once_and_shared_by_every_worker(monkeypatch, tmp_path):
    """`subset` runs once, before the fan-out, and every worker gets THAT
    registry. Building it per worker would be N identical registries — and the
    allowlist test next door pins the module to exactly one call, so this is the
    property that keeps both halves true at once."""
    _, records, knowme = _run(monkeypatch, tmp_path, _notes)

    assert knowme.tools.subset_calls == [binder.SUBAGENT_TOOLS]
    assert {id(kwargs["tools"]) for kwargs in records["loops"]} == {id(knowme.tools.registry)}


# --- 2: a dead sub-agent costs only its own sub-question ----------------------

def test_a_raising_subagent_does_not_take_the_round_with_it(monkeypatch, tmp_path):
    """`f.result()` re-raises whatever the worker raised, so the worker has to
    swallow its own exception. The other two questions were already paid for."""
    def answer(**kwargs):
        question = _question_of(kwargs)
        if question == QUESTIONS[1]:
            raise RuntimeError("search backend on fire")
        return LoopResult(reply=f"notes for {question}")

    state, _, _ = _run(monkeypatch, tmp_path, answer)

    assert "search backend on fire" in state["findings"], state["findings"]
    assert f"notes for {QUESTIONS[0]}" in state["findings"], state["findings"]
    assert f"notes for {QUESTIONS[2]}" in state["findings"], state["findings"]
    # The round still reached the writer: a run that spent three searches and
    # lost one of them has to produce a report anyway (rule 2, workflow docstring).
    assert state["digest"] == "REPORT" and state["draft_path"].endswith(".md")


# --- 3: the signal survives the merge ----------------------------------------

def test_sources_are_deduped_across_subagents(monkeypatch, tmp_path):
    """Two agents reading the same page must not count as two new sources —
    that number is what decides whether another round runs."""
    def answer(**kwargs):
        if "earlier rounds already established" in kwargs["messages"][0]["content"]:
            return LoopResult(reply="nothing new")   # round 2 finds nothing → stop
        index = QUESTIONS.index(_question_of(kwargs))
        return LoopResult(reply=f"notes {index}",
                          tool_calls=[_call(SHARED), _call(f"https://only-{index}.example/b")])

    state, records, _ = _run(monkeypatch, tmp_path, answer)

    assert state["seen"] == [SHARED, "https://only-0.example/b",
                             "https://only-1.example/b", "https://only-2.example/b"]
    assert state["sources_new"] == 0, "round 2 re-counted an already-seen URL"
    # Round 2 fanned out too, with the covered material in its brief.
    assert len(records["loops"]) == 2 * len(QUESTIONS)
    assert "earlier rounds already established" in records["loops"][-1]["messages"][0]["content"]


def test_a_starved_subquestion_is_named_where_it_happened(monkeypatch, tmp_path):
    """A sub-agent that hits its iteration limit replies with loop.py's canned
    apology, which is NOT a finding. It has to arrive in the report labelled as
    this run's gap — and under the question it belongs to, so the synthesizer can
    tell the reader which question to go and ask somewhere else."""
    def answer(**kwargs):
        question = _question_of(kwargs)
        return LoopResult(reply="partial notes", hit_limit=(question == QUESTIONS[1]))

    state, _, _ = _run(monkeypatch, tmp_path, answer)

    labelled = f"### {QUESTIONS[1]}\n\n{binder.BUDGET_SPENT}"
    assert labelled in state["findings"]
    # Exactly once, and only for the agent that actually starved.
    assert state["findings"].count(binder.BUDGET_SPENT) == 1
    assert f"### {QUESTIONS[0]}\n\npartial notes" in state["findings"]


# --- 4: one lock, and events that say which question they came from ----------

def test_the_lock_is_taken_once_per_round_not_once_per_worker(monkeypatch, tmp_path):
    """Once per round, from the thread that owns the round. A worker taking it
    again would deadlock on the real lock — the probe makes that observable."""
    _, records, _ = _run(monkeypatch, tmp_path, _notes)

    assert records["lock"].taken == [threading.current_thread().name]


def test_every_event_says_which_subquestion_emitted_it(monkeypatch, tmp_path):
    """Without the tag all three agents write `node: research`, and the trace
    cannot show WHICH question ate its budget — which is the whole reason the
    report can now say why a gap is a gap."""
    def answer(**kwargs):
        kwargs["observer"]("tool", {"tool": "search_web", "args": {}, "output": "x"})
        return LoopResult(reply="notes")

    _, records, _ = _run(monkeypatch, tmp_path, answer)

    tools_events = [ev for kind, ev in records["events"] if kind == "tool"]
    assert [(ev.get("subquestion"), ev.get("round")) for ev in tools_events] == \
        [(q, 1) for q in QUESTIONS]
    # The engine's own tag is still on the same event, added to and not replaced.
    assert {ev.get("node") for ev in tools_events} == {"research"}


# --- 5: the page can see what each node did -----------------------------------
#
# `node_end` carries the KEYS of a node's output and never the values
# (test_graph_stream.py pins that key set), so "plan 428ms subquestions message"
# is all a card could ever say about the plan. What it split the topic into, and
# what each sub-agent was handed, has to travel on events this workflow emits
# itself. These tests are about those two events.

def test_the_plan_is_announced_with_the_subquestions_it_split_the_topic_into(
        monkeypatch, tmp_path):
    """Without this the plan card can only say the planner produced a field
    called `subquestions` — the user asked to see WHAT it split into."""
    _, records, _ = _run(monkeypatch, tmp_path, _notes)

    plans = [ev for kind, ev in records["events"] if kind == "plan_ready"]
    assert len(plans) == 1, "one plan, announced once"
    assert plans[0]["subquestions"] == QUESTIONS
    assert plans[0]["node"] == "plan", "the engine's own tag rides along"


def test_each_round_reports_one_row_per_subagent_in_plan_order(monkeypatch, tmp_path):
    """One row per agent, and the rows come out in the order the PLAN listed
    them — the workers finish in whatever order they finish in, so a page that
    sorted by arrival would shuffle the lines on every run. The counts come off
    the tool records: what an agent said it did is not evidence (rule 1)."""
    def answer(**kwargs):
        if "earlier rounds already established" in kwargs["messages"][0]["content"]:
            return LoopResult(reply="nothing new")   # round 2 finds nothing → stop
        index = QUESTIONS.index(_question_of(kwargs))
        return LoopResult(reply=f"notes {index}", iterations=index + 2,
                          tool_calls=[_call(f"https://r{index}.example/a"),
                                      {"tool": "search_web", "args": {"query": "q"},
                                       "output": "https://r.example/s"},
                                      _call(f"https://r{index}.example/b")])

    _, records, _ = _run(monkeypatch, tmp_path, answer)

    rows = _round_rows(records)
    assert len(rows) == 2, "round 1 found new pages, round 2 found none"
    assert [a["subquestion"] for a in rows[0]] == QUESTIONS
    assert [a["searches"] for a in rows[0]] == [1, 1, 1]
    assert [a["reads"] for a in rows[0]] == [2, 2, 2]
    assert [a["iterations"] for a in rows[0]] == [2, 3, 4]
    # Round 2's rows are its OWN, not last round's left hanging on the card.
    assert [a["searches"] for a in rows[1]] == [0, 0, 0]


def test_a_starved_subagent_says_so_in_its_own_row(monkeypatch, tmp_path):
    """The report names a starved question; the card has to point at WHICH agent
    it was, otherwise "this round did not finish" has no address."""
    def answer(**kwargs):
        starved = _question_of(kwargs) == QUESTIONS[1]
        return LoopResult(reply="partial notes", hit_limit=starved,
                          iterations=8 if starved else 3)

    _, records, _ = _run(monkeypatch, tmp_path, answer)

    rows = _round_rows(records)[0]
    assert [a["hit_limit"] for a in rows] == [False, True, False]
    assert [a["iterations"] for a in rows] == [3, 8, 3]
    assert [a["max_iterations"] for a in rows] == [binder.MAX_ITERATIONS] * 3


def test_a_worker_that_blows_up_still_gets_a_row(monkeypatch, tmp_path):
    """The failing agent costs its own sub-question and nothing else — and it
    must not vanish from the card either, or the round looks like it only had
    two agents and the missing one's absence has no explanation."""
    def answer(**kwargs):
        if _question_of(kwargs) == QUESTIONS[1]:
            raise RuntimeError("search backend on fire")
        return LoopResult(reply="notes")

    _, records, _ = _run(monkeypatch, tmp_path, answer)

    rows = _round_rows(records)[0]
    assert len(rows) == 3, "the failed worker vanished from the card"
    assert "search backend on fire" in rows[1]["error"]
    assert rows[1]["hit_limit"] is False, "a crash is not a budget exhaustion"
    assert [a["error"] for a in rows] == ["", rows[1]["error"], ""]


# --- 6: the two knobs on the page ---------------------------------------------

def test_the_pages_iteration_knob_reaches_run_loop(monkeypatch, tmp_path):
    """`max_iterations` is the whole point of the knob — a page that posts a
    number nothing reads is a control that does nothing. The receipt has to
    carry the same number, because the row prints "往返 3/3" and the denominator
    can only come from here."""
    _, records, _ = _run(monkeypatch, tmp_path, _notes,
                         budget={"iterations": 3, "rounds": 2})

    assert [k["max_iterations"] for k in records["loops"]] == [3] * len(QUESTIONS)
    assert [a["max_iterations"] for a in _round_rows(records)[0]] == [3] * len(QUESTIONS)


def test_a_run_through_the_real_entry_point_forwards_its_own_events(
        monkeypatch, tmp_path):
    """Driving `run_deep_research` is the only way to test the FORWARDED set —
    the helper above calls run_graph directly and sees every kind there is, so
    a custom kind left out of that set would look fine there and reach the
    browser never. Same for the knobs: `budget` is threaded through this
    function, and the graph it builds is the one that runs."""
    _, records, _ = _run(monkeypatch, tmp_path, _notes,
                         budget={"iterations": 5, "rounds": 1}, via_page=True)

    kinds = [kind for kind, _ in records["events"]]
    assert "plan_ready" in kinds
    assert "research_round" in kinds
    assert records["loops"][0]["max_iterations"] == 5
    # One round and no more: nothing new was found, but the knob said 1 anyway.
    assert len(_round_rows(records)) == 1


def test_the_round_knob_raises_the_guard_that_watches_it(monkeypatch, tmp_path):
    """`max_visits` is built from the same knob, ALWAYS one above it. Equal would
    mean the guard fires on the last legitimate round and that wave comes back
    EMPTY — the run ends with no report and only a line in `errors` (rule 2)."""
    assert binder.build_bound_graph(_FakeKnowme(tmp_path)) \
        .nodes["research"].max_visits == binder.MAX_ROUNDS + 1
    assert binder.build_bound_graph(_FakeKnowme(tmp_path), {"rounds": 2}) \
        .nodes["research"].max_visits == 3


def test_the_budget_is_clamped_at_the_door():
    """The page clamps too, but the page is not to be trusted: /api/graph/stream
    takes any POST, and `rounds: 100000` is real money."""
    assert binder._clean_budget(None) == {}
    assert binder._clean_budget("nope") == {}
    assert binder._clean_budget({}) == {}
    assert binder._clean_budget({"iterations": 3, "rounds": 2}) == \
        {"iterations": 3, "rounds": 2}
    # Out of range comes back inside it; junk is dropped rather than defaulted,
    # so one bad field does not throw away the other good one.
    assert binder._clean_budget({"iterations": 100000, "rounds": 0}) == \
        {"iterations": 20, "rounds": 1}
    assert binder._clean_budget({"iterations": 2.7, "rounds": True}) == {"iterations": 2}
    assert binder._clean_budget({"iterations": None, "rounds": "4"}) == {}

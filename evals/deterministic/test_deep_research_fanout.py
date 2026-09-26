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
    used — `run_loop` is what would call it, and that is patched out."""

    def __init__(self, home):
        self.client = object()
        self.settings = SimpleNamespace(small_model="small", model="big", home=home)
        self.tools = _FakeTools()


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


def _run(monkeypatch, tmp_path, answer, plan: str = PLAN_JSON):
    """Run the real bound graph and hand back (final state, records, fake).

    `answer(**kwargs) -> LoopResult` stands in for one sub-agent's whole turn. It
    is handed exactly what run_loop would get, so a test can read the prompt,
    fire the observer, or raise. Records hold every turn's kwargs, every event
    that reached the observer, and the lock probe.
    """
    records = {"loops": [], "events": [], "lock": _LockProbe()}

    def fake_run_loop(**kwargs):
        records["loops"].append(kwargs)
        return answer(**kwargs)

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
    state = run_graph(binder.build_bound_graph(knowme), {"topic": "固态电池"},
                      observer=lambda kind, ev: records["events"].append((kind, ev)))
    return state, records, knowme


def _question_of(kwargs) -> str:
    """Which sub-question this worker owns. Exactly one is in its prompt."""
    prompt = kwargs["messages"][0]["content"]
    return next(q for q in QUESTIONS if q in prompt)


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

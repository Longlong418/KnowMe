"""`/deep_research <topic>` — go and find out, then write it down.

A topic is not a question. "固态电池的产业化进度" has no single answer to look
up, which is why this is a workflow and not a tool call: split it up, search,
read the pages that look substantive, notice what the round did NOT cover, go
again, then write a report with the sources in it.

    python -m knowme deep_research "固态电池的产业化进度"
    /deep_research 固态电池的产业化进度          (in the chat box)

This module is the ONE place where real callables meet the pure workflow in
knowme/graph/workflows/deep_research.py. Both the CLI and the dashboard's
/api/graph/stream come through build_bound_graph, so there is exactly one
definition of what a deep research run is allowed to touch.

WHAT IT MAY TOUCH
    Two tools — search_web and read_webpage — and nothing else. The sub-agent
    gets them as an allowlist (ToolRegistry.subset), so it is not "told" to stay
    on the web; it is UNABLE to do anything else. No calendar, no messages, no
    knowledge writes, no filesystem. The only writes in the whole run are the
    report itself: one knowledge note and one markdown file in the outbox.

ONE SUB-AGENT PER SUB-QUESTION, IN PARALLEL
    A round does not hand the whole plan to one agent. The plan is DATA
    (state["subquestions"]) and each entry gets its own agent, its own budget,
    and its own thread. The reason is a budget failure that was observed in a
    real report: with one agent covering four sub-questions in one 8-iteration
    turn, the searches ate the budget and the pages were never opened — the
    agent then honestly wrote "the pages could not be read", which reads like a
    gap in the world rather than a gap in this run.

    The fan-out lives HERE, inside the round's own node, and that is forced by
    the engine: a node can only be re-entered through a router jump and the
    dependency rule requires runs[n] == 0 (engine.py:126-137), so a round split
    across several graph nodes could never run a second round. Keeping it inside
    the node leaves the topology — and the byte-frozen chart — untouched.

TWO KNOBS, AND WHAT THE PAGE CAN SEE
    A run started from the dashboard carries a `budget` — "how many round-trips
    may one sub-agent spend" and "how many rounds may the whole thing take" —
    clamped at the door by _clean_budget, because the browser's copy of a number
    is a suggestion. The CLI and `/deep_research` send none and get the module
    defaults, which is why both live here as constants with one reader each.

    The page also gets to see what each node DID, which the engine cannot tell
    it: `node_end` carries the KEYS of the dict a node returned and never the
    values, so a card built from engine events alone can say "plan took 428ms and
    produced `subquestions message`" and not which sub-questions those were. The
    two facts a reader actually wants — the plan, and what each sub-agent was
    sent after — therefore travel on this module's OWN events (`plan_ready`,
    `research_round`), whose payloads stay bounded: a plan, and one receipt line
    per sub-agent. No model prose, and no tool output, ever rides them.

WHY EACH ROUND TAKES agent_lock
    A model call raises llm events and a tool call raises tool events, and
    data.py files every llm/tool event that arrives while a turn is open under
    that turn, with no filter for which workflow emitted it (data.py:168-182).
    A normal turn holds agent_lock for its whole life (runtime.py:107), so
    holding it around each round means a chat turn can never be open while this
    run is emitting — otherwise a two-minute research round would show up as
    tool calls the user's chat message never made. Per ROUND, not per run: a
    chat message should not be blocked for two minutes waiting for research.

    The lock is taken ONCE around the whole wave, never inside a worker:
    agent_lock is a plain threading.Lock and therefore NOT reentrant, so a child
    thread acquiring what its parent already holds is a deadlock, not a wait.
    Fanning out inside one round also helps this guarantee rather than straining
    it — a round now takes max(sub-agents) instead of sum(them).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from rich.console import Console

from knowme.app import KnowMe
from knowme.core.loop import run_loop
from knowme.graph import run_graph
from knowme.graph.workflows.deep_research import (
    MAX_ROUNDS,
    PLAN_PROMPT,
    SUBAGENT_PROMPT,
    SYNTH_PROMPT,
    build_deep_research_graph,
    sources_from_tool_calls,
)
from knowme.ops.browser_agent import agent_lock
from knowme.tools.knowledge import create_note

# The allowlist. An invariant a reviewer can check by eye, and one the tests
# assert against this file's source.
SUBAGENT_TOOLS = frozenset({"search_web", "read_webpage"})
# PER SUB-AGENT, not per round. Each agent owns exactly one sub-question, so this
# is roughly two or three searches plus the four or five pages they lead to —
# which is what it was always meant to be. The window in read_webpage is what
# keeps it affordable; the loop resends the whole message list every iteration
# (see that tool's docstring).
MAX_ITERATIONS = 8

# Prefixed to the notes of a sub-agent that ran out of iterations. The synthesizer
# is told (SYNTH_PROMPT) to name the sub-question this appears under, because a
# gap in THIS RUN and a gap in the EVIDENCE are different claims and only one of
# them tells the reader what to do next.
BUDGET_SPENT = ("（这个子问题**没查完**：子 agent 的模型往返预算用尽了，"
                "下面只是它已经拿到的部分。）")

# What the page's two knobs are allowed to be, clamped on THIS side of the wire.
# /api/graph/stream is a plain POST endpoint, so the browser's copy of the number
# is a suggestion: `rounds: 100000` from a hand-made request is real money, and
# `iterations: 1` is a sub-agent that searches and then stops. The low ends are
# what a sub-agent needs to do anything at all — one search, one page.
BUDGET_LIMITS = {"iterations": (2, 20), "rounds": (1, 6)}


def _clean_budget(raw) -> dict:
    """The page's knobs, clamped, with anything unrecognised dropped.

    Returns {} when there is nothing usable, and every reader then falls back to
    the module default — so a run started from the CLI or from `/deep_research`,
    which send no budget at all, is exactly the run it was before the knobs
    existed. Anything not an int (a string, a list, a bool) falls back too; a
    bool is an int in Python, and `rounds: true` must not mean one round.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for name, (low, high) in BUDGET_LIMITS.items():
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[name] = max(low, min(high, int(value)))
    return out


def _knob(budget, name: str, default: int) -> int:
    """One clamped knob, or `default` when this run brought none."""
    value = (budget or {}).get(name)
    return value if isinstance(value, int) else default

USAGE = ("`/deep_research` needs a topic to research, not just the command.\n\n"
         "Try `/deep_research 固态电池的产业化进度` — it will plan sub-questions, "
         "search, read, and write you a cited report.")

SYSTEM = """You are a research agent. You are given a topic and ONE sub-question
of it — another agent is working the other sub-questions at the same time — and
you have exactly two tools: search_web and read_webpage.

How to work:
- Search first, then OPEN the pages that look substantive. A snippet is a
  pointer, not evidence, and you may not cite what you did not read.
- Answer YOUR sub-question, but follow what the sources actually say: if they
  point somewhere more interesting and relevant, go there.
- Write down the URL for every fact as you go. A finding without a source is
  not usable in the report.
- If you cannot find something, say so plainly. "I could not find X" is a real
  answer and a useful one; inventing X is not.
- Be brief. Notes with sources, not prose."""


def _settings_model(knowme: KnowMe) -> tuple[str, str]:
    """(planning model, working model). Planning is a structured-output call —
    a small model does it well and cheaply; the round and the report are where
    quality shows, so they get the main one."""
    return (knowme.settings.small_model or knowme.settings.model,
            knowme.settings.model)


def _one_shot(knowme: KnowMe, model: str, prompt: str, max_tokens: int) -> str:
    """One model call with NO tools parameter. That absence is the guarantee:
    a model with no tool schemas cannot call a tool."""
    response = knowme.client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


def _one_subagent(*, client, model: str, tools, brief: str, subquestion: str,
                  round_no: int, max_iterations: int, notify) -> dict:
    """One sub-agent: one sub-question, its own budget, its own loop turn.

    THIS FUNCTION NEVER RAISES. Its result is read with `f.result()`, which
    re-raises whatever the worker raised; a sub-agent that blows up must cost its
    own sub-question and nothing else. The other agents in this wave have already
    paid for their searches, and the round still has to hand the synthesizer
    something — same posture as `_try` in the workflow.

    What it hands back beyond the notes is a RECEIPT — iterations used, whether
    it ran out, how long it took — because the page draws one line per sub-agent
    and a round's totals cannot be broken back down into those lines: `_merge`
    sees one merged reply, not three agents' worth of turns.
    """
    def told(kind: str, ev: dict) -> None:
        # Tag the event with the sub-question it belongs to. Without this every
        # agent in the wave writes `node: research` and the trace cannot say
        # WHICH question ran out of budget — which is the whole point of
        # reporting the gap honestly. `notify` is the engine's notifier, already
        # wrapped in a lock (engine.py:108-113), so concurrent writes stay
        # whole-line; this only adds keys to the dict it is handed.
        if notify:
            notify(kind, {**ev, "subquestion": subquestion, "round": round_no})

    started = time.perf_counter()
    try:
        result = run_loop(
            client=client, model=model, system=SYSTEM,
            messages=[{"role": "user", "content": SUBAGENT_PROMPT.format(
                brief=brief, subquestion=subquestion)}],
            tools=tools, max_iterations=max_iterations, max_tokens=2048,
            observer=told)
    except Exception as exc:  # noqa: BLE001 — one agent's failure is not the round's
        why = f"{type(exc).__name__}: {exc}"
        return {"subquestion": subquestion,
                "reply": f"(这个子问题失败了：{why})", "tool_calls": [],
                "hit_limit": False, "iterations": 0, "ms": _ms_since(started),
                "max_iterations": max_iterations, "error": why}
    return {"subquestion": subquestion, "reply": result.reply,
            "tool_calls": result.tool_calls, "hit_limit": result.hit_limit,
            "iterations": result.iterations, "ms": _ms_since(started),
            "max_iterations": max_iterations, "error": ""}


def _ms_since(started: float) -> int:
    """Milliseconds since a perf_counter() reading, as a whole number — the same
    shape the engine reports a node's `ms` in, so a card can print both."""
    return int((time.perf_counter() - started) * 1000)


def _merge(outs: list[dict]) -> dict:
    """The round's reply, sources and per-agent receipts, out of one answer per
    sub-question.

    The sources are counted ONCE over the concatenated tool records, so
    `sources_from_tool_calls` stays the single authority on what a round touched
    (rule 1 in the workflow's docstring) and its de-duplication also covers the
    case where two sub-agents read the same page — which the router would
    otherwise count twice as "new".

    A starved sub-agent gets BUDGET_SPENT in front of its notes: its reply is
    loop.py's canned apology, not a finding, and SYNTH_PROMPT is told to name it.

    `agents` is what the page draws, one line per sub-agent, IN THE ORDER THE
    PLAN LISTED THEM — `outs` comes straight from the futures list, which the
    caller built in that order, so the rows do not move around as the workers
    finish in whatever order they finish in. The two counts come off the tool
    records, never off the reply (rule 1 again): what a sub-agent SAID it did is
    not evidence.
    """
    parts, agents = [], []
    for out in outs:
        body = (out.get("reply") or "").strip() or "(没有写出任何笔记)"
        if out.get("hit_limit"):
            body = f"{BUDGET_SPENT}\n\n{body}"
        parts.append(f"### {out.get('subquestion', '')}\n\n{body}")
        calls = out.get("tool_calls") or []
        agents.append({
            "subquestion": out.get("subquestion", ""),
            "searches": sum(1 for c in calls if c.get("tool") == "search_web"),
            "reads": sum(1 for c in calls if c.get("tool") == "read_webpage"),
            "iterations": out.get("iterations") or 0,
            # The ceiling this run gave it, so the page can print "8/8" and mean
            # it. It travels with the receipt because only the binder knows it —
            # it comes from the page's knob, and the workflow has no business
            # knowing what a sub-agent's iteration budget is.
            "max_iterations": out.get("max_iterations") or 0,
            "hit_limit": bool(out.get("hit_limit")),
            "error": out.get("error") or "",
            "ms": out.get("ms") or 0,
        })
    return {"reply": "\n\n".join(parts),
            "sources": sources_from_tool_calls(
                [c for out in outs for c in (out.get("tool_calls") or [])]),
            "agents": agents}


def build_bound_graph(knowme: KnowMe, budget: dict | None = None):
    """The pure workflow, wired to this machine.

    `budget` is the page's two knobs, ALREADY CLAMPED (run_deep_research does that
    once, at the door). It reaches the graph in the one place a graph can be
    configured — the round node's `max_visits`, which must stay one above the
    round budget the router enforces (the workflow's rule 2). Everything else
    reads the budget out of the run's own state.
    """
    plan_model, work_model = _settings_model(knowme)
    settings = knowme.settings

    def plan_fn(state: dict) -> str:
        return _one_shot(knowme, plan_model,
                         PLAN_PROMPT.format(topic=state.get("topic", ""), n=4), 500)

    def research_fn(state: dict) -> dict:
        """One round: one sub-agent PER sub-question, in parallel, merged.

        The fan-out is here, inside the round's single node, because the engine
        can only re-enter a node through a router jump (see the module
        docstring): splitting a round across several graph nodes would cost the
        second round. The topology is unchanged; the width is a runtime decision.
        """
        questions = [str(q).strip() for q in (state.get("subquestions") or [])
                     if str(q).strip()]
        if not questions:   # a planner that failed still has the topic to search
            questions = [str(state.get("topic") or "").strip()]
        # ONE subset call, shared by every worker. Built here rather than inside
        # the worker because the registry is a read-only lookup once built
        # (core/tools.py:75-96) and the tools are stateless functions — and
        # because building it per worker would be N identical registries.
        tools = knowme.tools.subset(SUBAGENT_TOOLS)
        brief = state.get("message") or ""
        round_no = (state.get("round") or 0) + 1
        notify = state.get("_notify")
        # Each agent's OWN ceiling, from the page's knob (already clamped), or the
        # module default for a run that brought none. Every agent in the wave gets
        # the same one — the fan-out is not a shared budget, it is N of them.
        per_agent = _knob(state.get("budget"), "iterations", MAX_ITERATIONS)
        # ONE lock for the whole wave, never inside a worker: agent_lock is a
        # plain threading.Lock and NOT reentrant, so a child acquiring what its
        # parent holds deadlocks instead of waiting. Same scope as before — per
        # round, not per run (see the module docstring). Lock first, then pool,
        # because the combined `with` exits in reverse: the pool drains (its
        # __exit__ waits for every worker) before the lock is released.
        with agent_lock, ThreadPoolExecutor(max_workers=len(questions)) as pool:
            futures = [pool.submit(
                _one_subagent, client=knowme.client, model=work_model,
                tools=tools, brief=brief, subquestion=q, round_no=round_no,
                max_iterations=per_agent, notify=notify) for q in questions]
            # Read in sub-question order, so the merged notes read in the order
            # the plan listed them rather than in completion order.
            outs = [f.result() for f in futures]
        return _merge(outs)

    def synth_fn(state: dict) -> str:
        return _one_shot(knowme, work_model, SYNTH_PROMPT.format(
            topic=state.get("topic", ""),
            findings=(state.get("findings") or "(nothing was gathered)"),
            sources="\n".join(state.get("seen") or []) or "(none recorded)",
        ), 4000)

    def save_fn(state: dict) -> dict:
        """The two writes. Neither may raise: by the time this runs the report
        has already been paid for, and a failed note must not turn a finished
        run into an empty one (gather's rule 2, the same silence)."""
        report = state.get("digest") or ""
        out: dict = {}
        try:
            note = create_note(knowme.conn, _note_title(state.get("topic", "")),
                               folder="research", content=report, agent_id="research")
            out["note_id"] = note["id"]   # Note is a TypedDict, not an object
        except Exception as exc:  # noqa: BLE001 — report first, note second
            out["note_error"] = f"{type(exc).__name__}: {exc}"
        try:
            out["draft_path"] = _write_report(settings.home, state, report)
        except Exception as exc:  # noqa: BLE001
            out["file_error"] = f"{type(exc).__name__}: {exc}"
        return out

    return build_deep_research_graph(
        plan_fn=plan_fn, research_fn=research_fn, synth_fn=synth_fn,
        save_fn=save_fn,
        # One ABOVE the round budget, never equal to it: a jump target over its
        # max_visits is dropped from the wave silently, and an empty wave ends the
        # run before synthesize — which would mean no report at all, only a line
        # in errors, after the whole thing was paid for (workflow rule 2).
        max_visits=_knob(budget, "rounds", MAX_ROUNDS) + 1)


def _slug(topic: str, limit: int = 40) -> str:
    """A filename-shaped version of the topic. Chinese survives (it is a
    perfectly good filename character); punctuation and spaces do not."""
    cleaned = re.sub(r"[^\w一-鿿]+", "-", topic).strip("-")
    return cleaned[:limit] or "research"


def _note_title(topic: str) -> str:
    # The date is in the title on purpose: running the same topic next week
    # should not be an indistinguishable second note in the knowledge base.
    return f"深度研究：{topic[:60]} · {date.today().isoformat()}"


def _write_report(home: Path, state: dict, report: str) -> str:
    """Write the report where a human will find it. Resolves the path and checks
    it is inside the outbox before writing — same posture as ops/gather.py's
    _draft, which drafts and never sends."""
    outbox = (home / "outbox").resolve()
    outbox.mkdir(parents=True, exist_ok=True)
    dest = (outbox / f"research-{_slug(state.get('topic', ''))}"
                     f"-{date.today().isoformat()}.md").resolve()
    if outbox not in dest.parents:
        return "refused: report path escaped the outbox"
    sources = state.get("seen") or []
    body = report
    if sources and "Sources" not in report and "来源" not in report:
        body += "\n\n## Sources\n\n" + "\n".join(f"- {u}" for u in sources)
    dest.write_text(body + "\n", encoding="utf-8")
    return str(dest)


def run_deep_research(knowme: KnowMe | None = None, observer=None,
                      message: str = "", budget=None) -> dict:
    """Run one deep research job to completion. Returns the final state.

    The `message` parameter name is load-bearing: commands.run inspects the
    signature for it and passes `/deep_research <topic>`'s argument through
    (commands.py:96-108). No argument means no topic, and a research run with no
    topic is a paid round trip to find nothing — so say so instead of starting.

    `budget` is the page's two knobs and comes only from the dashboard:
    graph_stream passes it when the runner declares the parameter
    (runtime.py:221-225). The CLI and `/deep_research` never pass one, so they
    keep the module defaults. It is clamped HERE, once, before anything reads it.

    The observer is composed with the tracer, so the run lands in traces/*.jsonl
    like any turn — which is what lights the topology chart: the dashboard's
    /api/events poll animates from the trace, not from this stream.
    """
    topic = (message or "").strip()
    if not topic:
        return {"digest": USAGE}
    clean = _clean_budget(budget)
    own = knowme is None
    knowme = knowme or KnowMe()
    try:
        # graph_stream's docstring makes a promise, and it is worth keeping
        # exactly: the ENGINE's events carry no node OUTPUT, so a digest can
        # never leak into a frame. A round's llm/tool events DO carry output
        # (tool results, model text), so they go to the trace and not to the
        # stream — that part is unchanged.
        #
        # The last two kinds are this workflow's own, and they are the exception
        # the promise needs to name: `plan_ready` (the sub-questions the planner
        # produced) and `research_round` (one row per sub-agent). Both are
        # BOUNDED — at most 5 short questions and 5 rows of four numbers, no
        # model prose and no tool output — and neither exists for any other
        # workflow. They go out because a card cannot show what a node DID from
        # `node_end`, which carries the keys of a node's output and never values.
        forwarded = {"graph_start", "node_start", "node_end", "route", "graph_end",
                     "plan_ready", "research_round"}

        def notify(kind: str, ev: dict) -> None:
            knowme.tracer.event(kind, ev)
            if observer and kind in forwarded:
                observer(kind, ev)

        return run_graph(build_bound_graph(knowme, clean),
                         {"topic": topic, "budget": clean}, observer=notify)
    finally:
        if own:
            knowme.close()


def main(topic: str = "") -> None:
    """`python -m knowme deep_research "<topic>"`. The CLI passes the topic in;
    knowme/__main__.py stays the one place that knows about sys.argv."""
    console = Console()
    topic = (topic or "").strip()
    if not topic:
        console.print(USAGE)
        return
    knowme = KnowMe()
    try:
        console.print(f"[dim]researching: {topic} — 规划、检索、阅读、汇总，"
                      f"最多三轮…[/dim]")
        state = run_deep_research(knowme, message=topic)
        console.print(state.get("digest") or "(no report)")
        if state.get("draft_path"):
            console.print(f"[dim]saved to {state['draft_path']}[/dim]")
        if state.get("note_id"):
            console.print(f"[dim]note id {state['note_id']}[/dim]")
        for node, err in (state.get("errors") or {}).items():
            console.print(f"[dim]{node}: {err}[/dim]")
    finally:
        knowme.close()


if __name__ == "__main__":
    main()

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

WHY EACH ROUND TAKES agent_lock
    A model call raises llm events and a tool call raises tool events, and
    data.py files every llm/tool event that arrives while a turn is open under
    that turn, with no filter for which workflow emitted it (data.py:168-182).
    A normal turn holds agent_lock for its whole life (runtime.py:107), so
    holding it around each round means a chat turn can never be open while this
    run is emitting — otherwise a two-minute research round would show up as
    tool calls the user's chat message never made. Per ROUND, not per run: a
    chat message should not be blocked for two minutes waiting for research.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from rich.console import Console

from knowme.app import KnowMe
from knowme.core.loop import run_loop
from knowme.graph import run_graph
from knowme.graph.workflows.deep_research import (
    PLAN_PROMPT,
    SYNTH_PROMPT,
    build_deep_research_graph,
    sources_from_tool_calls,
)
from knowme.ops.browser_agent import agent_lock
from knowme.tools.knowledge import create_note

# The allowlist. An invariant a reviewer can check by eye, and one the tests
# assert against this file's source.
SUBAGENT_TOOLS = frozenset({"search_web", "read_webpage"})
# Eight iterations is four or five searches plus the pages they lead to. The
# window in read_webpage is what keeps this affordable; the loop resends the
# whole message list every iteration (see that tool's docstring).
MAX_ITERATIONS = 8

USAGE = ("`/deep_research` needs a topic to research, not just the command.\n\n"
         "Try `/deep_research 固态电池的产业化进度` — it will plan sub-questions, "
         "search, read, and write you a cited report.")

SYSTEM = """You are a research agent. You are given a topic and a set of
sub-questions, and you have exactly two tools: search_web and read_webpage.

How to work:
- Search first, then OPEN the pages that look substantive. A snippet is a
  pointer, not evidence, and you may not cite what you did not read.
- Cover the sub-questions, but follow what the sources actually say: if they
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


def build_bound_graph(knowme: KnowMe):
    """The pure workflow, wired to this machine."""
    plan_model, work_model = _settings_model(knowme)
    settings = knowme.settings

    def plan_fn(state: dict) -> str:
        return _one_shot(knowme, plan_model,
                         PLAN_PROMPT.format(topic=state.get("topic", ""), n=4), 500)

    def research_fn(state: dict) -> dict:
        """One round: a real loop turn with the two web tools and nothing else."""
        tools = knowme.tools.subset(SUBAGENT_TOOLS)
        with agent_lock:   # see the module docstring — not a performance choice
            result = run_loop(
                client=knowme.client, model=work_model, system=SYSTEM,
                messages=[{"role": "user", "content": state.get("message") or ""}],
                tools=tools, max_iterations=MAX_ITERATIONS, max_tokens=2048,
                observer=state.get("_notify"))
        # The sources come off the tool RECORDS, not the reply's prose — this is
        # what the router counts (rule 1 in the workflow's docstring).
        return {"reply": result.reply,
                "sources": sources_from_tool_calls(result.tool_calls)}

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

    return build_deep_research_graph(plan_fn=plan_fn, research_fn=research_fn,
                                     synth_fn=synth_fn, save_fn=save_fn)


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
                      message: str = "") -> dict:
    """Run one deep research job to completion. Returns the final state.

    The `message` parameter name is load-bearing: commands.run inspects the
    signature for it and passes `/deep_research <topic>`'s argument through
    (commands.py:96-108). No argument means no topic, and a research run with no
    topic is a paid round trip to find nothing — so say so instead of starting.

    The observer is composed with the tracer, so the run lands in traces/*.jsonl
    like any turn — which is what lights the topology chart: the dashboard's
    /api/events poll animates from the trace, not from this stream.
    """
    topic = (message or "").strip()
    if not topic:
        return {"digest": USAGE}
    own = knowme is None
    knowme = knowme or KnowMe()
    try:
        # graph_stream's docstring makes a promise: only the engine's own events
        # go out, and they carry no node OUTPUT, so a payload can never leak into
        # a frame. A round's llm/tool events DO carry output (tool results, model
        # text), so they go to the trace and not to the stream.
        forwarded = {"graph_start", "node_start", "node_end", "route", "graph_end"}

        def notify(kind: str, ev: dict) -> None:
            knowme.tracer.event(kind, ev)
            if observer and kind in forwarded:
                observer(kind, ev)

        return run_graph(build_bound_graph(knowme), {"topic": topic}, observer=notify)
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

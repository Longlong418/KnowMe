"""deep_research — a topic goes in, a cited report comes out, and it goes back
for more when a round actually found something new.

WHAT MAKES IT "DEEP"
    One search is a lookup. This is a loop with a budget: split the topic into
    sub-questions, run a round that searches and reads pages, then ask whether
    that round ADDED anything. If it did, run another round — told what is
    already covered so it does not re-read it. If it did not, stop. Spending a
    third round re-reading the same three pages is the precise failure mode this
    exists to avoid.

WHY THE LOOP IS A SINGLE NODE
    The engine re-runs a node only through a router/on_error JUMP
    (engine.py:126-137): the dependency rule additionally requires runs[n] == 0,
    so a node whose in-edges have already fired can never be re-entered. A loop
    body of several nodes therefore cannot repeat itself — only the single node
    a router points at can. So one round is ONE node (the sub-agent turn, start
    to finish) and the router sits on it. Anything else would look right in
    describe() and deadlock at runtime.

1. THE ROUTER IS CODE, OVER A COUNT — NEVER THE MODEL'S OPINION.
    "Routers are code, never models" is the engine's thesis (engine.py:16-18),
    and gather's needs_action reads counts the scans wrote (gather.py:97-108).
    The tempting design here is to have the sub-agent report "gaps: none" and
    route on that — which hands control flow to the model, makes the loop
    untestable without a scripted response per case, and is trivially defeated
    by a model that would rather stop. So the signal is `sources_new`: how many
    URLs this round touched that no earlier round had. Model prose cannot fake
    it, because it is counted from what the TOOLS returned (wrapped in
    knowme/ops/deep_research.py — see _source_recorder there), not from the
    reply. The gap list still exists, as the next round's PROMPT CONTENT, never
    as the control input.

2. THE ROUND BUDGET BELONGS TO THE ROUTER, NOT TO max_visits.
    When a jump target is over its max_visits the engine drops it from the wave
    silently (engine.py:133-135), records errors[name], and returns []. An empty
    wave ends the run — so if max_visits were the brake, the loop would exit
    WITHOUT ever running synthesize and there would be no report at all, only a
    line in errors. Silence is the worst failure mode for a job that has already
    spent money (same rule as gather's rule 2). `stop_reason()` therefore decides
    from the round count, and max_visits is set one ABOVE that count so it can
    only ever mean "there is a bug".

3. EVERY EXPENSIVE BRANCH LANDS SOMEWHERE.
    A round that blows up still writes honest text, so synthesize has something
    to work with; on_error="synthesize" is the second backstop for a defect in
    this file. A run that searched for two minutes and produced nothing is worse
    than a run that says "the search failed".
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from knowme.graph.engine import END, START, Graph, Node

# The round budget the ROUTER enforces. max_visits is deliberately one higher:
# see rule 2 in the module docstring.
MAX_ROUNDS = 3
MAX_VISITS = MAX_ROUNDS + 1

PLAN_PROMPT = """You are preparing a research plan. The topic is:

{topic}

Write {n} to 5 focused sub-questions that together would answer it. Prefer
questions that a web search can actually answer, and prefer specific over broad.
Reply with ONLY a JSON array of strings, nothing else:

["first sub-question", "second sub-question"]"""

# What a round is told. Note what is NOT here: any instruction to report how
# confident it is or what is still missing. The round reports what it FOUND; the
# router counts URLs. See rule 1.
ROUND_PROMPT = """{topic}

The sub-questions to cover:
{subquestions}

{covered}Search the web for these, and open the pages that look substantive.

Rules:
- Search before you conclude anything is missing. Snippets are not evidence.
- Cite the URL you got each fact from, inline.
- If two sources disagree, say so rather than picking one.
- Be concise. Notes, not prose: what you found, and where you found it."""

COVERED = """What earlier rounds already established (do not re-research this,
build on it):

{findings}
"""

SYNTH_PROMPT = """Write a research report on this topic:

{topic}

Everything the research rounds gathered:

{findings}

The sources they touched:
{sources}

Write it in Markdown, in the language of the topic. Requirements:
- Lead with the answer, not with the process.
- Every non-obvious claim carries its source as a Markdown link.
- Where the sources disagree or the evidence is thin, say so plainly instead of
  smoothing it over. A report that admits a gap is worth more than one that
  hides it.
- End with a "Sources" list of the URLs actually used.
Do not invent a URL, a title, or a number that is not in the material above."""


def _try(fn: Callable[[], dict], fallback: dict) -> dict:
    """Run a round, or return honest text saying why it could not.

    Same posture as gather's _safe, and for the same reason: the run must drain
    to a report either way. _safe itself is not reused because it rewrites every
    string value in the fallback with "unavailable (…)", and one of ours is a
    list of URLs that must stay a list.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — the whole point is to not propagate
        return {**fallback,
                "reply": f"(this round failed: {type(exc).__name__}: {exc})"}


# A URL as it appears in text or in a tool argument. Trailing punctuation is
# trimmed after the match — a sentence-final "…per the docs (https://x.com/a)."
# would otherwise carry the paren and the full stop into the count.
_URL = re.compile(r"https?://[^\s)\]\"'<>]+")


def sources_from_tool_calls(tool_calls: list[dict]) -> list[str]:
    """The URLs a round actually TOUCHED, read off the tool records.

    This is the signal the router runs on (rule 1 in the module docstring), so
    where it comes from matters: ``LoopResult.tool_calls`` is what the tools
    returned (loop.py:109-113), not what the model said about them. A model that
    wants the run to stop cannot write this number down — it would have to avoid
    calling search_web and read_webpage, and a round that touches nothing IS a
    round that found nothing, which is exactly the case we want to stop on.

    ``read_webpage`` names its URL in the argument; ``search_web`` returns its
    results with the URL on its own line, so both are just text to this regex.
    """
    found: list[str] = []
    for call in tool_calls or []:
        args = call.get("args") or {}
        candidates = [call.get("output") or ""]
        if isinstance(args, dict) and args.get("url"):
            candidates.append(str(args["url"]))
        for text in candidates:
            for match in _URL.findall(text):
                url = match.rstrip(".,;:")
                if url not in found:
                    found.append(url)
    return found


def parse_plan(text: str, topic: str) -> list[str]:
    """The model's sub-questions, or one honest question covering the topic.

    The plan is a CONVENIENCE, not a dependency: a run whose planner returned
    prose instead of JSON should still search for the topic rather than die. So
    every failure path lands on [topic], which is the one sub-question that is
    always valid.
    """
    try:
        start, end = text.index("["), text.rindex("]")
        items = json.loads(text[start:end + 1])
        questions = [str(q).strip() for q in items if str(q).strip()]
        if questions:
            return questions[:5]
    except (ValueError, TypeError):   # json.JSONDecodeError is a ValueError
        pass
    # No JSON: accept a plain list, one per line. "- x" and "1. x" are what a
    # model writes when it ignores the format instruction.
    lines = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", ln).strip()
             for ln in text.splitlines()]
    questions = [ln for ln in lines if ln and not ln.endswith(":")][:5]
    return questions or [topic]


def stop_reason(state: dict) -> str:
    """The router's reason, or "" to keep going. CODE over counts — see rule 1.

    Every read is defensive on purpose: the engine calls this OUTSIDE any
    try/except (engine.py:189), so a KeyError here would escape run_graph
    entirely and take graph_end down with it.
    """
    round_no = state.get("round") or 0
    if round_no >= MAX_ROUNDS:
        return f"到轮次上限（{MAX_ROUNDS} 轮）"
    if not (state.get("sources_new") or 0):
        return "这一轮没有找到新的信源"
    return ""


def _next_prompt(state: dict) -> str:
    """What the next round is told: the plan, plus what is already covered."""
    findings = (state.get("findings") or "").strip()
    covered = COVERED.format(findings=findings[-3000:]) if findings else ""
    return ROUND_PROMPT.format(
        topic=state.get("topic", ""),
        subquestions="\n".join(f"- {q}" for q in (state.get("subquestions") or [])),
        covered=covered,
    )


def _start(state: dict, plan_fn: Callable[[dict], str]) -> dict:
    """The entry node: ask for sub-questions, then turn them into a plan.

    A planner that fails is not fatal — parse_plan falls back to the topic
    itself, which is always a valid sub-question. Failing open here mirrors
    triage's classify (triage.py:52-54): a broken planner should cost focus,
    never the run.
    """
    text = _try(lambda: {"plan_text": plan_fn(state)}, {"plan_text": ""})["plan_text"]
    plan = parse_plan(text or "", state.get("topic", ""))
    return {"subquestions": plan,
            "message": _next_prompt({**state, "subquestions": plan})}


def _round(state: dict, research_fn: Callable[[dict], dict]) -> dict:
    """One round: run the sub-agent, then count what is NEW. Pure bookkeeping.

    `research_fn` returns {"reply", "sources"} — the sources counted from what
    the tools touched, not from the reply's prose. The counting lives here so a
    test can drive it with a stub and no model at all.
    """
    out = _try(lambda: research_fn(state), {"reply": "", "sources": []})
    reply = out.get("reply") or ""
    sources = [u for u in (out.get("sources") or []) if isinstance(u, str)]
    seen = list(state.get("seen") or [])
    fresh = [u for u in sources if u not in seen]
    previous = (state.get("findings") or "").strip()
    findings = (previous + f"\n\n## 第 {(state.get('round') or 0) + 1} 轮\n\n" + reply
                if reply else previous)
    merged = {**state, "round": (state.get("round") or 0) + 1, "findings": findings}
    # Say out loud what the count was, because the count is what decides the next
    # round and a trace that only shows "again" teaches nothing. Same channel as
    # triage's classify uses for its reason (triage.py:99-101) — a custom kind on
    # the node's own notifier, so it lands in the trace/log and not in the chart.
    notify = state.get("_notify")
    if notify:
        notify("research_round", {
            "round": merged["round"], "sources_new": len(fresh),
            "sources_total": len(seen) + len(fresh),
            "note": (f"第 {merged['round']} 轮：新增 {len(fresh)} 个信源"
                     f"（累计 {len(seen) + len(fresh)} 个）")})
    return {
        "reply": reply,
        "findings": findings,
        "seen": seen + fresh,
        "sources_new": len(fresh),
        "round": merged["round"],
        # The next round's prompt. Written here, not by the router: a router
        # decides, it does not prepare input (gather's needs_action reads and
        # returns a label, nothing else).
        "message": _next_prompt(merged),
    }


def build_deep_research_graph(*, plan_fn: Callable[[dict], str],
                              research_fn: Callable[[dict], dict],
                              synth_fn: Callable[[dict], str],
                              save_fn: Callable[[dict], dict]) -> Graph:
    """Callables injected exactly as gather does it: the tests script them with
    lambdas, deep_research_topology() passes stubs to describe the shape without
    running it, and knowme/ops/deep_research.py binds the real ones.

    There is deliberately NO research → synthesize edge. A node carrying a
    router never fires its static out-edges (engine.py:187-200), so that edge
    could never be the reason synthesize runs — and describe() does not dedupe,
    so it would be drawn twice in the chart. The router's target IS the edge.
    """
    g = Graph("deep_research")

    g.add_node(Node("plan", lambda s: _start(s, plan_fn), kind="llm"))
    g.add_node(Node("research", lambda s: _round(s, research_fn), kind="agent",
                    max_visits=MAX_VISITS, on_error="synthesize"))
    g.add_node(Node("synthesize", lambda s: {"digest": synth_fn(s)}, kind="llm"))
    # The two writes: a note in the knowledge base and a file in the outbox.
    # Both are wrapped in the binder so a failure leaves the report standing.
    g.add_node(Node("save", save_fn, kind="tool"))

    g.add_edge(START, "plan")
    g.add_edge("plan", "research")
    g.add_router("research", lambda s: "done" if stop_reason(s) else "again",
                 {"again": "research", "done": "synthesize"})
    g.add_edge("synthesize", "save")
    g.add_edge("save", END)
    return g


def deep_research_topology() -> dict:
    """The topology as data, for the dashboard — built with stubs, never run."""
    return build_deep_research_graph(
        plan_fn=lambda s: "",
        research_fn=lambda s: {"reply": "", "sources": []},
        synth_fn=lambda s: "",
        save_fn=lambda s: {},
    ).describe()

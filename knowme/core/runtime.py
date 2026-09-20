"""AgentRuntime — one turn, for any agent.

This is what KnowMe.respond() and _run_full_turn() were, with the agent made a
parameter. The runtime owns nothing agent-specific: it is handed a client, a
connection, a memory, a tool registry and a tracer once, and then runs any
(AgentSpec, Session, message) through the same steps every time:

    run_turn
      ├─ front_door(message)          optional — the triage graph, fail-open
      └─ run_loop_turn
           ├─ session.build_system(spec.persona, spec.model)     stable
           ├─ session.build_turn_context(message, extra=...)     volatile
           ├─ spec.context_policy.fit(history)                   fit the window
           └─ run_loop(...)                                      THE loop
      ├─ meta: gate / graph / iterations / latency / tools / model
      ├─ session.add_exchange   memory.maybe_consolidate   memory.export_markdown
      └─ tracer.end_turn

Two seams exist for what comes next and are unused by the default agent:

  extra_context   text an Application contributes to this turn — the open
                  document, the selection (the Application Context Bridge)
  front_door      a callable that may answer the turn without the loop; None
                  from it means "not mine", and the loop runs as usual

The window size comes in as a function (`context_for`) rather than an import,
because it lives in knowme.ops.pricing and the core may not import ops.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from knowme.config import Settings
from knowme.core.context.policy import FitContext
from knowme.core.events import Observer, compose
from knowme.core.loop import LoopResult, run_loop
from knowme.core.session import Session
from knowme.core.spec import AgentSpec, resolve
from knowme.core.tools import ToolRegistry

FrontDoor = Callable[[str, Observer, bool], "LoopResult | None"]
ContextFor = Callable[[str, str], int]   # (provider, model) → window in tokens


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    iterations: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def as_loop_result(self) -> LoopResult:
        return LoopResult(reply=self.reply, tool_calls=self.tool_calls,
                          iterations=self.iterations)


def _status(output: str) -> str:
    low = (output or "").lower()
    return "error" if ("failed" in low or "timed out" in low or low.startswith("error")) else "ok"


class AgentRuntime:
    def __init__(self, settings: Settings, *, client, conn, memory, tools: ToolRegistry,
                 tracer, context_for: ContextFor):
        self.settings = settings
        self.client = client
        self.conn = conn
        self.memory = memory
        self.tools = tools
        self.tracer = tracer
        self.context_for = context_for

    def run_turn(self, spec: AgentSpec, session: Session, user_message: str, *,
                 observer: Observer | None = None, source: str = "cli",
                 stream: bool = False, extra_context: str = "",
                 front_door: FrontDoor | None = None) -> TurnResult:
        """One full turn: assemble working memory → run the loop → persist.
        `source` tags whether the message arrived through the CLI or dashboard,
        so the unified chat can show its origin. `stream=True` streams the reply
        text token by token to the observer. Everything that happens is both
        shown (observer) and recorded (tracer)."""
        resolved = resolve(spec, self.settings)
        # capture the gate + graph decisions as they flow by, so we can persist
        # them with the turn (the reopened-thread telemetry the dashboard shows)
        captured: dict = {}

        def _capture(kind, ev):
            if kind == "context":
                captured["context"] = {
                    "application_chars": ev.get("application_chars", 0),
                    "history_messages": ev.get("history_messages", 0),
                    "sent_messages": ev.get("sent_messages", 0),
                    "compaction": ev.get("compaction", []),
                }
            if kind == "gate":
                captured["gate"] = {"decision": ev.get("decision"), "reason": ev.get("reason")}
            if kind == "route":
                captured["graph_route"] = {"target": ev.get("target"), "reason": ev.get("reason")}
            if kind == "triage":
                captured["triage_reason"] = ev.get("reason")
            if kind == "graph_end":
                captured["graph_path"] = ev.get("path")
        notify = compose(observer, self.tracer.event, _capture)
        # turn_start/end are already written by Tracer.turn/end_turn.  Send
        # them only to the live observer + capture path here, otherwise each
        # boundary appears twice in JSONL and splits one run into two cards.
        live_notify = compose(observer, _capture)
        t0 = time.perf_counter()

        identity = {
            "agent_id": resolved.name,
            "session_id": session.session_id,
            "model": resolved.model,
            "source": source,
        }
        with self.tracer.turn(user_message, **identity):
            live_notify("turn_start", identity)
            # The front door is optional and can NEVER make a turn worse: none
            # → this is exactly the plain path; one → it may answer, and any
            # failure anywhere falls open to the loop below (same fail-open
            # rule as the retrieval gate).
            result = None
            if front_door is not None:
                try:
                    result = front_door(user_message, notify, stream)
                except Exception as exc:
                    notify("graph_end", {"workflow": "triage", "ms": 0, "steps": 0,
                                         "path": [], "error": repr(exc)})
                    result = None
            if result is None:
                result = self.run_loop_turn(spec, session, user_message, notify, stream,
                                            extra_context=extra_context)

            quick = captured.get("graph_route", {}).get("target") == "quick_reply"
            meta = {
                "gate": captured.get("gate"),
                "graph": ({"workflow": "triage",
                           "route": "quick" if quick else "full",
                           "reason": captured.get("triage_reason", ""),
                           "path": captured.get("graph_path")}
                          if "graph_route" in captured else None),
                "iterations": result.iterations,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "tools": [{"tool": c["tool"], "status": _status(c["output"])}
                          for c in result.tool_calls],
                # which brain answered this turn — so a reopened thread (or a
                # thread you switched models mid-way) shows it per card. A quick
                # graph turn was answered by the small model; say so honestly.
                "model": resolved.small_model if quick else resolved.model,
                "provider": self.settings.provider,
            }
            context_meta = captured.get("context")
            # Preserve the historical meta shape for ordinary chat turns.  A
            # context record is persisted only when something noteworthy
            # happened (Application injection or compaction); the live event is
            # still emitted on every turn.
            if context_meta and (
                context_meta["application_chars"] or context_meta["compaction"]
            ):
                meta["context"] = context_meta
            session.add_exchange(user_message, result.reply, tool_calls=result.tool_calls,
                                 source=source, meta=meta)
            if self.memory is not None:
                self.memory.maybe_consolidate(notify=notify)
                self.memory.export_markdown()   # keep MEMORY.md in sync

            end_event = {
                **identity,
                "iterations": result.iterations,
                "latency_ms": meta["latency_ms"],
                "tools": meta["tools"],
            }
            live_notify("turn_end", end_event)

        self.tracer.end_turn(result.reply, result.iterations, **identity,
                             latency_ms=meta["latency_ms"], tools=meta["tools"])
        return TurnResult(reply=result.reply, tool_calls=result.tool_calls,
                          iterations=result.iterations, meta=meta)

    def run_loop_turn(self, spec: AgentSpec, session: Session, user_message: str,
                      notify: Observer, stream: bool, extra_context: str = "") -> LoopResult:
        """The classic turn: assemble working memory, run THE loop. The graph's
        full_agent node calls this SAME method, so loop-as-a-node can never
        drift from loop-as-default."""
        resolved = resolve(spec, self.settings)
        system = session.build_system(resolved.system_prompt, resolved.model)
        # Everything per-turn rides WITH the user's message, never in the system
        # prompt: the clock, the gated retrieval, the app's context. The system
        # prompt therefore stays byte-identical turn over turn, so a provider's
        # prefix cache can hold onto it — prompt caching is a prefix match, and
        # a per-turn change anywhere in the prefix re-bills everything after it.
        #
        # Only the PROMPT gets the context block; `history` keeps the bare
        # message (see add_exchange), so context never accumulates across turns.
        context = session.build_turn_context(user_message, notify=notify, extra=extra_context)
        prompt = f"{context}\n\n{user_message}"

        # COMPRESSION HAPPENS HERE, on a copy, on the way to the model — never in
        # the stored conversation. session.history and chat_log hold the complete
        # record, because that is what the person reading the dashboard is owed;
        # these rewrites exist only so the request fits. The chain and its
        # trigger rule are the spec's context_policy (core/context/policy.py).
        fit = resolved.context_policy.fit(session.history, FitContext(
            system=system, prompt=prompt, full_history=session.history,
            home=self.settings.home, conn=self.conn, session_id=session.session_id,
            client=self.client, small_model=resolved.small_model, settings=self.settings,
            limit=self.context_for(self.settings.provider, resolved.model),
            trigger=self.settings.context_trigger,
        ))
        if fit.persisted is not None:
            # Working memory becomes the summary; chat_log keeps every
            # message, so the user's own record is untouched.
            session.history = fit.persisted
        messages = fit.sent + [{"role": "user", "content": prompt}]

        # Context construction used to be invisible even though it is one of
        # the most important decisions an agent runtime makes.  This event does
        # not expose private prompt text; it exposes the useful facts a person
        # needs to understand the run: what was attached and which compaction
        # stages changed the request.
        if extra_context or fit.ran:
            notify("context", {
                "agent_id": resolved.name,
                "session_id": session.session_id,
                "application_chars": len(extra_context),
                "history_messages": len(session.history),
                "sent_messages": len(fit.sent),
                "compaction": fit.ran or [],
            })

        # AgentSpec.tools is an allowlist.  Keeping the full registry on the
        # runtime is useful (tools are built once and can hold resources), but
        # each agent must only show and execute the tools declared in its spec.
        tools = self.tools if resolved.tools is None else self.tools.subset(resolved.tools)

        return run_loop(
            client=self.client,
            model=resolved.model,
            system=system,
            messages=messages,
            tools=tools,
            max_iterations=resolved.max_iterations,
            max_tokens=resolved.max_tokens,
            observer=notify,
            stream=stream,
        )

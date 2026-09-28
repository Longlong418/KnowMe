"""Wiring — builds one KnowMe from its parts. Gateways call `respond()`.

This file is the assembly diagram in code: config → db → tools → memory →
session → runtime. If you want to understand the repo in one place, start here.

KnowMe is now a FACADE over the Agent Core. The turn itself lives in
knowme/core/runtime.py (AgentRuntime.run_turn), parameterised by an AgentSpec;
KnowMe is the default agent — DEFAULT_SPEC on a runtime built from Settings —
and keeps the constructor, attributes and respond() signature every gateway,
ops command and eval already uses.
"""

from __future__ import annotations

from knowme.config import Settings, load_settings
from knowme.core.events import Observer
from knowme.core.loop import LoopResult
from knowme.core.models import get_client, roles_for
from knowme.core.runtime import AgentRuntime
from knowme.core.session import Session
from knowme.core.spec import DEFAULT_SPEC, AgentSpec
from knowme.db import connect
from knowme.ops.pricing import context_for
from knowme.ops.tracing import Tracer
from knowme.tools import build_registry


class KnowMe:
    def __init__(self, settings: Settings | None = None, client=None, conn=None,
                 spec: AgentSpec = DEFAULT_SPEC):
        # `client` and `conn` are injectable: evals swap in a scripted model,
        # the web gateway injects a cross-thread connection. Same seam either way.
        self.settings = settings or load_settings()
        self.settings.ensure_home()
        self.conn = conn or connect(self.settings.home)
        self.client = client or get_client(self.settings)
        # What the gate and the summariser run on — each may name a different
        # provider than the loop (core/models.py:roles_for). Resolved ONCE here,
        # for the whole agent, so two turns cannot disagree about it and so an
        # injected client (every eval) serves those roles too.
        self.roles = roles_for(self.settings, self.client)
        self.spec = spec
        # One name per agent — it tags facts, episodes and chat_log rows with the
        # agent that created them. The memory pool itself is shared across agents;
        # this name is not an isolation boundary, so agents can read each other's
        # rows. The default agent keeps the historical "default" tag, so existing
        # databases see no behavioral change.
        self.agent_id = spec.name

        # Memory first: the memory-management tools need it.
        from knowme.memory import Memory

        self.memory = Memory(self.conn, self.settings, self.client,
                             agent_id=self.agent_id, roles=self.roles)
        # The session is built BEFORE the tools, because the coding tools stamp
        # every change with the thread it happened in. They read the id at CALL
        # time (Session holds no tools, so nothing here depends on the order) —
        # a value captured at construction would be the thread you were in when
        # the process started, not the one you are in now.
        self.session = Session(self.settings, memory=self.memory, conn=self.conn,
                               agent_id=self.agent_id)
        self.tools = build_registry(self.conn, self.settings, self.memory,
                                    session=self.session)
        self.mcp_bridge = getattr(self.tools, "mcp_bridge", None)
        self.tracer = Tracer(self.settings)
        self.runtime = AgentRuntime(
            self.settings, client=self.client, conn=self.conn, memory=self.memory,
            tools=self.tools, tracer=self.tracer, context_for=context_for,
            roles=self.roles)

    def close(self) -> None:
        """Release external resources (MCP subprocesses). Called when the
        web gateway rebuilds the agent after a settings change."""
        if self.mcp_bridge is not None:
            self.mcp_bridge.close()

    def respond(self, user_message: str, observer: Observer | None = None,
                source: str = "cli", stream: bool = False,
                extra_context: str = "", images: list[dict] | None = None) -> LoopResult:
        """One full turn through the runtime, as the default agent.

        The graph front door is optional and can NEVER make KnowMe worse:
        flag off → the plain loop; flag on → the triage graph decides quick vs
        full, and any failure anywhere falls open to the plain loop."""
        # The current triage graph only receives a message.  Application turns
        # carry richer state, so keep them on the full loop until graph state
        # has an explicit context field.  Silently dropping an open document or
        # selection would be much worse than skipping the quick route.
        # An attached picture is the same argument: the graph would take the
        # message and lose the image, so those turns go straight to the loop.
        front_door = (
            self._respond_via_graph
            if self.settings.graph_workflows and not extra_context and not images
            else None
        )
        return self.runtime.run_turn(
            self.spec, self.session, user_message, observer=observer, source=source,
            stream=stream, extra_context=extra_context, front_door=front_door,
            images=images,
        ).as_loop_result()

    def _run_full_turn(self, user_message: str, notify, stream: bool) -> LoopResult:
        """The classic turn — the graph's full_agent node calls this so
        loop-as-a-node can never drift from loop-as-default."""
        return self.runtime.run_loop_turn(self.spec, self.session, user_message, notify, stream)

    def _respond_via_graph(self, user_message: str, notify, stream: bool) -> LoopResult | None:
        """One turn through the triage graph workflow. Returns None whenever
        the graph didn't produce an answer — respond() then falls open to the
        plain loop, so this path can only ever ADD speed, never lose a reply."""
        from knowme.graph import run_graph
        from knowme.graph.workflows.triage import (
            QUICK_REPLY_PROMPT,
            build_triage_graph,
            classify_message,
            todays_events,
        )

        def quick_reply(state: dict) -> str:
            prompt = QUICK_REPLY_PROMPT.format(calendar=state.get("calendar", ""),
                                               message=state["message"])
            response = self.client.messages.create(
                model=self.settings.small_model, max_tokens=600,
                messages=[{"role": "user", "content": prompt}])
            return "".join(b.text for b in response.content if b.type == "text")

        graph = build_triage_graph(
            classify_fn=lambda m: classify_message(self.client, self.settings.small_model, m),
            calendar_fn=lambda: todays_events(self.settings.home),
            quick_fn=quick_reply,
            # the full path is the SAME method the flag-off default runs; the
            # engine's tagged notifier stamps its inner events with node=
            full_fn=lambda state: self._run_full_turn(
                state["message"], state.get("_notify", notify), stream),
        )
        state = run_graph(graph, {"message": user_message}, observer=notify)
        if isinstance(state.get("result"), LoopResult):
            return state["result"]
        if state.get("reply"):
            return LoopResult(reply=state["reply"], tool_calls=[], iterations=1)
        return None  # graph produced nothing → caller falls open to the loop

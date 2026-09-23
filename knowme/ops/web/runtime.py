"""Web chat and workflow runtime."""

# This module intentionally keeps the shared transport imports available to the
# extracted functions.  They are a mechanical split from the old server file.
# ruff: noqa: F401, I001


from __future__ import annotations

import errno
import json
import os
import shutil
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from knowme.agents import get_profile, list_profiles
from knowme.applications import ApplicationContextBridge
from knowme.applications.reader import ReaderError, load_upload, load_url
from knowme.config import load_settings
from knowme.core.tools import as_text
from knowme.db import connect
from knowme.integrations import (
    apply_integration,
    apply_provider,
    apply_provider_disabled,
    list_connections,
    list_providers,
    test_integration,
)
from knowme.ops import browser_agent, commands
from knowme.ops.browser_agent import agent_lock, dash_session, get_agent, maybe_rotate_session
from knowme.ops.catalog import list_models
from knowme.ops.pricing import price_for, usage_summary
from knowme.ops.settings_api import apply_settings, pin_action, settings_info
from knowme.ops.tracing import TraceEncodingError, iter_trace_lines
from knowme.ops.web.uploads import image_hint, save_images

# Windows commonly reserves 7777 (for example through Hyper-V/WSL port
# exclusions), while 8888 is conventionally available for local webs.
# Keep the familiar 7777 default elsewhere, but make a clean Windows checkout
# The frontend lives in its own files (static/index.html + style.css + app.js),
# served as-is by this stdlib server — no build step, no framework. Edit those
# to change the UI; edit this file to change the server/API.
STATIC = Path(__file__).resolve().parents[1] / "static"

# Transient UI state belongs to the web process, not SQLite memory.  The
# bridge is intentionally explicit so the chat path cannot accidentally read
# state from another agent or another conversation.
application_contexts = ApplicationContextBridge()


def chat(message: str, agent_id: str = "default", images=None) -> dict:
    """One turn, one JSON result — the non-streaming door to the same room.

    The web itself uses /api/chat/stream; this exists for scripts and for
    `curl`. It deliberately does NOT reimplement the turn: it drives chat_stream
    and keeps the final "done" payload, because the two used to be separate
    copies of the same 25 lines and had already drifted (the streaming one
    reported which model answered, this one didn't). One implementation means
    they cannot disagree again.
    """
    final: dict = {}

    def collect_done(kind: str, ev: dict) -> None:
        if kind == "done":
            final.update(ev)

    chat_stream(message, collect_done, agent_id=agent_id, images=images)
    return final


def chat_stream(message: str, emit, agent_id: str = "default", images=None) -> None:
    """Run one turn, calling emit(kind, event) for every harness event AS it
    happens — gate decision, tool calls, and the reply text token by token —
    so the browser can show thinking streams like the CLI does. Ends
    with a 'done' event carrying the final structured result.

    `images` are attachments for THIS turn only (see ops/web/uploads.py). They
    are saved to disk here — before the agent is fetched, so a bad attachment
    fails without building one — and handed to respond() to ride along on the
    request's user message.

    A leading slash calls a graph workflow BY NAME instead of running a turn.
    Both doors end in the same 'done' event, so the chat renders the answer the
    same way whether the harness routed it or you named the shape yourself."""
    command = commands.parse(message)
    if command is not None:
        _run_command(command, emit)
        return

    settings = load_settings()
    settings.ensure_home()
    saved = save_images(settings.home, images)

    events: list[dict] = []

    def observer(kind, ev):
        if kind in ("context", "gate", "consolidation", "route", "triage"):
            events.append({"kind": kind, **ev})
        emit(kind, ev)

    with agent_lock:
        agent = get_agent(agent_id)
        thread_before = agent.session.session_id
        maybe_rotate_session(agent)
        if agent.session.session_id != thread_before:
            # The idle rotation gave this agent a new thread. The Application
            # snapshot (the document you have open) is screen state, not thread
            # state, so it comes along — otherwise an hour of reading would come
            # off the moment you asked about it, because the publish happened
            # back when the OLD thread was current.
            application_contexts.rekey(
                agent.agent_id, thread_before, agent.session.session_id
            )
        extra_context = application_contexts.render(
            agent.agent_id, agent.session.session_id
        )
        start = datetime.now(UTC)
        try:
            result = agent.respond(
                message,
                observer=observer,
                source="web",
                stream=True,
                extra_context=extra_context,
                images=saved,
            )
        except Exception as exc:
            # The one place that knows this turn carried pictures, and therefore
            # the one place that can tell the user the likely reason a provider
            # refused it. Errors from image-free turns pass through untouched.
            if saved:
                raise RuntimeError(image_hint(agent.settings.model, exc)) from exc
            raise
        latency_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)

    context = next((e for e in events if e["kind"] == "context"), None)
    gate = next((e for e in events if e["kind"] == "gate"), None)
    cons = next((e for e in events if e["kind"] == "consolidation"), None)
    route = next((e for e in events if e["kind"] == "route"), None)
    triage = next((e for e in events if e["kind"] == "triage"), None)
    quick = bool(route) and route.get("target") == "quick_reply"
    emit("done", {
        "reply": result.reply,
        "context": ({
            "application_chars": context.get("application_chars", 0),
            "history_messages": context.get("history_messages", 0),
            "sent_messages": context.get("sent_messages", 0),
            "compaction": context.get("compaction", []),
        } if context else None),
        "gate": {"decision": gate["decision"], "reason": gate.get("reason")} if gate else None,
        "graph": ({"workflow": route.get("workflow", "triage"),
                   "route": "quick" if quick else "full",
                   "reason": (triage or {}).get("reason", "")} if route else None),
        "tools": [{"tool": c["tool"], "args": c["args"], "output": as_text(c["output"]),
                   "status": _tool_status(c["output"]),
                   "summary": _tool_summary(c["output"])} for c in result.tool_calls],
        "consolidation": {"new_facts": cons["new_facts"]} if cons else None,
        "iterations": result.iterations,
        "latency_ms": latency_ms,
        # the ordered timeline (meta.steps) so the live card ends up with exactly
        # what a reloaded historical card renders — one shape, two paths.
        "steps": result.meta.get("steps") or [],
        # which brain answered — shown per card; a quick graph turn was the small model
        "model": agent.settings.small_model if quick else agent.settings.model,
    })


# A NAME -> runner table, never a dynamic import of whatever the browser sent.
# "run the workflow the client named" is one careless refactor away from "import
# and call whatever string arrives", so the indirection is a dict on purpose.
def WORKFLOW_RUNNERS() -> dict[str, str]:  # noqa: N802 — reads as a table
    """Discovered, not hand-listed. A hardcoded table and a slash-command list
    are two registries of the same fact, and they drift."""
    return commands.discover()


def _bounded(text: str, limit: int = 40_000) -> str:
    """Cap the digest, and SAY SO when the cap bites.

    It used to be a silent [:4000], which turned a long report into a document
    that stopped mid-sentence while looking finished — the frame is what the
    graph card renders, so there was nothing else to compare against. Still
    bounded (one SSE frame should not be able to carry a megabyte), just far
    past any real report and honest at the edge.
    """
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[报告过长，这里只显示前 {limit} 字]"


def graph_stream(payload: dict, emit) -> None:
    """Run a graph workflow, streaming its node events as SSE.

    Only the engine's own events go out — graph_start / node_start / node_end /
    route / graph_end. They already carry `workflow` and `node`, which is all a
    card needs, and they carry no node OUTPUT, so a digest can never leak into
    a frame. Unlike the Arena this needs no lock of its own: run_graph already
    serialises notify() behind one (engine.py), so events arrive whole.
    """
    name = (payload.get("workflow") or "").strip()
    target = WORKFLOW_RUNNERS().get(name)
    if target is None:
        emit("done", {"error": f"unknown workflow '{name}'"})
        return
    module_name, _, fn_name = target.partition(":")
    try:
        import importlib
        import inspect

        run = getattr(importlib.import_module(module_name), fn_name)
        # A workflow that takes input declares a `message` parameter and gets the
        # payload's topic; one that does not is called exactly as before. Same
        # convention as commands.run (commands.py:104), so `/gather` and the
        # dashboard button stay one story.
        if "message" in inspect.signature(run).parameters:
            state = run(observer=lambda kind, ev: emit(kind, ev),
                        message=payload.get("message") or "")
        else:
            state = run(observer=lambda kind, ev: emit(kind, ev))
        emit("done", {
            "workflow": name,
            # Not truncated to fit a card: a research report is a document, and
            # a report that stops mid-sentence with no marker reads as complete.
            # A bound still exists, but it is now far past any real report and
            # it says so when it bites.
            "digest": _bounded(state.get("digest") or ""),
            "note_id": state.get("note_id", ""),
            "draft_path": state.get("draft_path", ""),
            "errors": state.get("errors") or {},
        })
    except Exception as exc:
        # Includes GraphStateCollision, which run_graph raises OUT (unlike node
        # errors) — better shown in the card than dropped on the floor.
        emit("done", {"error": f"{type(exc).__name__}: {exc}"})


def _run_command(command: tuple[str, str], emit) -> None:
    """Handle `/name` from the chat box.

    The node events go out exactly as the engine emits them, so the topology
    chart animates from the same trace poll that animates a normal turn — a
    named workflow lights the picture as readily as a routed one.
    """
    name, arg = command
    start = datetime.now(UTC)
    if name in ("graphs", "help", "?"):
        emit("done", {"reply": commands.describe(), "tools": [], "iterations": 0,
                      "latency_ms": 0, "gate": None})
        return
    try:
        state = commands.run(name, emit, arg)
    except Exception as exc:
        emit("done", {"reply": f"`/{name}` failed: {type(exc).__name__}: {exc}",
                      "tools": [], "iterations": 0, "latency_ms": 0, "gate": None})
        return
    if state is None:
        emit("done", {"reply": commands.unknown_reply(name), "tools": [],
                      "iterations": 0, "latency_ms": 0, "gate": None})
        return
    reply = state.get("digest") or "(the workflow produced no text)"
    if state.get("ignored_argument"):
        reply = (f"*`/{name}` takes no input, so \u201c{state['ignored_argument']}\u201d "
                 f"was not used — a fixed shape always fetches the same sources. "
                 f"Ask a normal question to use the loop instead.*\n\n") + reply
    if state.get("draft_path"):
        reply += f"\n\n*saved to `{state['draft_path']}`*"
    for node, err in (state.get("errors") or {}).items():
        reply += f"\n\n*{node}: {err}*"
    emit("done", {
        "reply": reply, "tools": [], "gate": None, "consolidation": None,
        "iterations": 0,
        "latency_ms": int((datetime.now(UTC) - start).total_seconds() * 1000),
        "workflow": name,
    })


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _tool_status(output) -> str:
    """Classify a tool result for the UI: ok / warn / error — from the output
    string alone (tools already report honestly, so trust their words).

    as_text() first, because trace files written before that helper existed
    still contain raw lists from the notes tools (see tools.as_text)."""
    low = as_text(output).lower()
    if "failed" in low or "timed out" in low or low.startswith("error"):
        return "error"
    if "already exists" in low or "not synced" in low or "skipped" in low:
        return "warn"
    return "ok"


def _tool_summary(output) -> str:
    """The one-line gist under a tool chip: first sentence, capped. Shared by
    the live turn and the trace-derived turns so the two can't drift."""
    return as_text(output).split(". ")[0][:120]


# Notion-backed episodes live across the network, so the client AND the result
# are cached with a short TTL — collect() runs on every web auto-refresh
# and must not round-trip to Notion every few seconds (rate limits + latency).
# The sqlite path is a local query and doesn't need this.
# The two caches themselves live in data.py, next to the only code that reads
# them (see the note there); a copy here would be a second, silently-dead one.
_NOTION_EPISODES_TTL = 30.0   # seconds; the page polls ~every 5s
_notion_lock = threading.Lock()

"""Web data collection and mutation handlers."""

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
from .runtime import (
    _NOTION_EPISODES_TTL,
    _notion_lock,
    _parse_ts,
    _tool_status,
    application_contexts,
)

def invalidate_notion_cache() -> None:
    """Forget cached Notion clients/results after connection settings change."""
    global _notion_store, _notion_episodes
    with _notion_lock:
        _notion_store = None
        _notion_episodes = None


def _get_notion_store():
    """The ONE NotionEpisodeStore for the whole web process. Its
    constructor round-trips to Notion (data-source resolution), so it's built
    lazily and cached. Callers must hold _notion_lock."""
    global _notion_store
    if _notion_store is None:
        from knowme.memory.episodic.notion_store import NotionEpisodeStore

        _notion_store = NotionEpisodeStore()
    return _notion_store


def collect(agent_id: str = "default") -> dict:
    """Everything the page shows for one Agent, in one JSON blob."""
    get_profile(agent_id)
    settings = load_settings()
    info = settings_info()
    settings.ensure_home()
    home = settings.home
    conn = connect(home)

    def rows(sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def episodes_payload() -> dict:
        """Episodes from the active backend: sqlite (default) or notion.
        A Notion outage must not take down the whole web payload."""
        if settings.episodic_store != "notion":
            return {
                "source": "sqlite",
                "error": "",
                "items": rows(
                    "SELECT id, happened_at, summary, agent_id "
                    "FROM episodes WHERE agent_id=? ORDER BY happened_at DESC",
                    (agent_id,),
                ),
            }
        try:
            global _notion_episodes
            with _notion_lock:
                store = _get_notion_store()
                if _notion_episodes and time.time() - _notion_episodes[0] < _NOTION_EPISODES_TTL:
                    items = _notion_episodes[1]
                    return {"source": "notion", "error": "", "items": [
                        item for item in items
                        if item.get("agent_id", "default") == agent_id
                    ]}
                items = store.list()
                _notion_episodes = (time.time(), items)
                return {"source": "notion", "error": "", "items": [
                    item for item in items
                    if item.get("agent_id", "default") == agent_id
                ]}
        except Exception as exc:
            # Degrade gracefully: never take the payload down, and serve the
            # last good fetch if we have one (an outage shouldn't blank the tab).
            stale = [item for item in (_notion_episodes[1] if _notion_episodes else [])
                     if item.get("agent_id", "default") == agent_id]
            return {"source": "notion", "error": str(exc), "items": stale}

    episodes_data = episodes_payload()

    # --- traces → turns (group events between turn_start and turn_end)
    events = []
    trace_errors = []
    trace_files = sorted((home / "traces").glob("*.jsonl"))
    for path in trace_files:
        try:
            lines = list(iter_trace_lines(path))
        except TraceEncodingError as exc:
            trace_errors.append({"file": path.name, "error": str(exc)})
            continue
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    events = [event for event in events if event.get("agent_id", agent_id) == agent_id]
    turns, current, wake_scans = [], None, []
    for ev in events:
        kind = ev.get("type")
        if kind == "turn_start":
            current = {"user_message": ev.get("user_message"), "ts": ev.get("ts"),
                       "agent_id": ev.get("agent_id", "default"),
                       "session_id": ev.get("session_id"),
                       "gate": None, "llm_calls": [], "tools": [], "reply": None}
        elif kind == "wake_scan":
            wake_scans.append(ev)
        elif current is not None:
            if kind == "gate":
                current["gate"] = ev
            elif kind == "context":
                current["context"] = ev
            elif kind == "route":
                current["graph"] = {"workflow": ev.get("workflow"),
                                    "route": "quick" if ev.get("target") == "quick_reply" else "full",
                                    "reason": (current.get("graph") or {}).get("reason", "")}
            elif kind == "triage":
                current.setdefault("graph", {})["reason"] = ev.get("reason", "")
            elif kind == "llm":
                current["llm_calls"].append(ev)
            elif kind == "tool":
                current["tools"].append(ev)
            elif kind == "consolidation":
                current["consolidation"] = ev
            elif kind == "turn_end":
                current["reply"] = ev.get("reply")
                current["iterations"] = ev.get("iterations")
                turns.append(current)
                current = None
    if current is not None:  # a turn that never ended = the smoking gun for hangs
        current["reply"] = "TURN NEVER FINISHED — check for a hang after this point"
        current["unfinished"] = True
        turns.append(current)

    # --- derive per-turn latency + dollar cost (the ops numbers humans feel)
    if settings.base_url or settings.provider == "openrouter":
        list_models()  # warm the per-model price cache (5-min cached fetch)
    price_in, price_out = price_for(settings.provider, settings.model or "")
    for t in turns:
        start, end = _parse_ts(t["ts"]), None
        last = t["llm_calls"][-1]["ts"] if t["llm_calls"] else None
        end = _parse_ts(last)
        t["latency_ms"] = int((end - start).total_seconds() * 1000) if start and end else None
        tin = sum(c.get("usage", {}).get("in", 0) for c in t["llm_calls"])
        tout = sum(c.get("usage", {}).get("out", 0) for c in t["llm_calls"])
        t["cost"] = tin / 1e6 * price_in + tout / 1e6 * price_out
        for x in t["tools"]:
            x["status"] = _tool_status(x.get("output", ""))
            x["summary"] = (x.get("output", "") or "").split(". ")[0][:120]

    latencies = sorted(t["latency_ms"] for t in turns if t["latency_ms"] is not None)
    total_cost = sum(t["cost"] for t in turns)

    def pct(p: float) -> int:
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))] if latencies else 0

    from knowme.memory import bundled_skill_dirs
    from knowme.memory.procedural.loader import SkillLoader

    skills = [{"name": s.name, "description": s.description, "body": s.body,
               "path": str(s.path),
               # relative path (for reveal) + whether it lives in the editable home dir
               "rel": _rel_to_home(s.path, home),
               "editable": str((home / "skills").resolve()) in str(s.path.resolve())}
              for s in SkillLoader([*bundled_skill_dirs(), home / "skills"]).skills]

    eval_report = None
    report_path = home / "eval_report.json"
    if report_path.exists():
        eval_report = json.loads(report_path.read_text(encoding="utf-8"))

    eval_history = []
    hist_path = home / "eval_runs.jsonl"
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines()[-20:]:
            try:
                eval_history.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    eval_history.reverse()

    outbox = [{"name": p.name, "text": p.read_text(encoding="utf-8")[:400]}
              for p in sorted((home / "outbox").glob("*.txt"), reverse=True)[:20]]

    # --- state.db introspection: the actual SQLite tables, so the persistence
    # layer is visible (not just its contents). Table names are hard-coded, so
    # the f-string SQL is safe.
    def table_info(name):
        info = conn.execute(f"PRAGMA table_info({name})").fetchall()
        cols = [r["name"] for r in info]
        types = {r["name"]: r["type"] for r in info}
        scoped = "agent_id" in cols
        where = " WHERE agent_id=?" if scoped else ""
        args = (agent_id,) if scoped else ()
        count = conn.execute(f"SELECT COUNT(*) FROM {name}{where}", args).fetchone()[0]
        # up to 200 newest rows so each table has its own scrollable tab
        sample = [dict(r) for r in conn.execute(
            f"SELECT * FROM {name}{where} ORDER BY rowid DESC LIMIT 200", args
        ).fetchall()]
        return {"name": name, "columns": cols, "types": types, "count": count, "sample": sample}

    db_path = home / "state.db"
    all_tables = [r["name"] for r in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    db_info = {
        "path": str(db_path.resolve()),
        "size": db_path.stat().st_size if db_path.exists() else 0,
        "tables": [table_info(n) for n in ("calendar_events", "facts", "episodes", "chat_log")],
        "fts": [t for t in all_tables if t.endswith("_fts")],
        "all_tables": all_tables,
    }

    # Report every profile without eagerly building an LLM client.  A page load
    # should stay cheap; selecting/chatting with an agent creates it lazily.
    profiles = list_profiles()
    live_agents = browser_agent.current_agents()
    current_sessions = {
        profile.id: (
            live_agents[profile.id].session.session_id
            if profile.id in live_agents else dash_session(profile.id)
        )
        for profile in profiles
    }
    sessions_by_agent = {
        profile.id: session_list(conn, profile.id) if profile.id == agent_id else []
        for profile in profiles
    }

    # --- graph workflows: topology straight from the engine (never hand-drawn,
    # so the picture can't drift) + quick/full split from the trace events
    from knowme.graph.workflows.gather import gather_topology
    from knowme.graph.workflows.triage import triage_topology
    graph_routes = [e.get("target") for e in events if e.get("type") == "route"]
    # The last few completed runs, newest first. Overview needs this because the
    # two workflows are two different JOBS with different triggers — triage runs
    # itself on every message, gather runs when you ask — so "which chart is
    # relevant right now" is a question only the trace can answer. Rendering a
    # fixed workflow there showed triage forever, seconds after a gather ran.
    graph_runs = [{"workflow": e.get("workflow"), "ms": e.get("ms"),
                   "at": e.get("ts"), "steps": e.get("steps"),
                   "path": e.get("path") or [], "error": e.get("error")}
                  for e in events if e.get("type") == "graph_end"][-8:][::-1]

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "home": str(home.resolve()),
        "provider": settings.provider,
        "model": info["model"],
        "stats": {
            "turns": len(turns),
            "tool_calls": sum(len(t["tools"]) for t in turns),
            "tool_errors": sum(1 for t in turns for x in t["tools"] if x["status"] == "error"),
            "gate_skips": sum(1 for t in turns if t["gate"] and t["gate"].get("decision") == "skip"),
            "gate_retrieves": sum(1 for t in turns if t["gate"] and t["gate"].get("decision") == "retrieve"),
            "tokens_in": sum(c.get("usage", {}).get("in", 0) for t in turns for c in t["llm_calls"]),
            "tokens_out": sum(c.get("usage", {}).get("out", 0) for t in turns for c in t["llm_calls"]),
            "cost": round(total_cost, 4),
            "latency_avg": int(sum(latencies) / len(latencies)) if latencies else 0,
            "latency_p95": pct(0.95),
            "trace_files": len(trace_files),
        },
        "turns": turns[::-1][:50],
        "wake_scans": wake_scans[::-1][:25],
        # last raw trace lines, so Ops shows traces inline (no folder needed)
        "trace_tail": [{"type": e.get("type"), "ts": e.get("ts"),
                        "detail": (e.get("user_message") or e.get("decision") or e.get("tool")
                                   or e.get("reply") or "")}
                       for e in events[-18:]][::-1],
        "trace_file": (trace_files[-1].name if trace_files else None),
        "trace_errors": trace_errors,
        "agents": [
            {**profile.to_public(),
             "status": "ready" if profile.id in live_agents else "idle",
             "session_id": current_sessions[profile.id]}
            for profile in profiles
        ],
        "facts": rows(
            "SELECT id, subject, content, source, agent_id, created_at "
            "FROM facts WHERE agent_id=? ORDER BY id DESC", (agent_id,)
        ),
        "episodes": episodes_data["items"],
        "episodes_source": episodes_data["source"],
        "episodes_error": episodes_data["error"],
        "soul": (home / "SOUL.md").read_text(encoding="utf-8") if (home / "SOUL.md").exists() else "",
        "chat_pending": conn.execute("SELECT COUNT(*) FROM chat_log WHERE consolidated=0").fetchone()[0],
        "chat_log": rows(
            "SELECT role, content, consolidated, source, session_id, agent_id, created_at "
            "FROM chat_log WHERE agent_id=? ORDER BY id DESC LIMIT 80", (agent_id,)
        )[::-1],
        # Keep the two legacy fields for old open tabs.  New clients use the
        # per-agent maps and never mix one agent's history into another's dock.
        "sessions": sessions_by_agent["default"],
        "current_session": current_sessions["default"],
        "sessions_by_agent": sessions_by_agent,
        "current_sessions": current_sessions,
        "consolidate_every": settings.consolidate_every,
        "calendar": rows('SELECT title, start, "end", attendees, created_at FROM calendar_events ORDER BY start'),
        "outbox": outbox,
        "skills": skills,
        "eval_report": eval_report,
        "eval_history": eval_history,
        "graph": {
            # NOTE: `enabled` gates TRIAGE ONLY — the per-message front door.
            # `knowme gather` is a routine you run yourself and ignores this flag
            # entirely, so the UI must not say "off = no graphs run".
            "enabled": settings.graph_workflows,
            "workflows": [triage_topology(), gather_topology()],
            "runs": graph_runs,
            "stats": {"quick": sum(1 for t in graph_routes if t == "quick_reply"),
                      "full": sum(1 for t in graph_routes if t == "full_agent")},
        },
        "db": db_info,
        "workspace": workspace_info(),
        "settings": info,
        "providers": [asdict(view) for view in list_providers()],
        "knowledge_info": knowledge_info(),
        "connections": [asdict(view) for view in list_connections()],
        "tools": tools_info(),
        "usage": usage_summary(home),
    }


def _rel_to_home(path, home) -> str:
    """Path relative to KNOWME_HOME if it lives there, else the repo-relative
    'skills/...' path — either way something reveal_path can open."""
    try:
        return str(path.resolve().relative_to(home.resolve()))
    except ValueError:
        return str(path)


def session_list(conn, agent_id: str = "default") -> list[dict]:
    """One row per conversation for the chat-history picker: id, its first user
    message (the title), message count, newest first. Sessions are just a
    session_id label on chat_log rows — the same table, no new storage."""
    groups = conn.execute(
        """SELECT session_id, COUNT(*) AS messages, MAX(created_at) AS last_at
           FROM chat_log WHERE agent_id=?
           GROUP BY session_id ORDER BY last_at DESC""",
        (agent_id,),
    ).fetchall()
    out = []
    for g in groups:
        sid = g["session_id"]
        first = conn.execute(
            "SELECT content FROM chat_log WHERE session_id=? AND agent_id=? "
            "AND role='user' ORDER BY id LIMIT 1",
            (sid, agent_id),
        ).fetchone()
        last = conn.execute(
            "SELECT role, content FROM chat_log WHERE session_id=? AND agent_id=? "
            "ORDER BY id DESC LIMIT 1", (sid, agent_id)
        ).fetchone()
        sources = [r["source"] for r in conn.execute(
            "SELECT DISTINCT source FROM chat_log WHERE session_id=? AND agent_id=?",
            (sid, agent_id),
        ).fetchall()]
        preview = ""
        if last:
            preview = ("you: " if last["role"] == "user" else "knowme: ") + last["content"][:80]
        out.append({"id": sid,
                    "title": (first["content"][:60] if first else "(empty)"),
                    "last": preview,
                    "sources": sources,
                    "messages": g["messages"],
                    "last_at": g["last_at"]})
    return out


# A tool's origin, for grouping in the Tools tab (name → category).
_FLAGSHIP = {"create_event", "list_events", "save_note", "send_message"}
_SELFMGMT = {"manage_memory", "update_soul", "create_skill"}
_APPLE = {"read_apple_calendar", "read_apple_mail", "create_reminder", "create_note"}
_WEB = {"search_web"}


def _tool_source(name: str, mcp_servers: list[str]) -> str:
    if name in _FLAGSHIP:
        return "flagship"
    if name in _WEB:
        return "web"
    if name in _SELFMGMT:
        return "self-management"
    if name in _APPLE:
        return "apple"
    if any(name.startswith(f"{s}_") for s in mcp_servers):
        return "mcp"
    return "other"


def knowledge_info() -> dict:
    """Every one of the user's knowledge notes, for the web.

    Not filtered by Agent: this is the human's own knowledge base and they own
    every note in it, whatever Agent wrote it. Each row still carries its
    agent_id so the view can label where the note came from. (the Agents' own
    tools stay scoped — see knowme/tools/knowledge.py:_agent_conds.)
    """
    from knowme.tools.knowledge import list_folders, list_notes
    settings = load_settings()
    conn = connect(settings.home)
    notes = list_notes(conn, agent_id=None)
    folders = list_folders(conn, agent_id=None)
    return {
        "notes_count": len(notes),
        "folders": folders,
        "notes": notes,
        "all_folders": folders,
    }


def workspace_info(relative: str | None = None) -> dict:
    """Return the safe, read-only Coding Workspace file tree."""
    from knowme.applications.coding_workspace import workspace_info as build_info

    return build_info(relative)


def workspace_action(payload: dict) -> dict:
    """Handle a read-only Coding Workspace request."""
    from knowme.applications.coding_workspace import workspace_action as run_action

    agent_id = payload.get("agent_id") or "default"
    get_profile(agent_id)
    return run_action(payload)


def tools_info() -> dict:
    """The agent's available tools + any configured MCP servers — so the Tools
    tab shows CAPABILITIES, not just the artifacts tool calls produced. Reflects
    the live agent's registry when one exists (exact), else builds a display-only
    catalog (no MCP subprocess is spawned just to render the page)."""
    settings = load_settings()
    settings.ensure_home()
    mcp = {"configured": False, "servers": [], "live": False}
    mcp_path = settings.home / "mcp.json"
    if mcp_path.exists():
        mcp["configured"] = True
        try:
            mcp["servers"] = [s.get("name", "?") for s in json.loads(mcp_path.read_text(encoding="utf-8")).get("servers", [])]
        except (json.JSONDecodeError, OSError):
            pass

    catalog = []
    live = browser_agent.current()
    if live is not None:
        mcp["live"] = getattr(live, "mcp_bridge", None) is not None
        tools = list(live.tools._tools.values())
    else:
        # Display-only: same tools minus MCP (building the real registry would
        # start MCP servers, which we don't want on a 5-second poll).
        from knowme.memory import Memory
        from knowme.tools import calendar, knowledge, memory_admin, messages, notes, search

        conn = connect(settings.home)
        try:
            # Notion mode: reuse the web's one cached client instead of
            # letting Memory() build a fresh one per poll (issue #20).
            episode_store = None
            if settings.episodic_store == "notion":
                with _notion_lock:
                    episode_store = _get_notion_store()
            mem = Memory(conn, settings, None, episode_store=episode_store)
        except Exception:
            # A misconfigured optional backend (notion/supabase) must not take
            # the web down — drop the memory-admin tools from the
            # display-only catalog instead.
            mem = None
        tools = [calendar.make_tool(
                     conn,
                     settings.home,
                     apple_calendar=settings.apple_calendar,
                     google_calendar=settings.google_calendar,
                     google_calendar_id=settings.google_calendar_id,
                 ),
                 calendar.make_list_tool(conn),
                 notes.make_tool(conn), messages.make_tool(settings.home),
                 search.make_tool(),
                 memory_admin.make_update_soul_tool(settings)]
        if mem is not None:
            tools += [memory_admin.make_manage_memory_tool(mem),
                      memory_admin.make_create_skill_tool(settings, mem)]
        tools += list(knowledge.make_knowledge_tools(
            conn, agent_id=getattr(mem, "agent_id", "default")
        ).values())
        if settings.apple_tools:
            from knowme.tools import apple

            tools += apple.make_tools()
        if settings.experimental:
            # Mirror build_registry: without this the catalog LIES after you
            # flip the experimental toggle — delegate_task is missing until the
            # first chat turn builds the real agent, so it looks like the
            # switch did nothing.
            from knowme.tools import experimental as experimental_tools

            tools += experimental_tools.make_tools(settings)
    for t in tools:
        catalog.append({"name": t.name, "description": t.description,
                        "source": _tool_source(t.name, mcp["servers"])})
    catalog.sort(key=lambda c: (c["source"], c["name"]))
    from knowme.tools.experimental import PLANNED

    return {"catalog": catalog, "mcp": mcp, "apple_on": settings.apple_tools,
            "planned": PLANNED}   # whiteboard boxes not wired in yet (coming soon)


def run_query(payload: dict) -> dict:
    sql = (payload.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return {"error": "Type a SELECT query."}
    low = sql.lower()
    if not (low.startswith(("select", "with"))):
        return {"error": "Only SELECT (or WITH … SELECT) queries are allowed."}
    if ";" in sql:
        return {"error": "One statement at a time (no semicolons)."}
    import sqlite3

    settings = load_settings()
    settings.ensure_home()
    db = (settings.home / "state.db").resolve()
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        cur = c.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        data = [[str(r[i]) if r[i] is not None else "" for i in range(len(cols))]
                for r in cur.fetchmany(200)]
        c.close()
        return {"columns": cols, "rows": data}
    except sqlite3.Error as exc:
        return {"error": str(exc)}


def _thread_history(conn, sid: str, agent_id: str = "default") -> list[dict]:
    """The ONE way to load a thread for the chat dock: role + content + the
    per-turn meta (gate/stats/tools/model) so every card renders in full.
    id '__all__' returns the whole cross-thread timeline (like the Loop tab,
    but as chat). Every history-loading path goes through here so they can't
    drift apart (they used to: 'switch' dropped meta and showed only text)."""
    if sid == "__all__":
        rows = conn.execute(
            "SELECT role, content, meta FROM chat_log WHERE agent_id=? "
            "ORDER BY id DESC LIMIT 200",
            (agent_id,),
        ).fetchall()[::-1]
    else:
        rows = conn.execute(
            "SELECT role, content, meta FROM chat_log "
            "WHERE session_id=? AND agent_id=? ORDER BY id",
            (sid, agent_id),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"],
             "meta": json.loads(r["meta"]) if r["meta"] else None} for r in rows]


def session_action(payload: dict) -> dict:
    """Chat history control: start a new conversation, switch to a past one, or
    read a conversation's history (read-only, for the live inbox). Sessions live
    in chat_log."""
    action = payload.get("action")
    agent_id = payload.get("agent_id") or "default"
    get_profile(agent_id)
    if action == "history":
        # read-only view of a conversation — never touches the agent, so the
        # web can poll it live (e.g. to show new Telegram messages arrive).
        settings = load_settings()
        settings.ensure_home()
        conn = connect(settings.home)
        sid = payload.get("id") or "default"
        return {"ok": True, "agent_id": agent_id, "session_id": sid,
                "history": _thread_history(conn, sid, agent_id)}
    with agent_lock:
        agent = get_agent(agent_id)
        if action == "new":
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            sid = f"s-{agent_id}-{stamp}"
            agent.session.start_new(sid)
            return {"ok": True, "agent_id": agent_id, "session_id": sid, "history": []}
        if action == "switch":
            sid = payload.get("id") or "default"
            agent.session.switch(sid)
            # Same meta-rich rows as the read-only "history" action, so a
            # switched thread renders its full turn cards (gate/stats/tools/
            # model) — not just the text. (These two paths used to disagree.)
            return {"ok": True, "agent_id": agent_id, "session_id": sid,
                    "history": _thread_history(agent.conn, sid, agent_id)}
    return {"error": f"unknown action {action}"}


def _editor_cmd() -> list[str] | None:
    """The user's code editor CLI: $KNOWME_EDITOR, then cursor, then code."""

    custom = os.getenv("KNOWME_EDITOR")
    if custom and shutil.which(custom):
        return [custom]
    for cli in ("cursor", "code"):
        if shutil.which(cli):
            return [cli]
    return None


def reveal_path(rel: str) -> dict:
    """Open a file/folder under KNOWME_HOME — in the user's code editor if one
    is on PATH (cursor/code/$KNOWME_EDITOR), otherwise reveal in Finder.
    Restricted to paths inside KNOWME_HOME."""
    import subprocess
    import sys

    settings = load_settings()
    settings.ensure_home()
    home = settings.home.resolve()
    target = (home / (rel or ".")).resolve()
    if target != home and home not in target.parents:
        return {"error": "path is outside the .knowme home"}
    if not target.exists():
        return {"error": f"not found: {target}"}

    editor = _editor_cmd()
    if editor and target.is_file() and target.suffix != ".db":  # editors choke on sqlite
        subprocess.run([*editor, str(target)], check=False)
        return {"ok": True, "opened_in": editor[0], "path": str(target)}
    if sys.platform != "darwin":
        return {"error": f"no editor found and reveal is macOS-only — the path is {target}"}
    subprocess.run(
        ["open", "-R", str(target)] if target.is_file() else ["open", str(target)],
        check=False,
    )
    return {"ok": True, "revealed": str(target)}


def memory_action(payload: dict) -> dict:
    """Human CRUD on memory from the web: update/delete facts & episodes,
    rewrite SOUL.md. Writes the same sqlite file the agent uses (busy_timeout
    covers contention); changes are live for the next agent turn."""
    from knowme.memory.episodic.store import SqliteEpisodeStore
    from knowme.memory.semantic.store import SqliteFactStore

    settings = load_settings()
    settings.ensure_home()
    action = payload.get("action")
    agent_id = payload.get("agent_id") or "default"
    get_profile(agent_id)
    if action == "save_soul":
        text = (payload.get("content") or "").strip()
        if not text:
            return {"error": "SOUL cannot be empty"}
        (settings.home / "SOUL.md").write_text(text + "\n")
        return {"ok": True}
    if action == "save_skill":
        # Edit any loaded SKILL.md by hand (same file the agent's create_skill
        # writes) — repo skills and home skills alike. Sandboxed to the two
        # skills folders; validates the frontmatter before writing.
        from pathlib import Path

        from knowme.memory import bundled_skill_dirs
        from knowme.memory.procedural.loader import _parse_text

        text = (payload.get("content") or "").strip()
        dest = Path(payload.get("path") or "").resolve()
        allowed = [d.resolve() for d in bundled_skill_dirs()] + [(settings.home / "skills").resolve()]
        if dest.name != "SKILL.md" or not any(a in dest.parents for a in allowed):
            return {"error": "can only edit SKILL.md files inside the skills folders"}
        if _parse_text(text, dest) is None:
            return {"error": "invalid SKILL.md — needs a name and description in the frontmatter"}
        dest.write_text(text.rstrip() + "\n", encoding="utf-8")
        return {"ok": True}

    conn = connect(settings.home)
    facts = SqliteFactStore(conn, agent_id=agent_id)
    episodes = SqliteEpisodeStore(conn, agent_id=agent_id)
    if action == "delete_episode" and settings.episodic_store == "notion":
        global _notion_episodes
        with _notion_lock:
            ok = _get_notion_store().delete(str(payload.get("id", "")))
            # bust the TTL cache so the next collect() refetches — otherwise a
            # deleted episode would linger on the page for up to 30s
            _notion_episodes = None
        return {"ok": ok}
    try:
        rid = int(payload.get("id", 0))
    except (TypeError, ValueError):
        return {"error": "bad id"}
    if action == "update_fact":
        return {"ok": facts.update(rid, payload.get("content", ""), payload.get("subject") or None)}
    if action == "merge_fact":
        try:
            target_id = int(payload.get("target_id", 0))
        except (TypeError, ValueError):
            return {"error": "bad target id"}
        return {"ok": facts.merge(rid, target_id)}
    if action == "delete_fact":
        return {"ok": facts.delete(rid)}
    if action == "delete_episode":
        return {"ok": episodes.delete(rid)}
    return {"error": f"unknown action {action}"}


def extras_action(payload: dict) -> dict:
    """Publish or clear the Application snapshot used by the next agent turn.

    Preferred payload::

        {"application": "reader", "resource": "paper.md",
         "content": "...", "selection": "...", "session_id": "..."}

    The earlier ``type=selection/text/source`` shape remains accepted so an old
    open web keeps working during an upgrade.
    """
    agent_id = (payload.get("agent_id") or "default").strip()
    get_profile(agent_id)
    session_id = (payload.get("session_id") or dash_session(agent_id)).strip()
    if payload.get("action") == "clear":
        cleared = application_contexts.clear(agent_id, session_id)
        return {"ok": True, "cleared": cleared}

    legacy_selection = payload.get("type") == "selection"
    if payload.get("type") and not legacy_selection:
        return {"error": f"unknown context type: {payload.get('type')}"}

    previous = application_contexts.get(agent_id, session_id)
    application = payload.get("application") or (
        previous.application if previous else "reader"
    )
    resource = payload.get("resource", payload.get("source"))
    if resource is None:
        resource = previous.resource if previous else ""
    content = payload.get("content")
    if content is None:
        content = previous.content if previous else ""
    selection = payload.get("selection", payload.get("text"))
    if selection is None:
        selection = previous.selection if previous else ""

    state = application_contexts.publish(
        agent_id=agent_id,
        session_id=session_id,
        application=application,
        resource=resource,
        content=content,
        selection=selection,
        metadata=payload.get("metadata") or (previous.metadata if previous else {}),
    )
    return {
        "ok": True,
        "agent_id": agent_id,
        "session_id": session_id,
        "application": state.application,
        "resource": state.resource,
        "content_chars": len(state.content),
        "selection_chars": len(state.selection),
        "truncated": (
            len(payload.get("content") or "") > application_contexts.max_content_chars
            or len(payload.get("selection", payload.get("text")) or "")
            > application_contexts.max_selection_chars
        ),
    }


def library_action(payload: dict) -> dict:
    """The document library: add, list, open, search, delete.

    ``open`` returns the extracted TEXT (what the reader shows for markdown and
    text) and the ``file_url`` the browser should fetch for the ORIGINAL when
    it can render it better than we can — a PDF goes to pdf.js, which needs the
    real bytes, not our text.

    Documents are workspace-level (see applications/library.py), so there is no
    agent filter here. ``added_by`` is stamped from the payload for provenance.
    """
    import base64
    import binascii

    from knowme.applications.library import (
        delete_document,
        get_document,
        list_documents,
        parse_and_save,
        search_documents,
        text_window,
    )
    from knowme.applications.reader import fetch_url
    from knowme.db import connect

    settings = load_settings()
    settings.ensure_home()
    conn = connect(settings.home)
    agent_id = payload.get("agent_id") or "default"
    get_profile(agent_id)
    action = payload.get("action", "")

    if action == "list":
        # home here so a row stored with the wrong kind repairs itself (see
        # library._repair_binary_kinds) -- this is the listing the reader calls
        # before it decides how to render anything.
        return {"ok": True, "documents": list_documents(conn, limit=200, home=settings.home)}
    if action == "search":
        return {"ok": True, "results": search_documents(conn, str(payload.get("query", "")))}
    if action in {"open", "text"}:
        row = get_document(conn, str(payload.get("doc_id", "")))
        if row is None:
            return {"ok": False, "error": "文档不存在"}
        window = text_window(row["content"], payload.get("offset", 0),
                             payload.get("limit", 400000))
        return {"ok": True, "document": {
            "id": row["id"], "title": row["title"], "kind": row["kind"],
            "suffix": row["suffix"], "source": row["source"],
            "chars": row["chars"], "bytes": row["bytes"],
            "created_at": row["created_at"],
        }, "text": window["text"], "has_more": window["has_more"],
            "file_url": f"/api/library/file?id={row['id']}"}
    if action == "delete":
        gone = delete_document(conn, settings.home, str(payload.get("doc_id", "")))
        return {"ok": gone} if gone else {"ok": False, "error": "文档不存在"}
    if action in {"upload", "url"}:
        try:
            if action == "url":
                fetched = fetch_url(str(payload.get("url", "")).strip())
                name = fetched["name"]
                # Keep the source URL so the library shows where it came from;
                # give the download a suffix when the URL path had none, so the
                # stored original is served with the right content type.
                content_type = fetched["content_type"]
                if not os.path.splitext(name)[1]:
                    name += {"application/pdf": ".pdf", "text/html": ".html",
                             "text/markdown": ".md"}.get(
                                 content_type.split(";")[0].strip(), ".txt")
                raw, source = fetched["raw"], fetched["source"]
            else:
                name = str(payload.get("name", ""))
                data_url = str(payload.get("data", ""))
                if not name or not data_url.startswith("data:") or "," not in data_url:
                    raise ReaderError("上传数据格式无效")
                header, encoded = data_url.split(",", 1)
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ReaderError("上传数据不是有效的 base64 文件") from exc
                content_type, source = header, name
            doc = parse_and_save(conn, settings.home, name=name, raw=raw,
                                 content_type=content_type, source=source,
                                 added_by=agent_id)
        except ReaderError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "document": doc}
    return {"error": f"unknown action {action}"}


def library_file(conn, home, doc_id: str):
    """The ORIGINAL bytes of a document, for the browser to render itself."""
    from knowme.applications.library import file_path, get_document

    target = file_path(conn, home, doc_id)
    if target is None:
        return None, None, None
    row = get_document(conn, doc_id)
    return target.read_bytes(), row["kind"], row["suffix"]


def knowledge_action(payload: dict) -> dict:
    """Store, retrieve, and delete knowledge notes with [[links]] support.

    The web is the human's own view, so reading, editing and deleting all
    span every Agent — you can open and save any note you can see, whichever
    Agent wrote it, and the note keeps its original agent_id. Only `create`
    still uses the selected Agent, because a NEW note has to be stamped with
    somebody. (The Agents' tools remain scoped: this is the browser, not a tool
    call.)
    """
    from knowme.db import connect
    from knowme.tools.knowledge import (
        create_note,
        delete_note,
        get_linked_notes,
        get_note,
        list_folders,
        list_notes,
        search_notes,
        update_note,
    )

    settings = load_settings()
    conn = connect(settings.home)
    agent_id = payload.get("agent_id") or "default"
    get_profile(agent_id)

    action = payload.get("action", "")

    if action == "list":
        notes = list_notes(conn, payload.get("folder"), agent_id=None)
        folders = list_folders(conn, agent_id=None)
        return {"ok": True, "notes": notes, "folders": folders}
    elif action == "get":
        note = get_note(conn, payload.get("note_id", ""), agent_id=None)
        return {"ok": True, "note": note} if note else {"ok": False, "error": "Note not found"}
    elif action == "create":
        try:
            note = create_note(
                conn,
                title=payload.get("title", ""),
                folder=payload.get("folder", "default"),
                content=payload.get("content", ""),
                agent_id=agent_id,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note}
    elif action == "update":
        try:
            note = update_note(
                conn,
                payload.get("note_id", ""),
                payload.get("content", ""),
                title=payload.get("title"),
                folder=payload.get("folder"),
                agent_id=None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": note} if note else {"ok": False, "error": "Note not found"}
    elif action == "delete":
        deleted = delete_note(conn, payload.get("note_id", ""), agent_id=None)
        return {"ok": deleted, "error": "Note not found"} if not deleted else {"ok": True}
    elif action == "search":
        notes = search_notes(conn, payload.get("query", ""), agent_id=None)
        return {"ok": True, "notes": notes}
    elif action == "links":
        notes = get_linked_notes(conn, payload.get("note_id", ""), agent_id=None)
        return {"ok": True, "notes": notes}
    else:
        return {"error": f"unknown knowledge action: {action}"}




def events_since(cursor):
    """New trace events past `cursor` (a line count in today's trace file).
    Both the browser and CLI append to this same file,
    so the live diagram lights up for all of them. cursor=None returns just
    the current tail so the browser starts fresh instead of replaying history."""
    settings = load_settings()
    settings.ensure_home()
    path = settings.home / "traces" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    if not path.exists():
        return {"events": [], "cursor": 0}
    try:
        lines = list(iter_trace_lines(path))
    except TraceEncodingError as exc:
        return {"events": [], "cursor": 0, "error": str(exc)}
    if cursor is None or cursor < 0 or cursor > len(lines):
        return {"events": [], "cursor": len(lines)}
    out = []
    for ln in lines[cursor:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return {"events": out, "cursor": len(lines)}

"""DETERMINISTIC EVAL — /api/data is JSON, whoever asks and whatever is in it.

Three live bugs share one shape: the payload the browser polls every 5 seconds
was allowed to be something the browser cannot use, and the failure was silent.

1. A tool that returns a LIST (search_notes / list_notes / list_folders) was
   recorded in the trace as a list, and the dashboard's turn summary called
   .lower() on it. collect() raised in the middle of building the payload, the
   route answered with a closed connection instead of JSON, and the page could
   only say "TypeError: Failed to fetch" — while the poll quietly kept failing.
   Loopback records are text now (tools.as_text), and the reader side tolerates
   the old rows that are already on disk.

2. `sessions_by_agent` is filled for the REQUESTED agent and left empty for
   everyone else, but the page indexes it with its own idea of the active agent
   (a mutable global). So the payload has to say whose data it is, and the
   frontend has to refuse an answer about someone else — otherwise the history
   menu of the agent you just switched to reads the old payload's empty list and
   announces "还没有历史对话".

3. With KNOWME_EPISODIC_STORE=notion the two Notion caches were only defined in
   runtime.py and copied into data.py by the package wrapper's _sync_state() —
   which the HTTP route never calls (server.py imports the data module
   directly). On a real request the names did not exist and the payload was a
   NameError instead of episodes. The caches now live where they are used.

Everything here goes through web.data.collect: that is the function the route
calls, so a fix that only works through the package-level wrapper (as the Notion
one did) cannot pass these.
"""

from __future__ import annotations

import json
import time

import pytest

from knowme.config import Settings
from knowme.ops.web import data as web_data


def _stub(monkeypatch, settings, module=web_data):
    """The dependency seam, applied to the module under test.

    The package-level wrappers (web.collect etc.) do this with _sync_state();
    the HTTP route does not, which is exactly what bug 3 was.
    """
    monkeypatch.setattr(module, "load_settings", lambda: settings)
    monkeypatch.setattr(module, "settings_info", lambda: {"model": "test-model"})
    monkeypatch.setattr(module, "list_connections", list)
    monkeypatch.setattr(module, "list_providers", list)
    monkeypatch.setattr(module, "usage_summary", lambda _: {"total_cost": 0})
    monkeypatch.setattr(module, "tools_info", lambda: {"catalog": []})
    monkeypatch.setattr(module.browser_agent, "current_agents", dict)


def _trace(home, *events):
    """Write one trace file, the way the loop's Tracer does."""
    traces = home / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    path = traces / "2026-09-22.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


TURN = [
    {"type": "turn_start", "ts": "2026-09-22T12:00:00+00:00", "agent_id": "default",
     "session_id": "s-default-1", "user_message": "我有哪些笔记？"},
    # The OLD on-disk shape: a list-returning tool, recorded verbatim. This is
    # the row that used to take the whole payload down.
    {"type": "tool", "ts": "2026-09-22T12:00:01+00:00", "agent_id": "default",
     "tool": "list_notes", "args": {}, "output": ["笔记 A", "笔记 B"]},
    {"type": "llm", "ts": "2026-09-22T12:00:02+00:00", "agent_id": "default",
     "usage": {"in": 10, "out": 5}},
    {"type": "turn_end", "ts": "2026-09-22T12:00:03+00:00", "agent_id": "default",
     "reply": "你有两条笔记。", "iterations": 1},
]


def test_a_list_tool_result_cannot_take_the_payload_down(tmp_path, monkeypatch):
    """The bug the user saw as "TypeError: Failed to fetch" on a chat card."""
    settings = Settings(home=tmp_path)
    settings.ensure_home()
    _trace(tmp_path, *TURN)
    _stub(monkeypatch, settings)

    payload = web_data.collect("default")          # ← must not raise

    turn = payload["turns"][0]
    tool = turn["tools"][0]
    assert tool["tool"] == "list_notes"
    assert tool["output"] == '["笔记 A", "笔记 B"]', "the record is text, not a list"
    assert tool["status"] == "ok"                   # classified, not crashed
    assert isinstance(tool["summary"], str) and tool["summary"]
    assert turn["reply"] == "你有两条笔记。"


def test_the_record_is_text_but_the_tool_still_returns_whatever_it_returns():
    """as_text() is about the RECORD of a call, not the call."""
    from knowme.core.tools import as_text

    assert as_text("already text") == "already text"
    assert as_text(None) == ""
    assert as_text(["a", "b"]) == '["a", "b"]'
    assert as_text({"n": 1}) == '{"n": 1}'
    assert as_text(3) == "3"
    # The web reader classifies whatever it finds, old trace rows included.
    from knowme.ops.web.runtime import _tool_status, _tool_summary

    assert _tool_status(["a"]) == "ok"
    assert _tool_status("Error: nope") == "error"
    assert _tool_status("file already exists") == "warn"
    assert _tool_summary(["a", "b"]) == '["a", "b"]'


def test_the_payload_names_its_agent_and_scopes_sessions_to_it(tmp_path, monkeypatch):
    """The page can only refuse a stale answer if the answer says who it is for."""
    settings = Settings(home=tmp_path)
    settings.ensure_home()
    conn = __import__("knowme.db", fromlist=["connect"]).connect(tmp_path)
    conn.execute("INSERT INTO chat_log (role, content, agent_id, session_id) "
                 "VALUES ('user', 'coding hello', 'coding', 's-coding-1')")
    conn.commit()
    conn.close()
    _stub(monkeypatch, settings)

    coding = web_data.collect("coding")
    assert coding["agent_id"] == "coding"
    assert [s["id"] for s in coding["sessions_by_agent"]["coding"]] == ["s-coding-1"]
    # Every OTHER agent gets an empty list — which is why a late answer for the
    # agent you just left must never be rendered (see static/js/main.js).
    assert coding["sessions_by_agent"]["default"] == []
    assert web_data.collect("default")["sessions_by_agent"]["coding"] == []


def test_notion_episodes_degrade_instead_of_naming_an_undefined_global(
        tmp_path, monkeypatch):
    """KNOWME_EPISODIC_STORE=notion on the HTTP code path."""
    settings = Settings(home=tmp_path, episodic_store="notion")
    settings.ensure_home()
    _stub(monkeypatch, settings)

    def unreachable():
        raise RuntimeError("notion unreachable")

    monkeypatch.setattr(web_data, "_get_notion_store", unreachable)

    payload = web_data.collect("default")

    assert payload["episodes_source"] == "notion"
    assert "notion unreachable" in payload["episodes_error"]
    assert payload["episodes"] == []

    # An outage serves the last good fetch rather than blanking the tab, so the
    # cache really does live in this module.
    monkeypatch.setattr(web_data, "_notion_episodes",
                        (time.time(), [{"id": "e1", "summary": "上次拉到的一条",
                                        "happened_at": "2026-09-21",
                                        "agent_id": "default"}]))
    stale = web_data.collect("default")
    assert [item["summary"] for item in stale["episodes"]] == ["上次拉到的一条"]


def test_a_new_conversation_keeps_the_document_you_have_open(tmp_path, monkeypatch):
    """「新建对话」 must not orphan the Application snapshot.

    The bridge is keyed by (agent, session) and the reader publishes when you
    OPEN a document, so a brand-new thread had no snapshot at all: the agent
    answered "我看不到你打开的是哪篇" about the document still on screen. The
    idle rotation already carried the snapshot across (and is tested in
    test_session_rotation.py); this is the same rule for the button.
    """
    from evals.helpers import ScriptedClient, make_knowme
    from knowme.ops import web

    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    old = app.session.session_id
    web.application_contexts.publish(
        agent_id=app.agent_id, session_id=old, application="reader",
        resource="REACT", content="the paper you were reading", selection="")
    # Patch the module the ROUTE reaches (session_action is not wrapped by the
    # package seam, so patching `web.get_agent` would not be seen).
    monkeypatch.setattr(web_data, "get_agent", lambda agent_id="default": app)

    out = web_data.session_action({"action": "new", "agent_id": app.agent_id})

    assert out["ok"] and out["session_id"] != old, "a new thread really started"
    assert "REACT" in web.application_contexts.render(app.agent_id, out["session_id"]), \
        "…and the document you have open came with you"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

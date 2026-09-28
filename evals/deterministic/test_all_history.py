"""DETERMINISTIC EVAL — loading ONE conversation's history.

A conversation is its own thread: reading one must never pick up another's rows.
(There used to be a read-only id="__all__" action that stitched every thread into
a single "全部消息" timeline for the chat dock. It is gone, and with it the only
way to read across threads — every history path is scoped to one session id.)"""

from __future__ import annotations

import json

from evals.helpers import ScriptedClient, make_knowme
from knowme.ops.web import _thread_history, session_action


def _seed(app, session_id, user, assistant):
    for role, content in (("user", user), ("assistant", assistant)):
        app.conn.execute(
            "INSERT INTO chat_log (role, content, session_id, source) VALUES (?, ?, ?, 'dashboard')",
            (role, content, session_id),
        )
    app.conn.commit()


def test_single_thread_history_is_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWME_HOME", str(tmp_path / "home"))
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    _seed(app, "dashboard-a", "hi from A", "reply A")
    _seed(app, "dashboard-b", "hi from B", "reply B")

    out = session_action({"action": "history", "id": "dashboard-b"})
    assert [m["content"] for m in out["history"]] == ["hi from B", "reply B"]


def test_thread_history_includes_meta(tmp_path, monkeypatch):
    """Regression: switching threads showed only text because that path dropped
    meta. Both the switch and history paths now go through _thread_history, which
    must carry the per-turn meta (gate/stats/tools/model) so cards render full."""
    monkeypatch.setenv("KNOWME_HOME", str(tmp_path / "home"))
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    meta = {"gate": {"decision": "skip"}, "iterations": 1, "latency_ms": 2400,
            "tools": [], "model": "gemini-3.5-flash"}
    app.conn.execute("INSERT INTO chat_log (role, content, session_id, source) VALUES ('user','hi','t','dashboard')")
    app.conn.execute("INSERT INTO chat_log (role, content, session_id, source, meta) "
                     "VALUES ('assistant','hey','t','dashboard',?)", (json.dumps(meta),))
    app.conn.commit()

    hist = _thread_history(app.conn, "t")
    assert hist[0]["meta"] is None                     # user row
    assert hist[1]["meta"]["model"] == "gemini-3.5-flash"
    assert hist[1]["meta"]["gate"]["decision"] == "skip"

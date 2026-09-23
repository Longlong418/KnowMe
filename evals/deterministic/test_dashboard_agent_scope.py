"""What the dashboard hands one Agent.

Memory is shared, so the payload carries every Agent's facts/episodes/notes and
labels each row with who recorded it. Conversations are not shared, so chat_log
(and the sessions list) is still trimmed to the Agent you are looking at — the
second half of that split is what this file pins now."""

from knowme.config import Settings
from knowme.db import connect


def test_collect_shares_memory_and_scopes_chat(tmp_path, monkeypatch):
    from knowme.ops import web

    settings = Settings(home=tmp_path)
    settings.ensure_home()
    conn = connect(tmp_path)
    conn.execute("INSERT INTO facts (subject, content, agent_id) VALUES (?, ?, ?)",
                 ("default", "default fact", "default"))
    conn.execute("INSERT INTO facts (subject, content, agent_id) VALUES (?, ?, ?)",
                 ("coding", "coding fact", "coding"))
    conn.execute("INSERT INTO episodes (happened_at, summary, agent_id) VALUES (?, ?, ?)",
                 ("2026-09-21", "default episode", "default"))
    conn.execute("INSERT INTO episodes (happened_at, summary, agent_id) VALUES (?, ?, ?)",
                 ("2026-09-21", "coding episode", "coding"))
    conn.execute("INSERT INTO chat_log (role, content, agent_id) VALUES (?, ?, ?)",
                 ("user", "default chat", "default"))
    conn.execute("INSERT INTO chat_log (role, content, agent_id) VALUES (?, ?, ?)",
                 ("user", "coding chat", "coding"))
    conn.commit()
    conn.close()

    monkeypatch.setattr(web, "load_settings", lambda: settings)
    monkeypatch.setattr(web, "settings_info", lambda: {"model": "test"})
    monkeypatch.setattr(web, "list_connections", list)
    monkeypatch.setattr(web, "list_providers", list)
    monkeypatch.setattr(web, "tools_info", lambda: {"catalog": []})
    monkeypatch.setattr(web, "usage_summary", lambda _: {"total_cost": 0})
    monkeypatch.setattr(web.browser_agent, "current_agents", dict)

    payload = web.collect("coding")
    # Memory: both Agents' rows, newest first, each carrying its stamp.
    assert sorted(row["content"] for row in payload["facts"]) == [
        "coding fact", "default fact"]
    assert sorted(row["agent_id"] for row in payload["facts"]) == ["coding", "default"]
    assert sorted(row["summary"] for row in payload["episodes"]) == [
        "coding episode", "default episode"]
    # Conversation: only this Agent's.
    assert [row["content"] for row in payload["chat_log"]] == ["coding chat"]
    # The database tab has to agree with the memory page about the same table.
    facts_table = next(table for table in payload["db"]["tables"] if table["name"] == "facts")
    assert facts_table["count"] == 2
    chat_table = next(table for table in payload["db"]["tables"] if table["name"] == "chat_log")
    assert chat_table["count"] == 1
    assert payload["sessions"] == []


def test_collect_rejects_unknown_agent(tmp_path, monkeypatch):
    from knowme.ops import web

    settings = Settings(home=tmp_path)
    monkeypatch.setattr(web, "load_settings", lambda: settings)
    try:
        web.collect("unknown")
    except ValueError as exc:
        assert "unknown agent" in str(exc)
    else:
        raise AssertionError("unknown agents must not receive dashboard data")

"""Dashboard payloads must not expose another Agent's private state."""

from knowme.config import Settings
from knowme.db import connect


def test_collect_scopes_memory_chat_and_database_samples(tmp_path, monkeypatch):
    from knowme.ops import web

    settings = Settings(home=tmp_path)
    settings.ensure_home()
    conn = connect(tmp_path)
    conn.execute("INSERT INTO facts (subject, content, agent_id) VALUES (?, ?, ?)",
                 ("default", "private default fact", "default"))
    conn.execute("INSERT INTO facts (subject, content, agent_id) VALUES (?, ?, ?)",
                 ("coding", "private coding fact", "coding"))
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
    assert [row["content"] for row in payload["facts"]] == ["private coding fact"]
    assert [row["summary"] for row in payload["episodes"]] == ["coding episode"]
    assert [row["content"] for row in payload["chat_log"]] == ["coding chat"]
    facts_table = next(table for table in payload["db"]["tables"] if table["name"] == "facts")
    assert [row["content"] for row in facts_table["sample"]] == ["private coding fact"]
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

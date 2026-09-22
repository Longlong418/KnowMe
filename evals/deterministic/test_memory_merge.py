"""Memory Manager merge behavior and Agent boundaries."""

from knowme.db import connect
from knowme.memory.semantic.store import SqliteFactStore


def test_fact_merge_keeps_target_and_removes_source(tmp_path):
    conn = connect(tmp_path)
    facts = SqliteFactStore(conn, agent_id="coding")
    facts.add("project", "uses Python")
    facts.add("project", "ships with tests")
    rows = facts.list()
    source, target = rows[0]["id"], rows[1]["id"]

    assert facts.merge(source, target) is True
    merged = facts.list()
    assert len(merged) == 1
    assert "uses Python" in merged[0]["content"] or "ships with tests" in merged[0]["content"]
    assert facts.merge(source, target) is False


def test_fact_merge_cannot_cross_agent_boundaries(tmp_path):
    conn = connect(tmp_path)
    coding = SqliteFactStore(conn, agent_id="coding")
    learning = SqliteFactStore(conn, agent_id="learning")
    coding.add("project", "coding fact")
    learning.add("project", "learning fact")
    coding_id = coding.list()[0]["id"]
    learning_id = learning.list()[0]["id"]

    assert coding.merge(learning_id, coding_id) is False
    assert len(coding.list()) == 1
    assert len(learning.list()) == 1


def test_dashboard_merge_action_uses_selected_agent(tmp_path, monkeypatch):
    from knowme.config import Settings
    from knowme.ops import web

    settings = Settings(home=tmp_path)
    settings.ensure_home()
    conn = connect(tmp_path)
    conn.execute("INSERT INTO facts (subject, content, agent_id) VALUES (?, ?, ?)",
                 ("project", "keep", "coding"))
    target_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO facts (subject, content, agent_id) VALUES (?, ?, ?)",
                 ("project", "absorb", "coding"))
    source_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    monkeypatch.setattr(web, "load_settings", lambda: settings)

    assert web.memory_action({
        "action": "merge_fact", "id": source_id,
        "target_id": target_id, "agent_id": "coding",
    })["ok"] is True
    assert web.memory_action({
        "action": "merge_fact", "id": source_id,
        "target_id": target_id, "agent_id": "learning",
    })["ok"] is False

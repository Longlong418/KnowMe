"""Memory Manager merge behavior.

There is no "Agent boundary" for memory any more — the facts are one shared
pool, so a merge across two Agents' rows is an ordinary merge. Conversations
are the thing that stays per-Agent; test_multi_agent_workspace.py pins that."""

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


def test_a_fact_recorded_by_one_agent_can_be_merged_by_another(tmp_path):
    """Memory is one pool, so "the Learning agent tidies up a fact the Coding
    agent wrote" is a normal thing to do, not a boundary violation."""
    conn = connect(tmp_path)
    coding = SqliteFactStore(conn, agent_id="coding")
    learning = SqliteFactStore(conn, agent_id="learning")
    coding.add("project", "coding fact")
    learning.add("project", "learning fact")
    # list() is unscoped now, so the two rows come back from either store; pick
    # them by what they say rather than by which store asked.
    by_content = {row["content"]: row["id"] for row in coding.list()}

    assert coding.merge(by_content["learning fact"], by_content["coding fact"]) is True
    merged = coding.list()
    assert len(merged) == 1
    assert "coding fact" in merged[0]["content"] and "learning fact" in merged[0]["content"]
    # The stamp on the surviving row is the agent that WROTE it, not the one
    # that merged — merging is an edit, and an edit never reassigns a fact.
    stamp = conn.execute("SELECT agent_id FROM facts").fetchone()["agent_id"]
    assert stamp == "coding"


def test_dashboard_merge_action_does_not_care_which_agent_is_selected(tmp_path, monkeypatch):
    """The dashboard still requires an agent_id (it names the workspace you are
    looking at), but memory actions act on the one shared pool, so the merge
    lands whoever is selected — and lands exactly once."""
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
        "target_id": target_id, "agent_id": "learning",
    })["ok"] is True
    # Merging the same pair twice is still a no-op — the source row is gone.
    assert web.memory_action({
        "action": "merge_fact", "id": source_id,
        "target_id": target_id, "agent_id": "coding",
    })["ok"] is False

"""Knowledge Base invariants: usable CRUD without cross-Agent leakage."""

import pytest

from knowme.db import connect
from knowme.tools.knowledge import (
    create_note,
    delete_note,
    get_linked_notes,
    get_note,
    linkify_content,
    list_notes,
    parse_links,
    search_notes,
    update_note,
)


def test_notes_are_scoped_and_ids_are_unique(tmp_path):
    conn = connect(tmp_path)
    general = create_note(conn, "Plan", content="general", agent_id="default")
    coding = create_note(conn, "Plan", content="coding", agent_id="coding")

    assert general["id"] != coding["id"]
    assert get_note(conn, general["id"], "coding") is None
    assert [n["content"] for n in list_notes(conn, agent_id="default")] == ["general"]
    assert [n["content"] for n in search_notes(conn, "coding", "coding")] == ["coding"]


def test_update_title_links_and_delete_report_real_results(tmp_path):
    conn = connect(tmp_path)
    target = create_note(conn, "Target", agent_id="learning")
    source = create_note(
        conn,
        "Source",
        content="See [[Target]] and [[Target]]",
        agent_id="learning",
    )

    updated = update_note(
        conn, source["id"], "new body [[Target]]", title="Renamed", agent_id="learning"
    )
    assert updated["title"] == "Renamed"
    assert get_linked_notes(conn, target["id"], "learning")[0]["title"] == "Renamed"
    assert delete_note(conn, "does-not-exist", "learning") is False
    assert delete_note(conn, source["id"], "coding") is False
    assert delete_note(conn, source["id"], "learning") is True
    with pytest.raises(ValueError):
        update_note(conn, target["id"], "body", title="", agent_id="learning")


def test_wiki_links_are_deduplicated_and_escaped_for_html(tmp_path):
    assert parse_links("[[One]] [[One]] [[ Two ]]") == ["One", "Two"]
    html = linkify_content("[[<script>alert(1)</script>]]")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_dashboard_api_keeps_crud_in_the_selected_agent_scope(tmp_path, monkeypatch):
    from knowme.config import Settings
    from knowme.ops import dashboard

    settings = Settings(home=tmp_path)
    settings.ensure_home()
    monkeypatch.setattr(dashboard, "load_settings", lambda: settings)

    created = dashboard.knowledge_action({
        "action": "create", "title": "Private", "content": "coding only",
        "agent_id": "coding",
    })
    note_id = created["note"]["id"]
    assert dashboard.knowledge_action({"action": "get", "note_id": note_id,
                                       "agent_id": "default"})["ok"] is False
    assert dashboard.knowledge_action({"action": "get", "note_id": note_id,
                                       "agent_id": "coding"})["ok"] is True
    assert dashboard.knowledge_action({"action": "delete", "note_id": note_id,
                                       "agent_id": "default"})["ok"] is False

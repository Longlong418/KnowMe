"""Knowledge Base invariants: usable CRUD, and the right things stay scoped.

Two audiences read these notes. The human's dashboard sees all of them; an
Agent's own tools see only its own. Both halves are asserted here.
"""

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


def test_dashboard_shows_every_agent_but_the_agents_tools_stay_scoped(tmp_path, monkeypatch):
    """The dashboard is the human's view; a tool call is an Agent's.

    This used to assert the opposite — that the dashboard filtered notes by the
    selected Agent — and that filter is why the knowledge base looked empty
    whenever the selected Agent had not written any notes yet. The human owns
    every note in their own knowledge base, so the browser shows them all.

    The isolation that MATTERS is unchanged and is asserted at the bottom: an
    Agent's own tools still cannot see another Agent's notes. Losing that half
    while fixing the first is the failure this test exists to catch.
    """
    from knowme.config import Settings
    from knowme.ops import web

    settings = Settings(home=tmp_path)
    settings.ensure_home()
    monkeypatch.setattr(web, "load_settings", lambda: settings)

    coding = web.knowledge_action({
        "action": "create", "title": "Coding note", "content": "coding only",
        "agent_id": "coding",
    })["note"]
    learning = web.knowledge_action({
        "action": "create", "title": "Learning note", "content": "learning only",
        "agent_id": "learning",
    })["note"]

    # The list spans Agents, and still says who wrote what — that label is what
    # the view shows on each card.
    listed = web.knowledge_action({"action": "list", "agent_id": "default"})
    assert {n["title"]: n["agent_id"] for n in listed["notes"]} == {
        "Coding note": "coding", "Learning note": "learning"}

    # A note you can see, you can open, save and delete — from any Agent. The
    # note does not change owner when you do.
    assert web.knowledge_action({"action": "get", "note_id": coding["id"],
                                       "agent_id": "default"})["ok"] is True
    saved = web.knowledge_action({
        "action": "update", "note_id": coding["id"], "content": "edited",
        "title": "Coding note", "agent_id": "default"})
    assert saved["ok"] is True and saved["note"]["agent_id"] == "coding"

    # The Agents' tools are the boundary, and it still holds.
    conn = connect(tmp_path)
    assert [n["content"] for n in list_notes(conn, agent_id="coding")] == ["edited"]
    assert [n["content"] for n in list_notes(conn, agent_id="learning")] == ["learning only"]
    assert get_note(conn, learning["id"], "coding") is None

    assert web.knowledge_action({"action": "delete", "note_id": coding["id"],
                                       "agent_id": "default"})["ok"] is True

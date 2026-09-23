"""Knowledge Base invariants: usable CRUD, and who can see whose notes.

There is one knowledge base. The human's dashboard shows all of it, and so do
the Agents' own tools — a note captured in one conversation is there in the
next, whichever Agent you are talking to. Each note still carries the agent_id
of whoever created it, as a label rather than a fence. Passing an agent_id
explicitly still narrows a query (the semantic backends partition that way);
that narrower path is asserted too, so it does not rot.
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
    make_knowledge_tools,
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


def test_dashboard_and_the_agents_tools_both_see_every_note(tmp_path, monkeypatch):
    """One knowledge base, two ways in.

    This test used to assert that the dashboard showed every Agent's notes while
    the Agents' tools stayed scoped. That split is what the user reported as a
    bug ("我现在前端看不到记忆了"): a note saved while talking to one Agent was
    invisible to the next one.
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
    research = web.knowledge_action({
        "action": "create", "title": "Research note", "content": "research only",
        "agent_id": "research",
    })["note"]

    # The list spans Agents, and still says who wrote what — that label is what
    # the view shows on each card.
    listed = web.knowledge_action({"action": "list", "agent_id": "default"})
    assert {n["title"]: n["agent_id"] for n in listed["notes"]} == {
        "Coding note": "coding", "Research note": "research"}

    # A note you can see, you can open, save and delete — from any Agent. The
    # note does not change owner when you do.
    assert web.knowledge_action({"action": "get", "note_id": coding["id"],
                                       "agent_id": "default"})["ok"] is True
    saved = web.knowledge_action({
        "action": "update", "note_id": coding["id"], "content": "edited",
        "title": "Coding note", "agent_id": "default"})
    assert saved["ok"] is True and saved["note"]["agent_id"] == "coding"

    # The Agents' own tools reach the same notes. This is the half that changed:
    # the Coding agent's tool registry, not just the browser, now finds the note
    # the Research agent wrote.
    conn = connect(tmp_path)
    coding_tools = make_knowledge_tools(conn, "coding")
    found = coding_tools["search_notes"].fn("research only")
    assert [n["title"] for n in found] == ["Research note"]
    assert coding_tools["get_note"].fn(research["id"])["title"] == "Research note"
    # Asking for one Agent explicitly still narrows — that path is kept for the
    # backends that partition remotely, so it must not rot.
    assert [n["content"] for n in list_notes(conn, agent_id="coding")] == ["edited"]
    assert get_note(conn, research["id"], "coding") is None
    # create_note still stamps its own Agent, so the label stays truthful.
    assert coding_tools["create_note"].fn("From coding")["agent_id"] == "coding"

    assert web.knowledge_action({"action": "delete", "note_id": coding["id"],
                                       "agent_id": "default"})["ok"] is True

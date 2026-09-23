"""MVP guarantees, and where the line between Agents sits.

Agents share MEMORY — facts, episodes and notes are one pool, so any Agent
recalls all of it and any Agent may correct it (the rows keep an agent_id, but
only as a "who wrote this down" label). They do NOT share CONVERSATIONS: a
session belongs to the Agent you had it with. Both halves are pinned here."""

from knowme.agents import get_profile, list_profiles
from knowme.db import connect
from knowme.memory.semantic.store import SqliteFactStore
from knowme.ops.browser_agent import resume_or_new_session
from knowme.ops.web import _thread_history, session_list


def test_builtin_profiles_are_small_explicit_agent_specs():
    profiles = list_profiles()
    assert [profile.id for profile in profiles] == [
        "default", "coding", "learning", "research", "reader"
    ]
    assert all(profile.spec.name == profile.id for profile in profiles)
    assert get_profile("default").spec.tools is None
    assert "get_document" in get_profile("learning").spec.tools
    # the reader agent lives in the reading pane and works from the document
    # library; it deliberately has no search_web (Research is next door for that)
    reader_tools = get_profile("reader").spec.tools
    assert {"open_document", "search_documents", "list_documents"} <= reader_tools
    assert "search_web" not in reader_tools


def test_sessions_and_history_are_isolated_by_agent(tmp_path):
    conn = connect(tmp_path)
    conn.executemany(
        "INSERT INTO chat_log(role, content, session_id, source, agent_id) "
        "VALUES (?,?,?,?,?)",
        [
            ("user", "general question", "same-id", "dashboard", "default"),
            ("assistant", "general answer", "same-id", "dashboard", "default"),
            ("user", "coding question", "same-id", "dashboard", "coding"),
            ("assistant", "coding answer", "same-id", "dashboard", "coding"),
        ],
    )
    conn.commit()

    assert session_list(conn, "default")[0]["title"] == "general question"
    assert session_list(conn, "coding")[0]["title"] == "coding question"
    assert [row["content"] for row in _thread_history(conn, "same-id", "coding")] == [
        "coding question", "coding answer"
    ]
    assert resume_or_new_session(conn, "coding") == "same-id"


def test_any_agent_can_correct_a_fact_another_agent_recorded(tmp_path):
    """The inverse of what this test used to pin, and the reason the user asked
    for it: the memory is theirs, not the Coding agent's, so telling the
    Research agent "that's wrong" has to actually fix it."""
    conn = connect(tmp_path)
    general = SqliteFactStore(conn, agent_id="default")
    coding = SqliteFactStore(conn, agent_id="coding")
    general.add("project", "keep this")
    fact_id = general.list()[0]["id"]

    assert coding.update(fact_id, "corrected by coding") is True
    assert general.list()[0]["content"] == "corrected by coding"
    # ...and one Agent forgetting something removes it for everyone.
    assert coding.delete(fact_id) is True
    assert general.list() == []


def test_a_fact_written_by_one_agent_is_found_by_another(tmp_path):
    """Reads too, not just writes: the point of sharing is that the agent you
    happen to be talking to knows what you already told a different one."""
    conn = connect(tmp_path)
    coding = SqliteFactStore(conn, agent_id="coding")
    coding.add("user", "lives in Dalian")

    assert SqliteFactStore(conn, agent_id="learning").search("Dalian") == [
        "[user] lives in Dalian"
    ]

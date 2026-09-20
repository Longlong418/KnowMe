"""MVP guarantees: profiles share the core but never share private state."""

from knowme.agents import get_profile, list_profiles
from knowme.db import connect
from knowme.memory.semantic.store import SqliteFactStore
from knowme.ops.browser_agent import resume_or_new_session
from knowme.ops.dashboard import _thread_history, session_list


def test_builtin_profiles_are_small_explicit_agent_specs():
    profiles = list_profiles()
    assert [profile.id for profile in profiles] == [
        "default", "coding", "learning", "research"
    ]
    assert all(profile.spec.name == profile.id for profile in profiles)
    assert get_profile("default").spec.tools is None
    assert "get_document" in get_profile("learning").spec.tools


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


def test_one_agent_cannot_edit_or_delete_another_agents_fact(tmp_path):
    conn = connect(tmp_path)
    general = SqliteFactStore(conn, agent_id="default")
    coding = SqliteFactStore(conn, agent_id="coding")
    general.add("project", "keep this")
    fact_id = general.list()[0]["id"]

    assert coding.update(fact_id, "overwritten") is False
    assert coding.delete(fact_id) is False
    assert general.list()[0]["content"] == "keep this"

"""DETERMINISTIC EVAL — the browser gateway's shared agent survives a swap.

The dashboard is the one gateway that is both multi-threaded and long-lived, so
it keeps a single KnowMe behind `knowme.ops.browser_agent`. Two callers mutate it:
dashboard.py builds it on the first chat, settings_api rebuilds it when you
change provider or model. This pins the part that is easy to get wrong.

The bug this was written for: changing ANY setting rebuilt the agent, and a
fresh KnowMe starts on the eternal 'default' session. So one visit to the Settings
tab silently moved you into a different conversation — your chat was still in
the database, just no longer the thread the dock was showing. It looked like
data loss and was reported as one.
"""

from __future__ import annotations

import pytest

from knowme.ops import browser_agent


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A throwaway home + a clean module global, so these tests never see (or
    leave behind) the real dashboard's agent."""
    monkeypatch.setenv("KNOWME_HOME", str(tmp_path))
    monkeypatch.setenv("KNOWME_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(browser_agent, "_agent", None)
    monkeypatch.setattr(browser_agent, "_dashboard_session", None)
    return tmp_path


def test_rebuild_keeps_the_conversation(isolated):
    """A settings change swaps the BRAIN, not the thread."""
    first = browser_agent.get_agent()
    session = first.session.session_id
    assert session != "default", "a dashboard run must never chat into 'default'"

    assert browser_agent.rebuild() is None          # None == success
    after = browser_agent.current()

    assert after is not first, "rebuild must actually build a new agent"
    assert after.session.session_id == session, (
        "rebuild dropped the chat thread — the dock would show an empty "
        "conversation and the user's messages would look lost"
    )


def test_the_page_and_the_first_turn_agree_on_the_thread(isolated, monkeypatch):
    """Two callers asking "which thread is current?" must get one answer.

    Live bug: the reader panel asked about the open document and the agent
    answered "I don't have the document you have open". The page had asked
    /api/data for the current thread, published the snapshot under it, and the
    turn then ran in a DIFFERENT thread — because dash_session() and get_agent()
    each called resume_or_new_session() on their own. Both saw a stale last
    message, both started a fresh thread, and nothing tied the two together.

    _new_session_id is stubbed to a counter on purpose: its real ids only differ
    once a second has passed, so a same-second double call would pass this test
    even when the bug is present.
    """
    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    first = browser_agent.get_agent()
    old = first.session.session_id
    first.conn.execute(
        "INSERT INTO chat_log (role, content, session_id, created_at) "
        "VALUES ('user', 'we talked ages ago', ?, datetime('now', '-3 hours'))",
        (old,),
    )
    first.conn.commit()

    # a dashboard restart: same database, empty process globals
    monkeypatch.setattr(browser_agent, "_agent", None)
    monkeypatch.setattr(browser_agent, "_dashboard_session", None)
    stamp = iter(f"dashboard-fresh-{n:02d}" for n in range(1, 10))
    monkeypatch.setattr(browser_agent, "_new_session_id", lambda agent_id="default": next(stamp))

    for_the_page = browser_agent.dash_session()      # what /api/data reports
    for_the_turn = browser_agent.get_agent()         # what chat_stream runs in

    assert for_the_page != old, "3 idle hours: a returning user gets a new thread"
    assert for_the_turn.session.session_id == for_the_page, (
        "the turn ran in a thread the page was never told about — anything the "
        "page published (the open document, a selection) lands on the wrong key"
    )


def test_rebuild_without_a_prior_agent_still_gets_a_dated_session(isolated):
    """Hitting Settings before ever chatting is a legitimate first action. It
    must not leave the agent parked on 'default' either."""
    assert browser_agent.current() is None
    assert browser_agent.rebuild() is None
    assert browser_agent.current().session.session_id.startswith("dashboard-")


def test_a_failed_rebuild_keeps_the_working_agent(isolated, monkeypatch):
    """A typo'd key should cost you the switch, not the agent you already had.
    Losing both is the failure mode that makes a local tool feel broken."""
    first = browser_agent.get_agent()

    def explode(*a, **k):
        raise SystemExit("no API key found")     # what get_client actually raises

    monkeypatch.setattr("knowme.app.KnowMe", explode)

    error = browser_agent.rebuild()
    assert error and "no API key" in error
    assert browser_agent.current() is first, "a failed swap must not orphan the user"
    assert first.session.session_id, "and the surviving agent keeps its thread"

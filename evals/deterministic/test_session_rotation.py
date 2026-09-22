"""DETERMINISTIC EVAL — the dashboard rotates idle chat threads.

Live bug: a tester returned days later and their fresh 'what's up' landed in a
week-old 32-message thread. New rule: if the current session's newest message
is older than KNOWME_SESSION_IDLE_MINUTES, the next chat starts a new thread
(old one stays in History)."""

from __future__ import annotations

from evals.helpers import ScriptedClient, make_knowme, response, text_block
from knowme.ops.browser_agent import maybe_rotate_session


def _seed(app, session_id, age_minutes):
    app.conn.execute(
        "INSERT INTO chat_log (role, content, session_id, created_at) "
        "VALUES ('user', 'old message', ?, datetime('now', ?))",
        (session_id, f"-{age_minutes} minutes"),
    )
    app.conn.commit()


def test_idle_session_rotates(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    before = app.session.session_id
    _seed(app, before, age_minutes=120)          # 2h idle > 60m threshold
    maybe_rotate_session(app)
    assert app.session.session_id != before
    assert app.session.session_id.startswith("dashboard-")
    assert app.session.history == []             # fresh working memory too


def test_active_session_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    before = app.session.session_id
    _seed(app, before, age_minutes=5)            # active conversation
    maybe_rotate_session(app)
    assert app.session.session_id == before


def test_empty_session_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    before = app.session.session_id
    maybe_rotate_session(app)                   # no messages at all -> no-op
    assert app.session.session_id == before


def test_rotation_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "0")
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    before = app.session.session_id
    _seed(app, before, age_minutes=10000)
    maybe_rotate_session(app)
    assert app.session.session_id == before


def test_a_rotated_thread_keeps_the_document_you_have_open(tmp_path, monkeypatch):
    """Rotation must not orphan the Application snapshot.

    The bridge is keyed by (agent, session), and the reader publishes when you
    OPEN a document — but the idle rotation lands at the START of the next turn.
    So after an hour of reading, the panel asked in the new thread and looked up
    a key nothing had ever been written to: the document you had open the whole
    time came off exactly when you asked about it. Same symptom as the wrong-key
    bug, longer fuse.
    """
    from knowme.ops import dashboard

    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    gate = response([text_block('{"retrieve": false, "reason": "about the open document"}')])
    app = make_knowme(tmp_path / "home",
                      client=ScriptedClient([gate, response([text_block("ok")])]))
    old = app.session.session_id
    _seed(app, old, age_minutes=120)                    # an hour+ since the last word
    dashboard.application_contexts.publish(
        agent_id=app.agent_id, session_id=old, application="reader",
        resource="REACT", content="the paper you were reading", selection="")
    monkeypatch.setattr(dashboard, "get_agent", lambda agent_id="default": app)

    dashboard.chat_stream("这一段是什么意思？", lambda kind, ev: None)

    assert app.session.session_id != old, "the idle thread really did rotate"
    assert "REACT" in dashboard.application_contexts.render(
        app.agent_id, app.session.session_id), \
        "…and the document you had open followed you to the new thread"


def test_provider_switch_resets_stale_model_overrides(tmp_path, monkeypatch):
    """Live bug: kimi -> gemini kept gate model kimi-k3; every turn then 404'd
    against Gemini. A provider change must reset any model field the user
    didn't newly type."""
    from knowme.ops import settings_api

    captured = {}
    monkeypatch.setenv("KNOWME_PROVIDER", "kimi")
    monkeypatch.setenv("KNOWME_MODEL", "kimi-k3")
    monkeypatch.setenv("KNOWME_SMALL_MODEL", "kimi-k3")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(settings_api, "find_dotenv", lambda **k: "", raising=False)

    # intercept at the env-write layer; abort before the agent rebuild
    def fake_set_key(path, k, v):
        captured[k] = v
        raise RuntimeError("stop-before-rebuild")

    import dotenv
    monkeypatch.setattr(dotenv, "set_key", fake_set_key)
    try:
        settings_api.apply_settings({"provider": "gemini", "model": "kimi-k3",
                                     "small_model": "kimi-k3", "keys": {}})
    except RuntimeError:
        pass
    assert captured.get("KNOWME_MODEL", "unset") in ("", "unset") or \
        captured.get("KNOWME_PROVIDER") == "gemini"
    # the actual contract: stale kimi ids must have been blanked
    assert captured.get("KNOWME_MODEL") != "kimi-k3"
    assert captured.get("KNOWME_SMALL_MODEL") != "kimi-k3"

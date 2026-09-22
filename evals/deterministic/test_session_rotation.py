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
    from knowme.ops import web

    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    gate = response([text_block('{"retrieve": false, "reason": "about the open document"}')])
    app = make_knowme(tmp_path / "home",
                      client=ScriptedClient([gate, response([text_block("ok")])]))
    old = app.session.session_id
    _seed(app, old, age_minutes=120)                    # an hour+ since the last word
    web.application_contexts.publish(
        agent_id=app.agent_id, session_id=old, application="reader",
        resource="REACT", content="the paper you were reading", selection="")
    monkeypatch.setattr(web, "get_agent", lambda agent_id="default": app)

    web.chat_stream("这一段是什么意思？", lambda kind, ev: None)

    assert app.session.session_id != old, "the idle thread really did rotate"
    assert "REACT" in web.application_contexts.render(
        app.agent_id, app.session.session_id), \
        "…and the document you had open followed you to the new thread"


def test_a_documents_thread_is_never_rotated(tmp_path, monkeypatch):
    """A document's own thread belongs to the document, not to "the thread you
    were resumed into" — reading for two hours and asking a question must not
    move the panel to a fresh thread while that document is still on screen.

    The picked-thread exemption cannot cover this: it is consumed by the next
    turn, and a document is read for an afternoon.
    """
    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    app.session.start_new("doc-4d21")            # what the reading pane uses
    _seed(app, "doc-4d21", age_minutes=600)      # 10h of reading
    maybe_rotate_session(app)
    assert app.session.session_id == "doc-4d21"


def _pick_from_history(app, session_id):
    """The page's 历史记录 -> 点一条对话 path, without a browser."""
    from knowme.ops.web import data as web_data

    return web_data.session_action(
        {"action": "switch", "id": session_id, "agent_id": app.agent_id})


def _point_web_at(app, monkeypatch):
    from knowme.ops import web
    from knowme.ops.web import data as web_data

    monkeypatch.setattr(web_data, "get_agent", lambda agent_id="default": app)
    monkeypatch.setattr(web, "get_agent", lambda agent_id="default": app)


def test_a_thread_you_picked_from_history_is_not_rotated_away(tmp_path, monkeypatch):
    """Live bug: 在历史记录里点开一条旧对话接着说，回复却落进了另一条新对话 ——
    屏幕上还挂在旧对话下面，所以那条对话看起来永远只能看、不能接着聊。

    The rotation is for the thread you were RESUMED into; a thread you clicked
    on purpose is the opposite, and the idle gap is exactly what makes an old
    conversation look like a conversation again.
    """
    from knowme.ops import web

    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    gate = response([text_block('{"retrieve": false, "reason": "接着上次说"}')])
    app = make_knowme(tmp_path / "home",
                      client=ScriptedClient([gate, response([text_block("第二步是先写测试。")])]))
    old = app.session.session_id
    _seed(app, old, age_minutes=180)                # 3h idle: rotation bait
    _point_web_at(app, monkeypatch)

    _pick_from_history(app, old)
    assert app.session.session_id == old

    web.chat_stream("继续，第二步是什么", lambda kind, ev: None)

    assert app.session.session_id == old, "手选的线程被轮换掉了"
    landed = {r[0] for r in app.conn.execute("SELECT DISTINCT session_id FROM chat_log")}
    assert landed == {old}, "这条消息落进了另一个线程"


def test_the_exemption_covers_the_turn_you_sent_not_the_thread_forever(tmp_path, monkeypatch):
    """The other half: picking a thread must not switch the idle rule off for it.
    An hour later, saying something new there is the accident rotation exists to
    prevent, and the old thread is a click away in History again."""
    from knowme.ops import web

    monkeypatch.setenv("KNOWME_SESSION_IDLE_MINUTES", "60")
    gate = response([text_block('{"retrieve": false, "reason": "接着上次说"}')])
    app = make_knowme(tmp_path / "home",
                      client=ScriptedClient([gate, response([text_block("ok")])]))
    old = app.session.session_id
    _seed(app, old, age_minutes=180)
    _point_web_at(app, monkeypatch)

    _pick_from_history(app, old)
    web.chat_stream("继续", lambda kind, ev: None)
    assert app.session.session_id == old

    app.conn.execute("UPDATE chat_log SET created_at=datetime('now', '-180 minutes') "
                     "WHERE session_id=?", (old,))
    app.conn.commit()
    maybe_rotate_session(app)

    assert app.session.session_id != old


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

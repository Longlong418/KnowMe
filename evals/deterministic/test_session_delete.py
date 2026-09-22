"""DETERMINISTIC EVAL — deleting a conversation.

Live request: 给对话管理加上删除对话的功能, and it has to be final (消息彻底删掉,
不可恢复 — what was already distilled into memory stays, because a fact never
carried a session id).

The hard part is that a "thread" is not one table. Its messages are chat_log
rows, its archived tail is two more tables plus the .jsonl files those point at,
and the document it was reading is a Context Bridge entry keyed by
(agent, session). Deleting the thread you are IN also has to move the agent
somewhere, or the next message appends itself to a conversation that is gone."""

from __future__ import annotations

import pytest

from evals.helpers import ScriptedClient, make_knowme, response, text_block
from knowme.core.context import snip_compact
from knowme.ops.web import data as web_data


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """session_action/library_action open their OWN connection from
    load_settings(), not the one make_knowme built — so without this the web
    layer would reach into the maintainer's real .knowme and rename or delete
    documents there. Same value make_knowme gets, so both see one home."""
    monkeypatch.setenv("KNOWME_HOME", str(tmp_path / "home"))


def _seed_messages(app, session_id, text="hi", agent_id="default"):
    app.conn.execute(
        "INSERT INTO chat_log (role, content, session_id, source, agent_id) "
        "VALUES ('user', ?, ?, 'dashboard', ?)", (text, session_id, agent_id))
    app.conn.commit()


def _seed_archives(app, home, session_id):
    """Both archive shapes, the way the compactor writes them: the plain one at a
    path that is a pure function of the id, and a dated one per compaction."""
    plain = snip_compact.archive_path(home, session_id)
    plain.parent.mkdir(parents=True, exist_ok=True)
    plain.write_text('{"role": "user", "content": "archived"}\n', encoding="utf-8")
    dated = home / snip_compact.ARCHIVES_DIR / f"{snip_compact._slug(session_id)}-compacted-20260101-120000.jsonl"
    dated.write_text('{"role": "assistant", "content": "summarised"}\n', encoding="utf-8")
    app.conn.execute(
        "INSERT INTO history_snips (session_id, tail_start, archived, archive_path, updated_at) "
        "VALUES (?, 1, 1, ?, datetime('now'))", (session_id, str(plain)))
    app.conn.execute(
        "INSERT INTO history_summaries (session_id, covered, archive_path, summary, updated_at) "
        "VALUES (?, 1, ?, 'a summary', datetime('now'))", (session_id, str(dated)))
    app.conn.commit()
    return plain, dated


def _point_at(app, monkeypatch):
    from knowme.ops import web
    monkeypatch.setattr(web_data, "get_agent", lambda agent_id="default": app)
    monkeypatch.setattr(web, "get_agent", lambda agent_id="default": app)
    return web


def test_deleting_a_thread_removes_everything_it_lives_in(tmp_path, monkeypatch):
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    monkeypatch.setattr(web_data, "get_agent", lambda agent_id="default": app)
    victim, keeper = "dashboard-old", app.session.session_id
    _seed_messages(app, victim, "删掉这段")
    _seed_messages(app, keeper, "留着这段")
    plain, dated = _seed_archives(app, tmp_path / "home", victim)
    from knowme.ops import web
    web.application_contexts.publish(
        agent_id=app.agent_id, session_id=victim, application="reader",
        resource="old.md", content="一份已经删掉的文档", selection="")

    out = web_data.session_action({"action": "delete", "id": victim,
                                   "agent_id": app.agent_id})

    assert out["ok"]
    assert app.conn.execute("SELECT COUNT(*) FROM chat_log WHERE session_id=?",
                            (victim,)).fetchone()[0] == 0
    assert app.conn.execute("SELECT COUNT(*) FROM history_snips WHERE session_id=?",
                            (victim,)).fetchone()[0] == 0
    assert app.conn.execute("SELECT COUNT(*) FROM history_summaries WHERE session_id=?",
                            (victim,)).fetchone()[0] == 0
    assert not plain.exists() and not dated.exists(), "归档文件也一起走"
    assert web.application_contexts.render(app.agent_id, victim) == "", \
        "桥上那条快照也得撤掉"

    # …and nobody else's conversation was touched on the way past.
    assert app.conn.execute("SELECT COUNT(*) FROM chat_log WHERE session_id=?",
                            (keeper,)).fetchone()[0] == 1


def test_deleting_the_thread_you_are_in_moves_you_to_a_fresh_one(tmp_path, monkeypatch):
    """The one that would come back to bite: leave the agent in a deleted thread
    and your next message appends itself to a conversation the page no longer
    shows — or, if the id is reseated by `switch`, to an EMPTY thread still
    labelled with the dead id."""
    gate = response([text_block('{"retrieve": false, "reason": "换个话题了"}')])
    app = make_knowme(tmp_path / "home",
                      client=ScriptedClient([gate, response([text_block("好的")])]))
    web = _point_at(app, monkeypatch)
    old = app.session.session_id
    _seed_messages(app, old, "这条对话要被删掉")
    web.application_contexts.publish(
        agent_id=app.agent_id, session_id=old, application="reader",
        resource="REACT", content="the paper you were reading", selection="")

    out = web_data.session_action({"action": "delete", "id": old,
                                   "agent_id": app.agent_id})

    assert out["session_id"] != old, "agent 不能还停在被删掉的那条会话上"
    assert app.session.session_id == out["session_id"]
    assert app.session.history == [], "新会话是空的，不会带着旧消息复活"
    assert "REACT" in web.application_contexts.render(app.agent_id, out["session_id"]), \
        "你正打开的那份文档跟着你走（rekey，不是清掉再补）"

    web.chat_stream("那继续", lambda kind, ev: None)
    landed = {r[0] for r in app.conn.execute("SELECT DISTINCT session_id FROM chat_log")}
    assert landed == {out["session_id"]}, "下一条消息落进了已经删掉的那条会话"


def test_deleting_some_other_thread_leaves_this_one_alone(tmp_path, monkeypatch):
    """History is a list of conversations you can delete from; deleting one you
    are not in must not move you off the one you are."""
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    web = _point_at(app, monkeypatch)
    current, other = app.session.session_id, "dashboard-old"
    _seed_messages(app, other, "旧的那条")
    web.application_contexts.publish(
        agent_id=app.agent_id, session_id=current, application="reader",
        resource="REACT", content="still reading", selection="")
    web.application_contexts.publish(
        agent_id=app.agent_id, session_id=other, application="reader",
        resource="old.md", content="gone", selection="")

    out = web_data.session_action({"action": "delete", "id": other,
                                   "agent_id": app.agent_id})

    assert out["session_id"] == current, "没动的会话不该被换掉"
    assert app.session.session_id == current
    assert "REACT" in web.application_contexts.render(app.agent_id, current), \
        "手上这份文档不能因为删了别条对话就被清掉"
    assert web.application_contexts.render(app.agent_id, other) == ""


def test_deleting_a_document_deletes_its_own_conversation(tmp_path, monkeypatch):
    """One document, one conversation (browser_agent.DOC_PREFIX) — so deleting
    the document has to take that conversation with it, or a deleted document's
    chat stays in History and its rows stay in chat_log forever."""
    from knowme.applications.library import parse_and_save
    from knowme.ops import web

    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    monkeypatch.setattr(web, "get_agent", lambda agent_id="default": app)
    home = tmp_path / "home"
    doc = parse_and_save(app.conn, home, name="paper.md", raw=b"# REACT\n\ntext\n",
                         content_type="text/markdown", source="paper.md",
                         added_by="default")
    sid = "doc-" + doc["id"]
    # …under the READING agent: a document's conversation is the reading pane's,
    # which is why deleting the document can find it by nothing but its id.
    _seed_messages(app, sid, "这份文档里这段什么意思？", agent_id="reader")
    _seed_messages(app, "dashboard-keep", "别的对话")
    web.application_contexts.publish(
        agent_id="reader", session_id=sid, application="reader",
        resource="paper.md", content="the paper", selection="")

    out = web_data.library_action({"action": "delete", "doc_id": doc["id"]})

    assert out["ok"]
    assert app.conn.execute("SELECT COUNT(*) FROM chat_log WHERE session_id=?",
                            (sid,)).fetchone()[0] == 0
    assert web.application_contexts.render("reader", sid) == ""
    assert app.conn.execute("SELECT COUNT(*) FROM chat_log WHERE session_id='dashboard-keep'"
                            ).fetchone()[0] == 1


def test_renaming_a_document_changes_only_its_name(tmp_path, monkeypatch):
    """Only the name in the library moves: the id, the stored file and the text
    are the document, and the title is just what we call it here."""
    from knowme.applications.library import file_path, get_document, parse_and_save

    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    home = tmp_path / "home"
    doc = parse_and_save(app.conn, home, name="paper.md", raw=b"# REACT\n\ntext\n",
                         content_type="text/markdown", source="paper.md",
                         added_by="default")
    before = get_document(app.conn, doc["id"])

    out = web_data.library_action({"action": "rename", "doc_id": doc["id"],
                                   "title": "  REACT 阅读笔记  "})

    assert out == {"ok": True, "title": "REACT 阅读笔记"}      # trimmed
    after = get_document(app.conn, doc["id"])
    assert after["title"] == "REACT 阅读笔记"
    assert (after["id"], after["kind"], after["chars"], after["path"]) == \
        (before["id"], before["kind"], before["chars"], before["path"])
    assert file_path(app.conn, home, doc["id"]).read_bytes() == b"# REACT\n\ntext\n"


def test_rename_refuses_an_empty_name_and_an_unknown_document(tmp_path, monkeypatch):
    from knowme.applications.library import MAX_TITLE, get_document, parse_and_save

    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    doc = parse_and_save(app.conn, tmp_path / "home", name="paper.md",
                         raw=b"# REACT\n", content_type="text/markdown",
                         source="paper.md", added_by="default")

    blank = web_data.library_action({"action": "rename", "doc_id": doc["id"], "title": "   "})
    assert blank["ok"] is False
    assert get_document(app.conn, doc["id"])["title"] == "paper"     # unchanged

    missing = web_data.library_action({"action": "rename", "doc_id": "nope", "title": "x"})
    assert missing["ok"] is False

    long = web_data.library_action({"action": "rename", "doc_id": doc["id"],
                                    "title": "字" * (MAX_TITLE + 50)})
    assert long["ok"] and len(long["title"]) == MAX_TITLE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

"""extra_context — the Application Context Bridge's seat in the prompt.

Empty (the default, and every turn so far) changes nothing byte for byte.
Non-empty lands at the END of the [context] block, after retrieved memory, so
the prefix a provider caches is the same whether or not an app contributed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from knowme.config import Settings
from knowme.core.session import Session


class _Memory:
    conn = None

    def gated_retrieve(self, message, notify=None):
        return "- alex prefers mornings"


def _session(tmp_path, memory=None) -> Session:
    settings = Settings(home=tmp_path / "home")
    settings.ensure_home()
    return Session(settings, memory=memory)


def test_empty_extra_is_byte_identical(tmp_path, monkeypatch):
    session = _session(tmp_path)
    fixed = SimpleNamespace(astimezone=lambda: __import__("datetime").datetime(2026, 9, 20, 10, 0))
    monkeypatch.setattr("knowme.core.session.datetime",
                        SimpleNamespace(now=lambda: fixed))
    assert session.build_turn_context("hi") == session.build_turn_context("hi", extra="")


def test_extra_lands_after_memory(tmp_path):
    session = _session(tmp_path, memory=_Memory())
    out = session.build_turn_context("hi", extra="[app: reader]\nSelected: 'the fox'")
    assert out.startswith("[context]\nRight now it is")
    assert out.index("Relevant memory") < out.index("[app: reader]")
    assert out.endswith("Selected: 'the fox'")


def test_runtime_passes_extra_through(tmp_path):
    """run_loop_turn hands extra_context to the session and the prompt shows it."""
    from evals.helpers import ScriptedClient, make_knowme, response, text_block

    seen = {}

    class Client(ScriptedClient):
        def _create(self, **kwargs):
            # copy: run_loop appends to this list after the call returns
            seen["messages"] = list(kwargs["messages"])
            return super()._create(**kwargs)

    gate = response([text_block('{"retrieve": false, "query": "", "reason": "x"}')])
    app = make_knowme(tmp_path / "home", client=Client([gate, response([text_block("ok")])]))
    r = app.runtime.run_turn(app.spec, app.session, "what is this?",
                             extra_context="[app: reader]\nDocument: notes.md")
    assert r.reply == "ok"
    prompt = seen["messages"][-1]["content"]
    assert "[app: reader]\nDocument: notes.md\n\nwhat is this?" in prompt
    # the stored history keeps the BARE message — context never accumulates
    assert app.session.history[0]["content"] == "what is this?"


def test_facade_passes_application_context_and_emits_transparent_events(tmp_path):
    from evals.helpers import ScriptedClient, make_knowme, response, text_block

    gate = response([text_block('{"retrieve": false, "query": "", "reason": "x"}')])
    app = make_knowme(
        tmp_path / "home",
        client=ScriptedClient([gate, response([text_block("ok")])]),
    )
    events = []

    result = app.respond(
        "explain it",
        observer=lambda kind, event: events.append((kind, event)),
        extra_context="[application context]\nApplication: reader",
    )

    assert result.reply == "ok"
    kinds = [kind for kind, _ in events]
    assert "turn_start" in kinds
    assert "context" in kinds
    assert "turn_end" in kinds
    context = next(event for kind, event in events if kind == "context")
    assert context["application_chars"] > 0
    assert context["agent_id"] == "default"
    row = app.conn.execute(
        "SELECT meta FROM chat_log WHERE role='assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    saved = json.loads(row["meta"])["context"]
    assert saved["application_chars"] == context["application_chars"]
    assert saved["sent_messages"] == context["sent_messages"]

"""DETERMINISTIC EVAL — working-memory assembly is pure string logic.

Regression net for a live bug found on the dashboard: the agent had the date
but not the time, so it asked the user "what time is it?" before scheduling
something "in 30 minutes." The prompt must still carry a real clock.

Plus the invariant that split the prompt in two: the SYSTEM prompt holds nothing
per-turn, so a provider's prefix cache survives. The clock, the retrieved memory
and the matched skills all ride with the user's message instead.
"""

from __future__ import annotations

import re

from knowme.config import load_settings
from knowme.runtime.session import Session


def test_the_turn_carries_the_current_time():
    settings = load_settings()
    settings.ensure_home()
    session = Session(settings, memory=None)
    prompt = session.build_system() + session.build_turn_context(
        "what should I do in 30 minutes?")
    # a HH:MM clock must be present — not just a date — so the model never has
    # to ask the user for the time (the live bug). It rides with the message now
    # rather than the system prompt; what matters is that it is still there.
    assert re.search(r"\b\d{2}:\d{2}\b", prompt), "the turn is missing a HH:MM time"
    assert "Right now it is" in prompt


def test_system_prompt_carries_no_clock():
    """The system prompt must hold NOTHING per-turn.

    A clock here changes every minute, and prompt caching is a prefix match:
    every byte after it is re-billed at full price on every turn. Silent when it
    regresses — the request still succeeds, the bill is just larger.
    """
    settings = load_settings()
    settings.ensure_home()
    system = Session(settings, memory=None).build_system()
    assert not re.search(r"\b\d{2}:\d{2}\b", system), "a clock is back in the system prompt"
    assert "Right now it is" not in system


class _CountingMemory:
    """A memory stub whose every answer differs — so a leak into the system
    prompt shows up as a changed string rather than as a flaky test."""

    def __init__(self):
        self.calls = 0

    def gated_retrieve(self, message, notify=None):
        self.calls += 1
        return f"remembered fact {self.calls}"


def test_per_turn_memory_does_not_leak_into_the_system_prompt():
    """Retrieved memory changes every turn by design, so it cannot sit in the
    system prompt without killing the prefix cache on every turn."""
    settings = load_settings()
    settings.ensure_home()
    session = Session(settings, memory=_CountingMemory())

    system = session.build_system()
    first = session.build_turn_context("alpha")
    second = session.build_turn_context("beta")

    assert "remembered fact 1" not in system
    # ...while still reaching the turn context, which is what the model reads
    assert "remembered fact 1" in first
    assert "remembered fact 2" in second
    # and neither turn moved the system prompt
    assert session.build_system() == system


def test_the_turn_context_is_not_persisted(tmp_path):
    """The context block is PROMPT-ONLY — it must never reach the database.

    It exists so the system prompt can stay free of per-turn content. If it also
    landed in chat_log it would be replayed into every later turn's history —
    growing without bound, re-showing a long-stale clock — and consolidation
    would distil a timestamp and a memory dump into durable "facts".

    Two separate things keep it out, and either could regress alone:
      1. respond() passes the BARE user_message to add_exchange(), not the
         context-prefixed prompt;
      2. `messages` handed to run_loop is a fresh list (history[-window:] + [..]),
         so run_loop's in-place appends cannot touch session.history.
    """
    from evals.helpers import ScriptedClient, make_knowme, response, text_block

    client = ScriptedClient([
        response([text_block('{"retrieve": false}')]),   # the retrieval gate
        response([text_block("Booked.")]),               # the loop's reply
    ])
    app = make_knowme(tmp_path, client=client)
    app.respond("book a court")

    rows = [r["content"] for r in app.conn.execute("select content from chat_log")]
    assert rows, "nothing was logged at all"
    for content in rows:
        assert "[context]" not in content, "the context block reached chat_log"
        assert "Right now it is" not in content, "the clock reached chat_log"
    for message in app.session.history:
        assert "[context]" not in message["content"], "the context block reached working memory"


def test_session_tags_history_with_its_session_id():
    # sessions are just a session_id label; a fresh Session carries the default.
    settings = load_settings()
    assert Session(settings, memory=None).session_id == "default"
    s = Session(settings, memory=None)
    s.start_new("s-test")
    assert s.session_id == "s-test" and s.history == []


def test_system_prompt_includes_own_model_identity():
    """Live bug on the dashboard (K3 launch day): asked "what's ur model", the
    agent said it had no idea what it was running on. The system prompt must
    name the model + provider so the agent can answer honestly."""
    settings = load_settings()
    settings.ensure_home()
    settings.provider, settings.model = "kimi", "kimi-k3"
    system = Session(settings, memory=None).build_system()
    assert "kimi-k3" in system and "'kimi' provider" in system

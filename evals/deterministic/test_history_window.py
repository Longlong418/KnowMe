"""DETERMINISTIC EVAL — working memory is bounded, through the whole app.

Sean's insight while testing Telegram: its one always-on session accumulated
history forever, and every turn resent the whole thing (unbounded context ->
cost/latency climb -> eventual context-limit break). Working memory must be
bounded; older turns live in state.db + consolidation, not the prompt.

That premise has not changed. What changed (2026-09-20) is the mechanism, and
this file moved with it. The bound used to be history[-N:], which dropped every
older turn without a trace — the model answered as though the conversation had
started late. It is now snip_compact, which ARCHIVES what it removes and leaves
a marker saying how much went and where. The assertions below are the same
concern expressed against the new behaviour, plus the one thing the window could
never give: evidence that the model can tell something came before.

Unit-level rules for the snipping itself live in test_snip_compact.py. This file
drives the whole app, because a bound that works in isolation and is never
called from respond() is not a bound.
"""

from __future__ import annotations

from evals.helpers import ScriptedClient, make_knowme, response, text_block

HEAD, TAIL = 3, 4          # small, so a few turns reach the trigger
AT = HEAD + 1 + TAIL       # 8

# Consolidation also calls the model — every consolidate_every exchanges, six by
# default — so ten turns would trip it and eat a scripted response here. It is
# pushed out of range in every make_knowme below: this file is about the history
# bound, not about consolidation, and a test that fails for the other reason is
# worse than no test.
_NO_CONSOLIDATION = {"consolidate_every": 10_000}


def _gate_skip():
    return response([text_block('{"retrieve": false, "query": "", "reason": "t"}')])


def test_the_prompt_stays_bounded_over_many_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWME_SNIP_HEAD", str(HEAD))
    monkeypatch.setenv("KNOWME_SNIP_TAIL", str(TAIL))
    sent = []

    class Recorder(ScriptedClient):
        def _create(self, **kwargs):
            # snapshot the message count NOW — run_loop mutates the same list
            # (appends the assistant reply) after this call returns
            sent.append(list(kwargs.get("messages", [])))
            return self._script.pop(0)

    turns = 10
    script = []
    for _ in range(turns):
        script += [_gate_skip(), response([text_block("ok")])]
    app = make_knowme(tmp_path / "home", client=Recorder(script), **_NO_CONSOLIDATION)
    for i in range(turns):
        app.respond(f"message number {i}")

    last = sent[-1]
    assert len(last) <= AT + 1, f"working memory grew unbounded: {len(last)} messages"
    blob = " ".join(str(m.get("content", "")) for m in last)
    assert "message number 0" in blob, "the opening was cut — the thread lost its subject"
    assert f"message number {turns - 1}" in blob, "the newest turn is missing"
    assert "message number 4" not in blob, "the middle was never removed"


def test_the_model_is_told_that_earlier_messages_exist(tmp_path, monkeypatch):
    """The one thing the sliding window could never do.

    history[-24:] removed turns silently, so the model had no way to know its
    conversation had a past it could not see. The prompt must now carry a marker
    naming the archive — otherwise this is the same silent loss with extra
    steps."""
    monkeypatch.setenv("KNOWME_SNIP_HEAD", str(HEAD))
    monkeypatch.setenv("KNOWME_SNIP_TAIL", str(TAIL))

    script = []
    for _ in range(10):
        script += [_gate_skip(), response([text_block("ok")])]
    app = make_knowme(tmp_path / "home", client=ScriptedClient(script), **_NO_CONSOLIDATION)
    for i in range(10):
        app.respond(f"message number {i}")

    # the marker is in working memory, so the NEXT turn's prompt carries it
    assert any("archived to" in m["content"] for m in app.session.history), "no marker"
    archived = tmp_path / "home" / "archives"
    assert archived.is_dir() and list(archived.iterdir()), "nothing was archived"


def test_defaults_bound_a_long_thread(tmp_path):
    """The shipped numbers, not the test overrides: 50 messages, and the trigger
    is DERIVED from head+tail so the three cannot disagree."""
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]), **_NO_CONSOLIDATION)

    assert app.settings.snip_head == 3
    assert app.settings.snip_tail == 46
    assert app.settings.snip_at == 50

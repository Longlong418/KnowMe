"""DETERMINISTIC EVAL — snip_compact bounds the conversation by ARCHIVING.

The rules, in the order they matter:

  1. nothing is due until the conversation is genuinely long — an ordinary turn
     is handed back the same list, untouched
  2. what it removes is appended to .knowme/archives/ BEFORE the marker points
     at it, so the pointer never names a file that does not exist yet
  3. the opening survives. A conversation that lost its first messages has lost
     its subject, and "what were we doing?" is the question the tail alone
     cannot answer
  4. the shape lands exactly at the threshold: head + 1 marker + tail
  5. reopening a session reproduces that shape instead of reviving the middle
  6. history holds no content blocks, so there is no tool_use/tool_result pair
     for a cut to break

Rule 6 is an assumption this design depends on. It is asserted rather than
assumed so that if history ever starts carrying real blocks, someone is told
here instead of by an invalid-request 400 in production.
"""

from __future__ import annotations

import json

from knowme.runtime.session import Session

from knowme.config import Settings
from knowme.db import connect
from knowme.runtime import snip_compact as sc

HEAD, TAIL = 3, 46
AT = HEAD + 1 + TAIL          # 50


def _flat(n: int) -> list[dict]:
    """n messages, alternating user/assistant from a user, as history really is."""
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
            for i in range(n)]


def _conn(tmp_path):
    return connect(tmp_path)


def test_a_short_conversation_is_handed_back_untouched(tmp_path):
    """The common case must be a no-op — including the same list object, so an
    ordinary turn pays nothing for a feature it never triggers.

    AT itself is still under the trigger: the conversation is allowed to reach
    50, it snips on the way past. Comparing against head+tail here made it snip
    one message early, forever."""
    history = _flat(AT)

    assert sc.plan(AT, HEAD, TAIL, AT) is None
    assert sc.snip(history, tmp_path, _conn(tmp_path), "s", HEAD, TAIL) is history
    assert not (tmp_path / sc.ARCHIVES_DIR).exists()


def test_one_message_over_the_threshold_snips(tmp_path):
    """The bound is the bound: at AT nothing happens, at AT+1 the conversation
    comes back down to AT."""
    history = _flat(AT + 1)

    out = sc.snip(history, tmp_path, _conn(tmp_path), "s", HEAD, TAIL)

    assert len(out) == AT, "a snip must land exactly on the threshold"
    assert out[HEAD]["role"] == "assistant", "the marker broke the alternation"


def test_the_opening_survives_and_the_marker_says_where(tmp_path):
    history = _flat(60)

    out = sc.snip(history, tmp_path, _conn(tmp_path), "s", HEAD, TAIL)

    assert [m["content"] for m in out[:HEAD]] == ["m0", "m1", "m2"], (
        "the opening was cut — the conversation lost its subject")
    assert "m3" not in [m["content"] for m in out], "the middle was not removed"
    assert "m59" in [m["content"] for m in out], "the tail was not kept"


def test_what_it_removes_is_on_disk_before_the_marker_points_at_it(tmp_path):
    """A pointer to a file that does not exist yet is the failure this ordering
    prevents: the write happens before the text that names it is built."""
    history = _flat(60)
    out = sc.snip(history, tmp_path, _conn(tmp_path), "s", HEAD, TAIL)

    archived = [json.loads(line) for line in
                sc.archive_path(tmp_path, "s").read_text(encoding="utf-8").splitlines()]

    assert [m["content"] for m in archived] == [f"m{i}" for i in range(HEAD, 14)]
    assert sc.archive_path(tmp_path, "s").name in out[HEAD]["content"]


def test_repeated_snips_accumulate_rather_than_overwrite(tmp_path):
    """Append-only, like usage.jsonl and the traces: twenty snips on one session
    leave one growing record, never twenty partial ones.

    Asserted as a property rather than a count — how much a given snip removes
    depends on how far past the trigger the conversation had drifted, and a
    hardcoded total silently encodes that instead of the rule.
    """
    conn = _conn(tmp_path)
    history = _flat(AT + 1)
    sizes = []
    for _ in range(20):
        history = sc.snip(history + _flat(2), tmp_path, conn, "s", HEAD, TAIL)
        sizes.append(len(sc.archive_path(tmp_path, "s").read_text(
            encoding="utf-8").splitlines()))

    assert sizes == sorted(sizes), "the archive shrank — a snip overwrote it"
    assert sizes[-1] > sizes[0], "nothing accumulated"
    assert len(history) == AT, "the live history drifted off the threshold"


def test_the_marker_is_replaced_not_archived(tmp_path):
    """The second snip begins one past the previous marker, not on it.

    Archiving the marker was a real bug: it sits at index HEAD, so a cut that
    started at HEAD swallowed it every time — the file filled with stale notices
    (two extra lines per snip, forever) and the reader had to filter them out to
    find the conversation."""
    conn = _conn(tmp_path)
    history = _flat(AT + 1)
    for _ in range(5):
        history = sc.snip(history + _flat(2), tmp_path, conn, "s", HEAD, TAIL)

    archived = [json.loads(line) for line in
                sc.archive_path(tmp_path, "s").read_text(encoding="utf-8").splitlines()]

    assert not [m for m in archived if sc.is_marker(m)], "a marker was archived"
    assert len([m for m in history if sc.is_marker(m)]) == 1, (
        "markers accumulated in the live history")


def test_reopening_a_session_reproduces_the_shape(tmp_path):
    """Without this the archived middle comes back the moment a user switches
    away and back: chat_log keeps every row, so a naive rebuild hands back the
    whole conversation and the snip silently undoes itself."""
    conn = _conn(tmp_path)
    history = _flat(60)
    snipped = sc.snip(history, tmp_path, conn, "s", HEAD, TAIL)

    rebuilt = sc.rebuild(history, tmp_path, conn, "s", HEAD, TAIL)

    assert len(rebuilt) == len(snipped)
    assert [m["content"] for m in rebuilt] == [m["content"] for m in snipped]


def test_rebuild_tracks_the_tail_through_many_snips(tmp_path):
    """Storing a tail INDEX survived one snip and drifted after that.

    The live history carries a marker slot the full log does not, so a stored
    index into the live list is off by one when read as an index into the log —
    and the error accumulates with every snip. Measured on a 35-turn session:
    the reopened view came back with 68 messages whose tail began twelve turns
    earlier than the live one's.

    A cumulative COUNT has no coordinate system to get wrong. Forty snips, and
    the reopened conversation must be the live one, exactly.
    """
    conn = _conn(tmp_path)
    full: list[dict] = _flat(2)
    history = list(full)
    for i in range(40):
        new = [{"role": "user", "content": f"u{i}"},
               {"role": "assistant", "content": f"a{i}"}]
        full += new
        history = sc.snip(history + new, tmp_path, conn, "s", HEAD, TAIL)

    rebuilt = sc.rebuild(full, tmp_path, conn, "s", HEAD, TAIL)

    assert [m["content"] for m in rebuilt] == [m["content"] for m in history]
    assert len(history) == AT


def test_the_marker_reports_the_total_missing_not_the_last_snip(tmp_path):
    """"12 earlier messages were archived" is a claim about the CONVERSATION,
    so it has to count everything that is missing — not just what the most
    recent snip happened to take. A per-snip count under-reports from the second
    snip onward."""
    conn = _conn(tmp_path)
    history = _flat(AT + 1)
    for _ in range(6):
        history = sc.snip(history + _flat(2), tmp_path, conn, "s", HEAD, TAIL)

    total = sc.archived_count(conn, "s")
    marker_text = next(m["content"] for m in history if sc.is_marker(m))

    assert total > 2, "nothing accumulated across the snips"
    assert f"{total} earlier messages" in marker_text, (
        "the marker reported one snip's share, not what is missing")
    # and the number claimed matches what the archive actually holds
    lines = sc.archive_path(tmp_path, "s").read_text(encoding="utf-8").splitlines()
    assert len(lines) == total


def test_a_session_that_was_never_snipped_rebuilds_whole(tmp_path):
    """The watermark must not invent a marker for a session that never had one."""
    history = _flat(60)

    assert sc.rebuild(history, tmp_path, _conn(tmp_path), "fresh", HEAD, TAIL) == history


def test_history_holds_no_content_blocks(tmp_path):
    """The assumption the whole design rests on.

    A cut between an assistant `tool_use` and the `user` `tool_result` that
    answers it is an invalid request, so a snip like this would normally have to
    protect that pairing. It does not have to here — add_exchange folds tool
    activity into a text "[tools used: ...]" line, so every entry is a plain
    string and every cut point is structurally safe.

    If history ever starts carrying real blocks, THIS is where it should be
    discovered, not in a 400 from the provider.
    """
    from evals.helpers import ScriptedClient, make_knowme, response, text_block, tool_block

    client = ScriptedClient([
        response([text_block('{"retrieve": false}')]),
        response([tool_block("create_event", {"title": "Swim", "start": "2026-09-21T17:00"},
                             "tu_1")], stop_reason="tool_use"),
        response([text_block("Booked it.")]),
    ])
    app = make_knowme(tmp_path / "home", client=client)
    app.respond("book a swim")

    assert app.session.history, "nothing was recorded"
    for message in app.session.history:
        assert isinstance(message["content"], str), (
            "history now holds content blocks — snip_compact's cut points can "
            "break a tool_use/tool_result pair and must protect it first")


def test_the_trigger_is_derived_so_the_numbers_cannot_disagree(tmp_path):
    settings = Settings()
    assert settings.snip_at == settings.snip_head + 1 + settings.snip_tail == AT


def test_the_session_wires_it_end_to_end(tmp_path):
    """Through Session, not the pure function: past the threshold the live
    history is bounded and a marker is present."""
    settings = Settings()
    settings.home = tmp_path
    settings.ensure_home()
    conn = connect(tmp_path)
    session = Session(settings, memory=None, conn=conn)

    for i in range(AT // 2 + 2):
        session.add_exchange(f"q{i}", f"a{i}")

    assert len(session.history) == AT, f"history grew unbounded: {len(session.history)}"
    assert any("archived to" in m["content"] for m in session.history), "no marker"
    assert session.history[0]["content"] == "q0", "the opening was cut"

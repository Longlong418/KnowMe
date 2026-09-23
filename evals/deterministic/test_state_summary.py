"""DETERMINISTIC EVAL — state_summary is the one lossy compressor, so its
failure modes are the ones that matter.

  1. IT FAILS OPEN. A summarising call that raises or comes back empty must
     leave working memory EXACTLY as it was. History is the only working memory
     there is; half-replacing it is worse than not compacting at all.
  2. THE COMPLETE HISTORY IS ARCHIVED, not the already-fitted copy. The human
     record is never the degraded one — the same rule chat_log follows.
  3. THE PROMPT DEMANDS WHAT THE GUIDE SAYS SUMMARISING DESTROYS. Identifiers,
     decision reasons, failed attempts, open threads. This is an assertion on
     the prompt text because the prompt IS the safety margin: rewrite it to a
     generic "summarise this" and everything else here still passes while the
     compressor quietly starts losing the things that break the next tool call.
  4. IT ROUND-TRIPS through a session switch, by COUNT rather than index.
"""

from __future__ import annotations

from types import SimpleNamespace

from evals.helpers import ScriptedClient, make_knowme, response, text_block
from knowme.db import connect
from knowme.runtime import snip_compact
from knowme.runtime import state_summary as ss

SUMMARY = "Facts: the user wants X. Decided SQLite because single-user local."


def _history(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
            for i in range(n)]


def _client(reply: str = SUMMARY) -> ScriptedClient:
    return ScriptedClient([response([text_block(reply)])])


class _Boom:
    """A client that fails the way a network client fails."""

    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        raise RuntimeError("upstream 503")


def _run(tmp_path, history, fitted=None, client=None, conn=None, session="s"):
    return ss.summarize(
        history, fitted if fitted is not None else history, tmp_path,
        conn if conn is not None else connect(tmp_path),
        session, client or _client(), "small-model", 4096)


# --------------------------------------------------------- 1. failing open


def test_a_failed_call_leaves_the_conversation_untouched(tmp_path):
    """The most important test here. Replacing working memory with nothing is
    strictly worse than a request that is too big — too big fails loudly and
    costs a turn; no memory fails quietly and loses the thread."""
    history = _history(20)

    assert _run(tmp_path, history, client=_Boom()) is None
    assert not (tmp_path / "archives").exists(), "archived a conversation it failed to summarise"


def test_an_empty_summary_is_a_failure_not_a_replacement(tmp_path):
    """A model that returns nothing must not wipe the conversation to a blank
    marker. Treating "" as a valid summary is a one-line path to losing
    everything."""
    assert _run(tmp_path, _history(20), client=_client("")) is None


# ------------------------------------------------------------- 2. the shape


def test_the_replacement_is_one_message_and_the_list_can_still_start(tmp_path):
    """A message list has to open with `user`. Replace history with anything else
    and every following request is invalid."""
    out = _run(tmp_path, _history(20))

    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert out[0]["content"].startswith("[Compacted]")


def test_the_marker_names_where_the_transcript_went(tmp_path):
    out = _run(tmp_path, _history(20))

    archive = [p for p in (tmp_path / "archives").iterdir()][0]
    assert str(archive) in out[0]["content"], "the marker does not say where it went"
    assert SUMMARY in out[0]["content"]


def test_the_COMPLETE_history_is_archived_not_the_fitted_copy(tmp_path):
    """The fitted copy is what the summariser reads; the archive is what a human
    reads. Archiving the degraded one would destroy the record exactly when it
    matters most."""
    history = [{"role": "user", "content": "the original question"},
               {"role": "assistant", "content": "the full tool output " + "x" * 5000}]
    fitted = [{"role": "user", "content": "the original question"},
              {"role": "assistant", "content": "the full tool output [full: tool_results/a.txt]"}]

    _run(tmp_path, history, fitted=fitted)

    import json
    archive = [p for p in (tmp_path / "archives").iterdir()][0]
    stored = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert stored == history, "the archive holds the fitted copy, not the record"
    assert "x" * 5000 in archive.read_text(encoding="utf-8")


def test_the_summariser_reads_the_fitted_copy(tmp_path):
    """So the summary carries the tool pointers forward — the tool outputs stay
    retrievable through read_tool_result even though the conversation that
    mentioned them is gone."""
    seen = {}

    class Spy(ScriptedClient):
        def _create(self, **kwargs):
            seen["prompt"] = kwargs["messages"][0]["content"]
            return self._script.pop(0)

    _run(tmp_path, _history(4),
         fitted=[{"role": "user", "content": "[context] hi"},
                 {"role": "assistant", "content": "ok [full: tool_results/abc.txt]"}],
         client=Spy([response([text_block(SUMMARY)])]))

    assert "tool_results/abc.txt" in seen["prompt"], "the pointer was not carried in"


# ------------------------------------------------------------ 3. the prompt


def test_the_prompt_demands_what_summarising_normally_destroys(tmp_path):
    """The prompt IS the safety margin, so it is asserted directly.

    A generic "summarise this conversation" passes every other test in this file
    while silently dropping the four things the repo's own context guide names
    as the casualties of exactly this design: verbatim identifiers, decision
    reasons, failed attempts, open threads. A truncated identifier reads fine to
    a human and breaks the next tool call."""
    prompt = ss.SUMMARY_PROMPT.lower()

    assert "verbatim" in prompt and "never abbreviated" in prompt
    assert "with the reason" in prompt
    assert "failed" in prompt and "try the same thing again" in prompt
    assert "not finished" in prompt
    assert "do not narrate" in prompt


def test_the_prompt_asks_for_the_conversations_own_language():
    """A summary of a Chinese conversation written in English loses the user's
    own words for things, which is most of what makes it usable."""
    assert "language the conversation is in" in ss.SUMMARY_PROMPT


# --------------------------------------------------------- 4. round-tripping


def test_reopening_a_session_yields_the_summary_plus_what_came_after(tmp_path):
    """`covered` is a COUNT, not an index — the lesson snip_compact learned the
    hard way. An index into one list is wrong the moment it is read against
    another, and the error compounds."""
    conn = connect(tmp_path)
    history = _history(20)
    out = _run(tmp_path, history, conn=conn)

    grows = history + [{"role": "user", "content": "after"},
                       {"role": "assistant", "content": "also after"}]
    rebuilt = ss.rebuild(grows, tmp_path, conn, "s")

    assert rebuilt[0]["content"] == out[0]["content"]
    assert [m["content"] for m in rebuilt[1:]] == ["after", "also after"]


def test_a_session_never_summarised_rebuilds_whole(tmp_path):
    history = _history(20)
    assert ss.rebuild(history, tmp_path, connect(tmp_path), "fresh") == history


HEAD, TAIL = 4, 4


def test_the_count_is_in_the_coordinate_system_rebuild_slices(tmp_path):
    """The summary must account for the WHOLE conversation, even when the copy it
    was made from had already been snipped.

    `summarize` is handed `session.history`, and snip_compact has lifted the
    middle out of that — leaving `head + 1 marker + tail`. Recording that length
    as `covered` puts a working-memory count against a full-log slice, so a
    summary of a 20-message conversation claimed to cover 9 and a reopened
    session came back as the summary PLUS the eleven messages it already
    covered. A count is not coordinate-free just because it is not an index.
    """
    conn = connect(tmp_path)
    full = _history(20)
    snipped = snip_compact.snip(full, tmp_path, conn, "s", HEAD, TAIL)
    assert len(snipped) == HEAD + 1 + TAIL        # the premise: this really was snipped
    assert snip_compact.archived_count(conn, "s") == 12

    out = _run(tmp_path, snipped, fitted=snipped, conn=conn)

    covered = conn.execute("SELECT covered FROM history_summaries WHERE session_id = 's'"
                           ).fetchone()["covered"]
    assert covered == len(full), "covered is a count in the snipped list, not the log"

    # Nothing arrived after the summary, so the reopened session is just the summary.
    rebuilt = ss.rebuild(full, tmp_path, conn, "s")
    assert len(rebuilt) == 1
    assert rebuilt[0]["content"] == out[0]["content"]


def test_what_arrived_after_a_summary_is_kept_even_when_snipped(tmp_path):
    """Both halves at once: the snipped middle goes, the new messages stay —
    in that order, and only once each."""
    conn = connect(tmp_path)
    full = _history(20)
    snipped = snip_compact.snip(full, tmp_path, conn, "s", HEAD, TAIL)
    _run(tmp_path, snipped, fitted=snipped, conn=conn)

    grows = full + [{"role": "user", "content": "after"},
                    {"role": "assistant", "content": "also after"}]
    rebuilt = ss.rebuild(grows, tmp_path, conn, "s")

    assert rebuilt[0]["content"].startswith("[Compacted]")
    assert [m["content"] for m in rebuilt[1:]] == ["after", "also after"]


# ------------------------------------------- 5. the round trip, through a Session


def test_a_snipped_summarised_session_comes_back_the_same(tmp_path):
    """The whole thing end to end, at the level the user meets it: a real
    Session that was SNIPPED, then summarised, then kept going long enough to be
    snipped AGAIN, then switched away from and back.

    This is the case both earlier failures lived in. The summary is written from
    a list snip_compact has already taken the middle out of, and it then becomes
    the conversation's new opening — so both mechanisms have to agree about
    where the conversation starts. Split across two bugs:

      * the COUNT (state_summary covered) was taken in the snipped list;
      * the WATERMARK (snip_compact archived) kept counting from a conversation
        that no longer existed, so the reopened list was sliced at a pre-summary
        offset. Fixing only the first made this worse, not better: measured, the
        session came back ten messages SHORTER than the live one, newest gone.

    Asserting the full list, not the length — the earlier bug kept the length
    right and swapped one message for a stale one.
    """
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    session = app.session
    session.start_new("t")
    for i in range(30):
        session.add_exchange(f"问题 {i}", f"回答 {i}")
    assert snip_compact.archived_count(app.conn, "t") > 0, "premise: never snipped"

    # Exactly what runtime.run_turn does: summarise session.history, then adopt
    # the persisted result as the new working memory.
    session.history = ss.summarize(
        session.history, session.history, app.settings.home, app.conn, "t",
        _client(), "small-model", 4096)

    for i in range(30, 60):
        session.add_exchange(f"问题 {i}", f"回答 {i}")
    live = list(session.history)
    assert len(live) == 50 and snip_compact.archived_count(app.conn, "t") > 0, \
        "premise: the second batch must have been snipped too"

    session.switch("somewhere else")
    session.switch("t")

    assert session.history == live

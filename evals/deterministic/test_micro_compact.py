"""DETERMINISTIC EVAL — micro_compact is the emergency valve, and only that.

Three things have to hold, in order of how much they matter:

  1. IT IS REVERSIBLE. Everything it replaces is written to disk first, and the
     pointer it leaves will actually open. This is the only compressor of the
     three that keeps NO excerpt, so it is the one where a broken pointer would
     be unrecoverable.
  2. IT DOES NOTHING UNTIL IT IS NEEDED. A turn nowhere near the window must be
     handed back the same list object.
  3. THE MOST RECENT RESULTS SURVIVE. The model is almost certainly still
     working from those, and "the last three" counts across message boundaries,
     not three per message.
"""

from __future__ import annotations

from pathlib import Path

from knowme.ops.pricing import MODEL_CONTEXT, context_for, remember_context
from knowme.runtime import micro_compact as mc
from knowme.runtime import tool_budget
from knowme.tools.tool_results import make_tool

KEEP, MIN = 3, 120


def _assistant(reply: str, entries: list[str]) -> dict:
    return {"role": "assistant", "content": mc.render(reply, entries)}


def _history(*specs: tuple[str, list[int]]) -> list[dict]:
    """specs: (reply, [entry output lengths]) — one message per spec."""
    out = [{"role": "user", "content": "q"}]
    for reply, lengths in specs:
        out.append(_assistant(reply, [f"tool{i}({{}}) -> {'x' * n}"
                                      for i, n in enumerate(lengths)]))
        out.append({"role": "user", "content": "next"})
    return out


# ---------------------------------------------------------------- the format


def test_render_and_parse_are_inverses():
    """The format is owned by one module precisely so this cannot drift: an
    earlier `; `-joined line could not be split, because tool output contains
    both semicolons and newlines."""
    entries = ["search_web({'q': 'x'}) -> first\nsecond", "create_event({}) -> done"]

    record = mc.render("Booked it.", entries)

    assert mc.entries_of(record) == entries


def test_a_reply_with_no_tools_has_no_block():
    assert mc.entries_of("just talking") is None
    assert mc.render("just talking", []) == "just talking"


# ------------------------------------------------------------- the estimate


def test_the_estimate_is_biased_high_not_low():
    """It feeds a safety valve. Over-estimating trips it slightly early, which
    costs a few tokens; under-estimating trips it late, which costs the request.

    A single chars-per-token divisor cannot be right for both English and CJK —
    and this harness gets both — so the two are counted differently."""
    english = "a" * 400          # ~100 tokens
    chinese = "你" * 400     # ~400 tokens, not ~100

    assert mc.estimate_tokens(english) == 100
    assert mc.estimate_tokens(chinese) == 400
    assert mc.estimate_tokens("") == 0


# ------------------------------------------------------------ the behaviour


def test_a_turn_nowhere_near_the_window_is_untouched(tmp_path):
    history = _history(("a", [5000]), ("b", [5000]))

    assert mc.compact(history, tmp_path, KEEP, MIN) is history
    assert not (tmp_path / tool_budget.STORED_DIR).exists(), "wrote for no reason"


def test_the_most_recent_results_survive(tmp_path):
    """Spread across TWO messages, to pin that the count runs from the end of
    the conversation rather than restarting at each message."""
    history = _history(("old", [400, 400]), ("new", [400, 400]))

    out = mc.compact(history, tmp_path, KEEP, MIN)

    # four entries; keeping the last three protects BOTH of the newer message's
    # and only the second of the older one
    newest = out[-2]["content"]
    oldest = out[1]["content"]
    assert newest.count("read_tool_result") == 0, "a recent result was compacted"
    assert oldest.count("read_tool_result") == 1, "the wrong number were compacted"
    assert "x" * 400 in newest, "a recent result lost its text"


def test_a_short_result_is_left_alone_however_old(tmp_path):
    """The 120-character floor: replacing a short result would cost as much in
    pointer text as it saves."""
    history = _history(("old", [50, 50]), ("new", [400, 400, 400]))

    out = mc.compact(history, tmp_path, KEEP, MIN)

    assert out == history, "a result under the floor was compacted"


def test_a_pointer_is_never_wrapped_in_another_pointer(tmp_path):
    """Idempotence under a low floor.

    A pointer is about 80 characters, comfortably under the 120 default — so
    today the min_chars floor happens to protect it. That is two unrelated
    numbers agreeing by luck, and a user lowering KNOWME_MICRO_MIN_CHARS would
    turn the conversation into pointers to pointers, each one losing a little
    more of the trail. Run twice with the floor switched off entirely."""
    history = _history(("old", [900]), ("new", [400, 400, 400]))

    once = mc.compact(history, tmp_path, KEEP, min_chars=0)
    twice = mc.compact(once, tmp_path, KEEP, min_chars=0)

    assert twice == once, "a second pass nested one pointer inside another"
    line = [e for e in mc.entries_of(twice[1]["content"]) if "read_tool_result" in e][0]
    assert line.count("read_tool_result") == 1


def test_every_replacement_is_reversible(tmp_path):
    """THE test. This is the only compressor that keeps no excerpt, so a pointer
    that does not open would lose the content outright."""
    history = _history(("old", [900]), ("new", [400, 400, 400]))
    original = "x" * 900

    out = mc.compact(history, tmp_path, KEEP, MIN)

    line = [e for e in mc.entries_of(out[1]["content"]) if "read_tool_result" in e][0]
    stored_id = line.split(tool_budget.STORED_DIR + "/")[1].split()[0]
    assert make_tool(tmp_path).fn(id=stored_id) == original, "the pointer did not open"


def test_the_pointer_says_where_before_it_is_written(tmp_path):
    """Same ordering rule as tool_budget: the file exists by the time the text
    naming it does."""
    history = _history(("old", [900]), ("new", [400, 400, 400]))

    out = mc.compact(history, tmp_path, KEEP, MIN)

    assert list((tmp_path / tool_budget.STORED_DIR).iterdir()), "nothing was stored"
    assert tool_budget.STORED_DIR in out[1]["content"]


def test_the_reply_text_is_preserved(tmp_path):
    """Only the tool block is rewritten. Losing the assistant's own words would
    silently change what the model believes it already said."""
    history = _history(("I booked the court for 9am.", [900]),
                       ("new", [400, 400, 400]))

    out = mc.compact(history, tmp_path, KEEP, MIN)

    assert out[1]["content"].startswith("I booked the court for 9am.")


# ------------------------------------------------------- the context window


def test_the_window_resolves_from_most_specific_source_first(tmp_path):
    """Live catalog, then the table, then the env override, then a conservative
    default. Guessing LOW compacts early; guessing HIGH risks a request that
    does not fit — so the fallback is deliberately small."""
    assert context_for("deepseek", "deepseek-v4-flash") == MODEL_CONTEXT["deepseek-v4-flash"]
    assert context_for("deepseek", "some-model-from-next-year") == 128_000

    remember_context("some-model-from-next-year", 500_000)
    assert context_for("deepseek", "some-model-from-next-year") == 500_000


def test_an_explicit_env_override_is_honoured(monkeypatch):
    monkeypatch.setenv("KNOWME_MODEL_CONTEXT", "64000")
    assert context_for("xai", "unlisted-model") == 64_000

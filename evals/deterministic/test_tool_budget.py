"""DETERMINISTIC EVAL — long tool output is compressed ON THE WAY TO THE MODEL.

The rules, in the order they matter:

  0. THE STORED CONVERSATION IS COMPLETE. Compression rewrites a copy on its way
     out; chat_log and session.history keep every character the tool returned.
     This is first because getting it wrong is the worst outcome — an excerpt in
     the conversation the user reads, with the real output existing only in a
     file they were never told about.
  1. a turn whose tool output already fits is returned BYTE-FOR-BYTE unchanged
  2. compression triggers on the TURN's total, and shrinks largest-first, so the
     cost lands where the size came from
  3. results already under the cap are never touched, however big the turn is
  4. the pointer leads, the elision is explicit, and the whole thing is bounded
     by the cap — so it can never be longer than what it replaced
  5. reading it back returns exactly what was stored
"""

from __future__ import annotations

from knowme.runtime.session import Session

from knowme.config import Settings
from knowme.runtime import tool_budget
from knowme.runtime import tool_entries as te
from knowme.tools.tool_results import make_tool

BUDGET = 4000
CAP = 1200


def _entries(*outputs: str) -> list[str]:
    return [te.entry(f"tool{i}", {}, o) for i, o in enumerate(outputs)]


def _outputs(entries: list[str]) -> list[str]:
    return [te.split_entry(e)[1] for e in entries]


# ------------------------------------------- 0. the stored conversation


def test_the_stored_conversation_keeps_the_full_tool_output(tmp_path):
    """THE test for the rule that matters most.

    Tool output used to be compressed inside add_exchange, so chat_log — which is
    what the dashboard renders and what the user reads back — held the excerpt,
    and the 7,400-character original existed only in .knowme/tool_results/. The
    person reading their own conversation is entitled to the whole thing.
    """
    settings = Settings()
    settings.home = tmp_path
    settings.ensure_home()
    session = Session(settings, memory=None)
    huge = "R" * 8000

    session.add_exchange("find the matches", "Booked them.",
                         tool_calls=[{"tool": "search_web", "args": {}, "output": huge}])

    stored = session.history[1]["content"]
    assert huge in stored, "the stored conversation holds an excerpt, not the output"
    assert "read_tool_result" not in stored, "a pointer reached the stored conversation"


def test_fitting_does_not_mutate_the_stored_history(tmp_path):
    """fit_history rewrites a copy. Anything else would mean the compression the
    model was handed leaks back into the record the user reads."""
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": te.render("ok", _entries("x" * 6000))},
    ]
    before = [m["content"] for m in history]

    fitted = tool_budget.fit_history(history, tmp_path, BUDGET, CAP)

    assert fitted is not history
    assert [m["content"] for m in history] == before, "the original history was mutated"
    assert "x" * 6000 in history[1]["content"], "the stored text lost the output"


# ------------------------------------------------------- 1. the no-op case


def test_a_turn_that_already_fits_is_untouched(tmp_path):
    entries = _entries("Event created: 'Swim' 2026-09-20T17:00.",
                       "Saved to memory under 'raj': prefers evenings")

    assert tool_budget.fit_entries(entries, tmp_path, BUDGET, CAP) is None
    assert not (tmp_path / tool_budget.STORED_DIR).exists(), "wrote for no reason"


def test_a_conversation_with_nothing_to_fit_is_untouched(tmp_path):
    history = [{"role": "user", "content": "q"},
               {"role": "assistant", "content": "just talking"}]

    assert tool_budget.fit_history(history, tmp_path, BUDGET, CAP) is history


# ------------------------------------------------- 2. what gets compressed


def test_only_the_largest_results_are_compressed(tmp_path):
    fitted = tool_budget.fit_entries(
        _entries("H" * 6000, "E" * 300, "S" * 100), tmp_path, BUDGET, CAP)

    assert fitted is not None
    assert _outputs(fitted)[0] != "H" * 6000, "the huge result was not compressed"
    assert _outputs(fitted)[1] == "E" * 300, "a short result was touched"
    assert _outputs(fitted)[2] == "S" * 100, "a short result was touched"


def test_a_result_under_the_cap_is_never_a_candidate(tmp_path):
    """Over budget, but every result already fits the cap: nothing is
    compressible, and chasing the number must not shrink them anyway."""
    entries = _entries(*["A" * CAP] * 4)

    assert tool_budget.fit_entries(entries, tmp_path, BUDGET, CAP) is None


# ------------------------------------------------------ 3. the replacement


def test_a_replacement_is_never_longer_than_what_it_replaces(tmp_path):
    """The property that makes the cap safe to raise.

    The cap bounds the WHOLE replacement — pointer, excerpt and marker — so for
    any output longer than the cap the result is strictly smaller. Structural,
    not a runtime check: raising a number in .env must not be able to grow the
    conversation.

    Before the cap meant the whole replacement it meant only the excerpt, so
    every digest came out at cap + ~145. Measured at cap 2400 on ~2530-character
    results, the World Cup turn came out 1% LARGER than no compression at all,
    while reporting four successful compressions.
    """
    for cap in (200, 400, 1200, 2400):
        for size in (cap + 1, cap + 50, cap * 2, cap * 10):
            fitted = tool_budget.fit_entries(_entries("Q" * size), tmp_path, 1, cap)
            assert fitted is not None, f"{size} over cap {cap} was left alone"
            got = _outputs(fitted)[0]
            assert len(got) <= cap, f"replacement is {len(got)}, over its own {cap}"
            assert len(got) < size, "compressing made the result longer"


def test_the_pointer_comes_before_the_excerpt(tmp_path):
    """Order is load-bearing: if the excerpt led, the model would read it as the
    whole result and answer from a truncated view."""
    fitted = tool_budget.fit_entries(_entries("LINE ONE\n" + "x" * 5000), tmp_path, 1, CAP)

    got = _outputs(fitted)[0]
    assert got.index("read_tool_result") < got.index("LINE ONE")
    assert "…[+" in got and "chars]" in got, "the elision was not marked"


# ------------------------------------------------------------ 4. the disk


def test_every_replacement_is_reversible(tmp_path):
    original = "SEARCH RESULTS\n" + "\n".join(f"{i}. result {i}" for i in range(500))
    fitted = tool_budget.fit_entries(_entries(original), tmp_path, 1, CAP)

    stored_id = _outputs(fitted)[0].split(tool_budget.STORED_DIR + "/")[1].split()[0]
    assert make_tool(tmp_path).fn(id=stored_id) == original


def test_the_same_output_always_lands_in_the_same_file(tmp_path):
    """Content-addressed, because this runs on EVERY turn now. Timestamped names
    meant the same result compressed on ten turns left ten byte-identical copies
    of itself in tool_results/."""
    output = "Z" * 5000
    first = tool_budget.fit_entries(_entries(output), tmp_path, 1, CAP)
    second = tool_budget.fit_entries(_entries(output), tmp_path, 1, CAP)

    assert _outputs(first)[0] == _outputs(second)[0]
    assert len(list((tmp_path / tool_budget.STORED_DIR).iterdir())) == 1


def test_the_full_text_is_written_before_the_pointer_names_it(tmp_path):
    original = "Z" * 9000
    fitted = tool_budget.fit_entries(_entries(original), tmp_path, 1, CAP)

    stored = list((tmp_path / tool_budget.STORED_DIR).iterdir())
    assert len(stored) == 1
    assert stored[0].read_text(encoding="utf-8") == original
    assert original[:200] in _outputs(fitted)[0]


# ------------------------------------------------------- 5. through the app


def test_the_reply_survives_the_fitting(tmp_path):
    history = [{"role": "assistant",
                "content": te.render("I booked the court.", _entries("x" * 6000))}]

    fitted = tool_budget.fit_history(history, tmp_path, BUDGET, CAP)

    assert fitted[0]["content"].startswith("I booked the court.")

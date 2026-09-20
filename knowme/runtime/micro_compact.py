"""micro_compact — the third compressor, and the only emergency one.

tool_budget bounds one turn's tool output; snip_compact bounds how many messages
the conversation carries. Both work on their own terms, at their own thresholds.
This one runs LAST, measures the assembled prompt against the model's actual
context window, and only moves when the request is genuinely close to not
fitting — past `trigger` of the window (default 80%).

What it does when it moves:
  * the most recent `keep` tool results are left completely alone — the model is
    almost certainly still working from those
  * every EARLIER tool result longer than `min_chars` characters is written to
    .knowme/tool_results/ and replaced in the conversation by its path, with no
    excerpt at all

That last point is the difference from tool_budget, and it is deliberate.
tool_budget keeps a pointer plus the first N characters because it is optimising
a normal turn. This one is the valve you open when the alternative is the request
failing, so it keeps the pointer and nothing else. It is strictly more
destructive, it only runs under pressure, and the full text is on disk either way.

Like tool_budget, this rewrites a COPY of the conversation on its way to the
model — never the stored one. chat_log holds the complete record, because the
person reading the dashboard is entitled to the whole tool output, not the
excerpt the model was given.
"""

from __future__ import annotations

from pathlib import Path

from knowme.runtime import tool_budget
from knowme.runtime import tool_entries as te

# The history format is owned by tool_entries — tool_budget reads it too, and
# owning it inside one of its two readers is how the two drift apart. Re-exported
# here so callers and tests that reach for micro_compact.render keep working.
BLOCK_HEADER = te.BLOCK_HEADER
ENTRY_PREFIX = te.ENTRY_PREFIX
ARROW = te.ARROW
entry = te.entry
render = te.render
entries_of = te.entries_of


def estimate_tokens(text: str) -> int:
    """Rough token count, deliberately biased HIGH.

    English runs about 4 characters per token and CJK closer to 1, so no single
    divisor is right for both — and this harness gets both. Counting non-ASCII
    characters as a token each and everything else at 4-per-token over-estimates
    English slightly and lands CJK about right.

    Biased high on purpose: this feeds a safety valve, and over-estimating trips
    it a little early while under-estimating trips it too late. Early costs a few
    tokens; late costs a failed request.
    """
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if c.isascii())
    return ascii_chars // 4 + (len(text) - ascii_chars)


def prompt_tokens(system: str, messages: list[dict]) -> int:
    """The whole request, estimated: the system prompt plus every message."""
    total = estimate_tokens(system)
    for message in messages:
        content = message.get("content")
        total += estimate_tokens(content if isinstance(content, str) else str(content))
    return total


def _protected(history: list[dict], keep: int) -> set[tuple[int, int]]:
    """The (message, entry) positions of the `keep` most recent tool results.

    Counted from the END, and across message boundaries: "the last three" means
    the last three tool results wherever they sit, not three per message.
    """
    protected: set[tuple[int, int]] = set()
    remaining = keep
    for i in reversed(range(len(history))):
        found = te.entries_of(str(history[i].get("content", "")))
        if not found:
            continue
        take = min(remaining, len(found))
        for j in range(len(found) - take, len(found)):
            protected.add((i, j))
        remaining -= take
        if remaining <= 0:
            break
    return protected


def compact(history: list[dict], home: Path, keep: int, min_chars: int) -> list[dict]:
    """Replace older, longer tool results with a pointer to their full text.

    Returns the SAME list when there is nothing to do, so a turn that is nowhere
    near the window pays nothing for this.
    """
    protected = _protected(history, keep)
    if not protected:
        return history

    changed = False
    out = list(history)
    for i in range(len(out)):
        content = str(out[i].get("content", ""))
        found = te.entries_of(content)
        if not found:
            continue
        rebuilt, touched = [], False
        for j, original in enumerate(found):
            prefix, output = te.split_entry(original)
            # Already a pointer: never wrap one in another. It happens to be
            # skipped by the min_chars floor today (a pointer is ~80 characters,
            # under the 120 default), but relying on two unrelated numbers
            # staying in that order is how a lower KNOWME_MICRO_MIN_CHARS turns
            # the conversation into pointers to pointers.
            if ((i, j) in protected or not prefix or len(output) <= min_chars
                    or te.ALREADY_COMPACTED in output):
                rebuilt.append(original)
                continue
            # Same storage and the same read_tool_result tool as tool_budget, so
            # the model meets one kind of pointer everywhere and has one way back.
            stored_id = tool_budget.new_id(prefix.split("(", 1)[0] or "tool", output)
            tool_budget.write(home, stored_id, output)
            rebuilt.append(prefix + te.pointer(stored_id))
            touched = True
        if touched:
            out[i] = {**out[i], "content": te.render(te.reply_of(content), rebuilt)}
            changed = True

    return out if changed else history

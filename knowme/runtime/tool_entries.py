"""The shape of tool activity inside a history message — one owner, two readers.

An assistant history message reads:

    <reply>
    [tools used]
    - search_web({'query': 'x'}) -> <output>
    - create_event({'title': 'y'}) -> <output>

One entry per line, `- ` at the start of the line. That is deliberate rather than
cosmetic: tool_budget has to shrink individual results and micro_compact has to
count "the 3 most recent", and neither is possible on a `; `-joined single line,
because tool output contains both semicolons and newlines.

This lives in its own module because BOTH of those need to read and rebuild the
format. When session.py built it and micro_compact parsed it, the two could
drift; when tool_budget needed it too, they would have had to import each other.

THE ONE EDGE CASE, and why it is benign: a tool output containing a line that
begins with exactly `- ` gets split in two. Both halves are then handled
independently, and both are written to disk before anything replaces them, so the
worst outcome is compacting slightly more than intended — never a lost result, and
never a pointer to a file that was not written.
"""

from __future__ import annotations

BLOCK_HEADER = "[tools used]"
ENTRY_PREFIX = "- "
ARROW = " -> "
# What a replaced result looks like. tool_budget and micro_compact both write
# this exact shape, so the model sees one kind of pointer everywhere and has one
# way back (read_tool_result).
POINTER_OPEN = "[full: "
ALREADY_COMPACTED = POINTER_OPEN + "tool_results/"


def entry(tool: str, args: object, output: str) -> str:
    """One tool call as a history line."""
    return f"{tool}({args}){ARROW}{output}"


def render(reply: str, entries: list[str]) -> str:
    """The assistant's history message: the reply, then one line per tool call."""
    if not entries:
        return reply
    body = "\n".join(f"{ENTRY_PREFIX}{e}" for e in entries)
    return f"{reply}\n{BLOCK_HEADER}\n{body}"


def entries_of(record: str) -> list[str] | None:
    """The individual tool entries of an assistant message, or None if it has no
    tool block.

    The first entry's prefix has to be stripped by hand: split() removes the
    separator only from the elements that FOLLOW one, so the leading `- ` stayed
    on entry zero. Re-rendering then doubled it and the round trip stopped being
    an inverse — test_render_and_parse_are_inverses is what caught that.
    """
    _, sep, block = record.partition("\n" + BLOCK_HEADER + "\n")
    if not sep or not block.startswith(ENTRY_PREFIX):
        return None
    return block[len(ENTRY_PREFIX):].split("\n" + ENTRY_PREFIX)


def reply_of(record: str) -> str:
    """The assistant's own words, with the tool block stripped off."""
    head, _, _ = record.partition("\n" + BLOCK_HEADER + "\n")
    return head


def split_entry(entry_text: str) -> tuple[str, str]:
    """(everything up to and including the arrow, the output after it).

    Splits on the FIRST arrow: the tool name and its arguments come before it, so
    that is the one that separates them. An argument value containing a literal
    " -> " would split inside the arguments instead — unlikely enough to accept,
    and the failure is a shorter-than-intended pointer, not a broken one.
    """
    prefix, arrow, output = entry_text.partition(ARROW)
    return (prefix + arrow if arrow else "", output)


def pointer(stored_id: str) -> str:
    """What replaces an output that has been written to disk."""
    return f"{ALREADY_COMPACTED}{stored_id} — read_tool_result]"

"""Tool-result budget — keep a turn's tool output from being re-sent forever.

A tool result is paid for twice. Once while the model reasons over it inside the
turn — that is the price of thinking, and this module leaves it alone. Then again
on every later turn, because the tool block sits in the history and gets re-sent
each time. The second cost is pure repeat billing, and it is the one removed here.

The contract is delegate_task's, generalised. That tool has always returned
`summary[-500:]` plus a path to the full log in the outbox, and it works: the
model gets the gist, and the whole thing is one read away. Same shape here, for
any tool result too big to keep.

TWO NUMBERS, TWO JOBS — do not collapse them:

  budget  should this TURN be compressed at all? A turn whose tool output
          already fits is returned byte-for-byte unchanged.
  cap     how small does a compressed result get? It bounds the WHOLE
          replacement — pointer, excerpt and marker together — so a compressed
          result is never longer than the cap. Anything already at or under the
          cap is skipped, which is what keeps short results (create_event,
          save_note) exactly as they were.

APPLIED ON THE WAY TO THE MODEL, NOT ON THE WAY TO DISK. This module used to run
inside add_exchange, which meant its output went into chat_log as well — so the
conversation the USER reads was the compressed one, and the 7,000-character tool
output they had every right to see existed only in a file. Compression is a
prompt-assembly concern: history and chat_log hold the complete record, and this
rewrites a copy of it just before the request goes out.

That also makes the disk ids content-addressed rather than timestamped. The same
result compressed on ten different turns must land in the same file, or every
turn would litter tool_results/ with a byte-identical copy.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from knowme.runtime import tool_entries as te

STORED_DIR = "tool_results"

# Reserved room for the widest elision marker we can realistically print
# ("…[+1,234,567 chars]" is 18). Reserved rather than measured so the excerpt
# length can be decided BEFORE the count it would have to be measured from.
_MARKER_ROOM = 30


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:40] or "tool"


def new_id(tool: str, output: str) -> str:
    """The name this output WILL get — derived from its content, so compressing
    the same result twice is the same file rather than a second copy."""
    digest = hashlib.sha1(output.encode("utf-8")).hexdigest()[:12]
    return f"{_slug(tool)}-{digest}.txt"


def write(home: Path, stored_id: str, output: str) -> None:
    """Write one full tool result under .knowme/tool_results/. Idempotent."""
    path = home / STORED_DIR / stored_id
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output, encoding="utf-8")


def _digest_prefix(full: str, stored_id: str, cap: int) -> str | None:
    """`pointer + excerpt + elision marker`, bounded by cap. None if the cap
    cannot fit a pointer and a character of excerpt."""
    pointer = te.pointer(stored_id)
    room = cap - len(pointer) - _MARKER_ROOM - 2      # 2 newlines
    if room < 1:
        return None
    excerpt = full[:room]
    return f"{pointer}\n{excerpt}\n…[+{len(full) - room:,} chars]"


def fit_entries(entries: list[str], home: Path, budget: int, cap: int) -> list[str] | None:
    """Rewrite ONE TURN's entries so their total fits `budget`.

    Returns None when nothing was worth changing — the overwhelmingly common
    case, so an ordinary turn costs a length sum and nothing else.

    Largest first, stopping the moment the turn fits: the cost lands on whichever
    results actually caused the size, and short ones keep their exact text.
    """
    outputs = [te.split_entry(e)[1] for e in entries]
    total = sum(len(o) for o in outputs)
    if total <= budget:
        return None

    result = list(entries)
    changed = False
    for i in sorted(range(len(outputs)), key=lambda i: len(outputs[i]), reverse=True):
        if total <= budget:
            break
        # Already fits the cap, so its replacement could not be smaller — this is
        # what keeps create_event and save_note byte-for-byte intact however
        # oversized the rest of the turn is.
        if len(outputs[i]) <= cap:
            continue
        prefix, _ = te.split_entry(entries[i])
        stored_id = new_id(prefix.split("(", 1)[0], outputs[i])
        digest = _digest_prefix(outputs[i], stored_id, cap)
        # digest is bounded by cap and we got here only because the output
        # exceeds cap, so it is always strictly shorter. _digest_prefix returns
        # None only when cap is too small to hold a pointer at all.
        if digest is None:
            continue
        write(home, stored_id, outputs[i])
        total -= len(outputs[i]) - len(digest)
        result[i] = prefix + digest
        changed = True
    return result if changed else None


def fit_history(history: list[dict], home: Path, budget: int, cap: int) -> list[dict]:
    """The budget applied across a whole conversation, one turn at a time.

    Returns the SAME list when no turn needed anything, so a conversation that
    never produced a large tool result pays nothing.
    """
    out: list[dict] | None = None
    for i, message in enumerate(history):
        content = str(message.get("content", ""))
        found = te.entries_of(content)
        if not found:
            continue
        fitted = fit_entries(found, home, budget, cap)
        if fitted is None:
            continue
        if out is None:
            out = list(history)
        out[i] = {**message, "content": te.render(te.reply_of(content), fitted)}
    return out if out is not None else history

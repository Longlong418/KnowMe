"""snip_compact — the second compressor: bound the CONVERSATION, not one turn.

runtime/tool_budget.py caps how much any single turn's tool output may weigh.
This caps how many messages the conversation may carry — and it does it by
ARCHIVING rather than dropping. The messages it removes are appended to
.knowme/archives/<session>.jsonl first, and the slot they leave behind carries a
marker saying how many went and where they went.

That is the entire difference from the sliding window it replaces. `history[-24:]`
kept the last 24 messages and silently lost everything before them, so the model
had no way to know anything was missing — it answered as though the conversation
had simply started late. Here the model is told.

SHAPE. Once over the trigger, the conversation becomes:

    first HEAD messages    what this conversation is about
    ONE marker message     how many were archived, and where
    last TAIL messages     what is actually going on

HEAD + 1 + TAIL is the trigger, so a snip lands the conversation exactly AT the
threshold and one snip buys TAIL more messages of room. The trigger is derived
rather than configured: three knobs that must agree is a way to be wrong.

NO TOOL-PAIRING HAZARD — and a test that keeps it that way. Cutting between an
assistant `tool_use` and the `user` `tool_result` that answers it produces an
invalid request, so a snip like this normally HAS to protect that pairing. It
does not have to here, because session.history never holds content blocks:
add_exchange folds tool activity into a text "[tools used: ...]" line, so every
entry is a plain string and any cut point is structurally safe.
test_history_holds_no_content_blocks pins that assumption, so if history ever
starts carrying real blocks, someone is told instead of finding out from a 400.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ARCHIVES_DIR = "archives"

# The marker is identified by its own text, not by a dict key: the harness sends
# these dicts straight to the provider, and an extra key is an invalid request.
MARKER_START = "[archived:"


def _slug(session_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in session_id)[:60] or "default"


def archive_path(home: Path, session_id: str) -> Path:
    return home / ARCHIVES_DIR / f"{_slug(session_id)}.jsonl"


def is_marker(message: dict) -> bool:
    return str(message.get("content", "")).startswith(MARKER_START)


def plan(length: int, start: int, tail: int, at: int) -> tuple[int, int] | None:
    """The half-open [start, cut_to) to remove, or None when nothing is due.

    `at` is the trigger — head + 1 + tail, the size the conversation is allowed
    to reach. Checking the SAME number here as the caller advertises is what
    keeps "over 50" from meaning "at 49": comparing against head+tail instead
    made the conversation snip one message early, forever.
    """
    if length <= at:
        return None
    cut_to = length - tail
    if cut_to <= start:
        return None
    return start, cut_to


def marker(removed: int, archive: Path) -> str:
    return (
        f"{MARKER_START} {removed} earlier messages were archived to {archive} — "
        f"the conversation continues below. Nothing was lost; read the file if "
        f"you need what came before.]"
    )


def _append(home: Path, session_id: str, messages: list[dict]) -> Path:
    """Append-only, like usage.jsonl and the traces — one file per session that
    only ever grows, so repeated snips accumulate a complete record instead of
    racing to overwrite each other."""
    path = archive_path(home, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for message in messages:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")
    return path


def archived_count(conn: sqlite3.Connection | None, session_id: str) -> int:
    """How many messages this session has archived in total, 0 if never snipped.

    A COUNT, deliberately, not an index. The first version stored the tail's
    start position, which is an index into the LIVE history — and the live
    history has a marker slot that the full log does not, so the two drift apart
    by one and keep drifting. Rebuilding from that number produced a tail that
    began in the wrong place (measured: a reopened session came back with 68
    messages whose tail started where the live one's had twelve turns earlier).

    A cumulative count has no coordinate system to get wrong: the messages
    missing from a conversation are exactly the first `n` that come after the
    opening, so the tail always begins at head + n.
    """
    if conn is None:
        return 0
    row = conn.execute(
        "SELECT archived FROM history_snips WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row["archived"] if row else 0


def _remember(conn: sqlite3.Connection, session_id: str, total: int, path: Path) -> None:
    conn.execute(
        """INSERT INTO history_snips (session_id, tail_start, archived, archive_path, updated_at)
           VALUES (?, 0, ?, ?, datetime('now'))
           ON CONFLICT(session_id) DO UPDATE SET
               archived = excluded.archived,
               archive_path = excluded.archive_path,
               updated_at = excluded.updated_at""",
        (session_id, total, str(path)),
    )
    conn.commit()


def forget(conn: sqlite3.Connection | None, session_id: str) -> None:
    """Drop this session's watermark — the conversation it described is gone.

    Called when state_summary replaces the conversation with a summary. That
    summary becomes the new opening, so "how many messages are missing" has to
    start counting from it; the old number describes a conversation that no
    longer exists. Leaving it in place makes rebuild() slice a post-summary list
    at a pre-summary offset — measured: a reopened session came back ten messages
    SHORTER than the live one, with the newest of them gone.

    Deleting rather than zeroing, because archived_count() reads "no row" as 0
    and _remember() re-inserts on the next snip.
    """
    if conn is None:
        return
    conn.execute("DELETE FROM history_snips WHERE session_id = ?", (session_id,))
    conn.commit()


def snip(history: list[dict], home: Path, conn: sqlite3.Connection | None,
         session_id: str, head: int, tail: int) -> list[dict]:
    """Bound `history` to head + 1 + tail, appending what it drops to the archive.

    Returns the SAME list when nothing is due, so an ordinary turn does not pay
    for this in allocations or in a rewritten identity.

    A previous snip's marker sits at index `head`, so the second snip starts one
    past it. Without that the marker was itself archived on every later snip and
    the file filled up with stale notices — two of them per snip, forever.
    """
    start = head + 1 if len(history) > head and is_marker(history[head]) else head
    cut = plan(len(history), start, tail, at=head + 1 + tail)
    if cut is None:
        return history
    cut_from, cut_to = cut
    removed = history[cut_from:cut_to]

    path = _append(home, session_id, removed)
    # The marker reports the TOTAL that is missing from the conversation, not
    # this snip's share — "12 earlier messages were archived" is a claim about
    # the conversation, and a per-snip count under-reports it every time after
    # the first.
    total = archived_count(conn, session_id) + len(removed)
    if conn is not None:
        _remember(conn, session_id, total, path)

    return (
        history[:head]
        + [{"role": "assistant", "content": marker(total, path)}]
        + history[cut_to:]
    )


def rebuild(flat: list[dict], home: Path, conn: sqlite3.Connection | None,
            session_id: str, head: int, tail: int) -> list[dict]:
    """The same SHAPE, reconstructed from the full log — used when a session is
    reopened.

    Without this the archived middle would come back: chat_log keeps every row,
    so a naive rebuild hands back the whole conversation and the snip silently
    undoes itself the moment the user switches away and back.

    The tail begins at head + archived, where `archived` counts what is missing
    from the conversation entirely — messages appended since the last snip are
    correctly still present, and the next turn's snip re-bounds the whole thing.
    """
    gone = archived_count(conn, session_id)
    if gone <= 0 or len(flat) <= head + gone:
        return flat
    path = archive_path(home, session_id)
    return (
        flat[:head]
        + [{"role": "assistant", "content": marker(gone, path)}]
        + flat[head + gone:]
    )

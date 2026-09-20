"""state_summary — the last resort: replace the conversation with a summary of itself.

Every other compressor in this harness is REVERSIBLE. tool_budget, snip_compact
and micro_compact all move text out of the prompt and leave a pointer the model
can follow back. This one does not: the conversation is replaced by a passage
somebody else wrote about it. That is a real loss, and it is why it runs dead
last, only after the request has already been shrunk by every cheaper means and
is still over the line.

THE SUMMARY PROMPT IS THE WHOLE SAFETY MARGIN, so it is not "summarise this".
The repo's own context-management guide names four things a summarising
compressor destroys by default, and this prompt demands each of them in turn:

  * IDENTIFIERS, quoted verbatim. `commit a83f029` abbreviated to `a83f...`
    reads fine to a human and breaks the next tool call, because it looks usable
    and is not.
  * DECISION REASONS. "Use SQLite" alone lets a future self re-open a question
    that was already settled; "use SQLite because this is a single-user local
    tool" does not.
  * FAILED ATTEMPTS. A model that cannot see what was tried will try it again.
  * OPEN THREADS. Work started and not finished.

WHAT IS FED TO THE SUMMARISER: the already-fitted copy, not the raw history.
Two reasons. It is guaranteed to fit alongside the instructions, where the raw
history might not — and summarising the fitted copy carries the tool pointers
forward into the summary, so the tool outputs remain retrievable through
read_tool_result even though the conversation that mentioned them is gone.

WHAT IS ARCHIVED: the complete history. The human record is never the degraded
one, which is the same rule chat_log follows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from knowme.runtime import snip_compact

SUMMARY_PROMPT = """\
You are compacting a conversation that has grown too long to keep. Everything
below is about to be discarded from your working memory and replaced by your
summary of it. Assume you will NOT go back and read the full transcript — write
the summary as though it is the only thing you will have.

Produce a STATE SUMMARY that lets you continue the work without re-reading
anything. Cover, in this order:

1. What the user asked for, and what they have accepted or rejected.
2. Facts about the user and their world, stated as facts.
3. Decisions taken, WITH THE REASON. "Use SQLite" is not enough; "use SQLite
   because this is a single-user local tool" is.
4. Things that were tried and FAILED, and why. Record these explicitly — a
   future you that cannot see them will try the same thing again.
5. Open threads: anything started and not finished.
6. Exact identifiers, quoted VERBATIM and never abbreviated: file paths, URLs,
   commit hashes, event ids, dates and times, people's names. A truncated
   identifier is worse than no identifier, because it looks usable and is not.

Rules: do not narrate the conversation, do not include small talk, prefer short
factual lines over prose, and say when something is uncertain rather than
smoothing it over. Write in the language the conversation is in.

--- CONVERSATION ---
"""


def _transcript(history: list[dict]) -> str:
    lines = []
    for message in history:
        content = message.get("content")
        lines.append(f"{message.get('role', '?')}: "
                     f"{content if isinstance(content, str) else str(content)}")
    return "\n\n".join(lines)


def marker(summary: str, archive: Path) -> str:
    """The single message the conversation becomes.

    Role `user`, because a message list has to open with one — and the model
    reads it as context handed to it rather than as something it once said.
    """
    return (f"[Compacted] The earlier conversation was summarised to fit the "
            f"context window. The full transcript is at {archive}\n\n{summary}")


def _archive(home: Path, session_id: str, history: list[dict]) -> Path:
    """One file per compaction event — unlike snip_compact's append-only log,
    because each of these is a whole-conversation snapshot and a reader needs to
    tell one from the next."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = home / snip_compact.ARCHIVES_DIR / f"{snip_compact._slug(session_id)}-compacted-{stamp}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in history),
                    encoding="utf-8")
    return path


def _remember(conn: sqlite3.Connection, session_id: str, covered: int,
              archive: Path, summary: str) -> None:
    conn.execute(
        """INSERT INTO history_summaries
               (session_id, covered, archive_path, summary, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(session_id) DO UPDATE SET
               covered = excluded.covered,
               archive_path = excluded.archive_path,
               summary = excluded.summary,
               updated_at = excluded.updated_at""",
        (session_id, covered, str(archive), summary),
    )
    conn.commit()


def _record(conn: sqlite3.Connection | None, session_id: str) -> sqlite3.Row | None:
    if conn is None:
        return None
    return conn.execute(
        "SELECT covered, archive_path, summary FROM history_summaries WHERE session_id = ?",
        (session_id,),
    ).fetchone()


def summarize(history: list[dict], fitted: list[dict], home: Path,
              conn: sqlite3.Connection | None, session_id: str, client,
              model: str, max_tokens: int) -> list[dict] | None:
    """Replace the conversation with a summary of itself.

    Returns the new (one-message) history, or None when the summarising call
    failed. FAILING OPEN MATTERS MORE HERE THAN ANYWHERE ELSE in this harness:
    history is the only working memory there is, and a failed call must leave it
    exactly as it was rather than half-replaced.
    """
    try:
        response = client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": SUMMARY_PROMPT + _transcript(fitted)}])
        summary = "".join(b.text for b in response.content if b.type == "text").strip()
    except Exception:
        return None
    if not summary:
        return None

    # Archived BEFORE the replacement, and only once the summary exists: a
    # snapshot of a conversation we then failed to summarise is litter.
    archive = _archive(home, session_id, history)
    if conn is not None:
        _remember(conn, session_id, len(history), archive, summary)
    return [{"role": "user", "content": marker(summary, archive)}]


def rebuild(flat: list[dict], home: Path, conn: sqlite3.Connection | None,
            session_id: str) -> list[dict]:
    """The summary plus everything that arrived after it, for a reopened session.

    `covered` is a COUNT, not an index — the same lesson snip_compact learned.
    The number of messages the summary accounts for is coordinate-free; an index
    into one list is wrong the moment it is read against another.
    """
    row = _record(conn, session_id)
    if row is None:
        return flat
    return ([{"role": "user", "content": marker(row["summary"], Path(row["archive_path"]))}]
            + flat[row["covered"]:])

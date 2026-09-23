"""The receipt for every change made inside the project root.

Why this exists: `delegate_task` against a real project returns the CLI's own
sentence ("pi finished the delegated task") plus its stdout tail. That is a
SELF-REPORT — KnowMe has no idea what actually changed on disk, and neither do
you unless you go run `git status` yourself. This module is the other half:
what the working tree looked like when the work started, and one row per
change since.

Two kinds of row share the `coding_runs` table:

    baseline   written ONCE per (session, project root), lazily, before the
               first change of that session. Holds the HEAD commit and the
               `git diff HEAD` of the moment. This is what an "undo this
               session" would restore to, which is why it is recorded now even
               though the undo button is not built yet.
    write/edit/command/delegate
               one row per action: which file or command, insertions and
               deletions, and the detail (a unified diff, or the command's
               output) in a file under <home>/coding_runs/.

Diffs are difflib, not git: the before/after text is already in hand at write
time, so a change is recorded correctly even in a project that is not a git
repo. Git is only needed for the baseline, and if git is missing or the root is
not a repository the baseline row is simply skipped — recording a change must
never be the reason a write fails.
"""

from __future__ import annotations

import difflib
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from knowme.ops.coding_eval import CodingResult, run_command

RUNS_DIR = "coding_runs"
MAX_DETAIL_CHARS = 200_000
RECENT_LIMIT = 60
# Rows the dashboard shows. "baseline" is included on purpose: it is the line
# that says which commit the session started from.
_KINDS = ("baseline", "write", "edit", "command", "delegate", "verify")
# A run id is a client-supplied string when the panel asks for one file back
# (see read_detail), so it is matched against this before touching the disk.
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the table the first time it is needed.

    Lazy and additive, like library.py::_ensure_table: an existing .knowme
    database picks this up on first use with no migration step.
    """
    conn.execute("""
    CREATE TABLE IF NOT EXISTS coding_runs (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL,
        root TEXT NOT NULL,
        target TEXT DEFAULT '',
        summary TEXT DEFAULT '',
        insertions INTEGER DEFAULT 0,
        deletions INTEGER DEFAULT 0,
        detail TEXT DEFAULT '',
        head TEXT DEFAULT '',
        returncode INTEGER,
        session_id TEXT DEFAULT '',
        agent_id TEXT DEFAULT 'default'
    )
    """)
    conn.commit()


def runs_dir(home: Path) -> Path:
    return home / RUNS_DIR


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def git_path() -> str:
    """Where git is, or "" when it is not installed."""
    return shutil.which("git") or ""


def git(root: Path, *args: str, timeout: int = 20) -> CodingResult | None:
    """One git call with a constructed argv, or None when git is unavailable.

    Callers never pass user input as a flag: paths reach here only after the
    tool validated them, and `core.quotepath=false` keeps non-ASCII filenames
    readable in the output instead of escaping them into octal.
    """
    if not git_path():
        return None
    return run_command([git_path(), "-c", "core.quotepath=false", *args],
                       root, timeout=timeout)


def counts(patch: str) -> tuple[int, int]:
    """(insertions, deletions) for a unified diff, ignoring the ---/+++ headers."""
    insertions = sum(1 for line in patch.splitlines()
                     if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in patch.splitlines()
                    if line.startswith("-") and not line.startswith("---"))
    return insertions, deletions


def unified_diff(before: str, after: str, target: str) -> str:
    """A unified diff between two versions of one file. Empty when identical."""
    if before == after:
        return ""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{target}", tofile=f"b/{target}", n=3))


def _write_detail(home: Path, text: str, run_id: str, suffix: str) -> str:
    """Store a detail file next to the row and return its name.

    The file is named after the row id, so the table holds a NAME and never a
    path — read_detail() can then refuse anything that is not one of ours.
    """
    if not text:
        return ""
    if len(text) > MAX_DETAIL_CHARS:
        text = text[:MAX_DETAIL_CHARS] + f"\n… 超过 {MAX_DETAIL_CHARS} 字，已截断\n"
    directory = runs_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath(f"{run_id}{suffix}").write_text(text, encoding="utf-8")
    return f"{run_id}{suffix}"


def read_detail(home: Path, filename: str) -> str:
    """The stored detail for one row, by filename from the table (never a path)."""
    name = Path(filename or "").name
    if not _ID_RE.match(Path(name).stem) or filename != name:
        raise ValueError("not a run detail file")
    path = runs_dir(home) / name
    if not path.is_file():
        raise ValueError("no such run detail")
    return path.read_text(encoding="utf-8", errors="replace")


def _insert(conn: sqlite3.Connection, run_id: str, **row) -> str:
    _ensure_table(conn)
    conn.execute(
        "INSERT INTO coding_runs (id, created_at, kind, root, target, summary,"
        " insertions, deletions, detail, head, returncode, session_id, agent_id)"
        " VALUES (:id, :created_at, :kind, :root, :target, :summary, :insertions,"
        " :deletions, :detail, :head, :returncode, :session_id, :agent_id)",
        {"id": run_id, "created_at": _now(), "kind": "write", "root": "", "target": "",
         "summary": "", "insertions": 0, "deletions": 0, "detail": "", "head": "",
         "returncode": None, "session_id": "", "agent_id": "default", **row})
    conn.commit()
    return run_id


def baseline_for(conn: sqlite3.Connection, home: Path, root: Path,
                 session_id: str = "", agent_id: str = "default") -> str:
    """The id of this session's baseline row, writing it if it is not there yet.

    Lazily, so that merely opening the Coding page records nothing: the first
    row appears the first time something is actually about to change.
    """
    _ensure_table(conn)
    existing = conn.execute(
        "SELECT id FROM coding_runs WHERE kind='baseline' AND root=? AND session_id=?"
        " ORDER BY rowid LIMIT 1", (str(root), session_id)).fetchone()
    if existing:
        return existing["id"]
    # Not a repository at all → no baseline to take. Changes are still
    # recorded; only the "where it started from" line is missing. A repository
    # with no commits yet DOES get a baseline (head stays empty) — that is a
    # different situation and the row says so.
    top = git(root, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0:
        return ""
    head, patch = "", ""
    revision = git(root, "rev-parse", "HEAD")
    if revision is not None and revision.returncode == 0 and revision.output.strip():
        head = revision.output.strip().splitlines()[0]
    # `-- .` limits the diff to the project root, so a root that is one
    # directory inside a bigger repository does not adopt the whole repo.
    diff = git(root, "diff", "HEAD", "--", ".")
    if diff is not None and diff.returncode == 0:
        patch = diff.output
    run_id = uuid.uuid4().hex
    return _insert(conn, run_id, kind="baseline", root=str(root), head=head,
                   summary=(f"起点 {head[:8]}" if head else "起点（还没有提交）"),
                   session_id=session_id, agent_id=agent_id,
                   detail=_write_detail(home, patch, run_id, ".diff"))


def record(conn: sqlite3.Connection, home: Path, root: Path, kind: str, target: str,
           *, before: str = "", after: str = "", summary: str = "", command: str = "",
           output: str = "", returncode: int | None = None, session_id: str = "",
           agent_id: str = "default") -> str:
    """Record one change and return its run id.

    Every mutating tool goes through here, which is also where the baseline is
    made — a caller cannot forget it, and the first change of a session is
    exactly the moment the "before" picture is still available.

    ``returncode`` is only meaningful for "command" and "verify" rows, and the
    frontend reads three cases out of it: a number is the real exit code,
    ``None`` means it was killed on the timeout, and ``-1`` means it never
    started (denylist, or no such executable). Three different sentences.
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown run kind: {kind}")
    baseline_for(conn, home, root, session_id, agent_id)
    patch = unified_diff(before, after, target) if kind in ("write", "edit") else ""
    insertions, deletions = counts(patch)
    detail_source = patch if patch else output
    suffix = ".diff" if patch else ".log"
    run_id = uuid.uuid4().hex
    return _insert(
        conn, run_id, kind=kind, root=str(root), target=target,
        summary=summary or command, insertions=insertions, deletions=deletions,
        returncode=returncode, session_id=session_id, agent_id=agent_id,
        detail=_write_detail(home, detail_source, run_id, suffix))


def recent_runs(conn: sqlite3.Connection, root: Path | None = None,
                limit: int = RECENT_LIMIT) -> list[dict]:
    """Newest first, metadata only — never the diff text (it can be megabytes)."""
    _ensure_table(conn)
    sql = ("SELECT id, created_at, kind, root, target, summary, insertions, deletions,"
           " detail, head, returncode, session_id FROM coding_runs")
    args: list = []
    if root is not None:
        sql += " WHERE root=?"
        args.append(str(root))
    sql += " ORDER BY rowid DESC LIMIT ?"
    args.append(max(1, min(int(limit or RECENT_LIMIT), 200)))
    rows = conn.execute(sql, args).fetchall()
    return [{"id": r["id"], "at": r["created_at"], "kind": r["kind"],
             "target": r["target"], "summary": r["summary"],
             "insertions": r["insertions"], "deletions": r["deletions"],
             "has_detail": bool(r["detail"]), "detail": r["detail"],
             "head": r["head"], "returncode": r["returncode"],
             "session_id": r["session_id"]} for r in rows]


__all__ = ["MAX_DETAIL_CHARS", "RECENT_LIMIT", "baseline_for", "counts", "git",
           "git_path", "read_detail", "recent_runs", "record", "runs_dir",
           "unified_diff"]

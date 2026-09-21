"""The document library — every document you have brought into KnowMe.

TWO THINGS are stored per document, on purpose:

  the ORIGINAL BYTES, at ``<home>/documents/<uuid4><suffix>``
      The browser needs these to render a PDF properly (figures, layout, and
      the text layer you select and send to the agent). Extracted text alone
      would lose all of that.
      The stored name is a fresh UUID, NEVER the uploaded filename: a filename
      is attacker-controlled input and must never decide where a file lands.

  the EXTRACTED TEXT, in SQLite (``documents.content``), FTS5-indexed
      This is what the agent reads and what search runs over, so neither has to
      re-parse a PDF on every turn.

WHERE IT LIVES: ``_ensure_table`` creates the table lazily, like
``tools/knowledge.py`` does — NOT in ``db.py``'s ``SCHEMA``. An existing
workspace therefore needs no migration, and the FTS index is built with the
same external-content + trigger idiom ``db.py`` already uses for facts.

SCOPE: documents are **workspace-level, not agent-scoped**. Everyone in the
workspace shares one library — a document on your disk is not owned by
whichever agent happened to be selected when you opened it, and scoping it
would mean the ``reader`` agent could not see a file you just uploaded while
talking to ``default``. (Notes and memories ARE agent-scoped; a library
isn't.) ``added_by`` records which agent brought it in, for provenance only,
and is deliberately not a filter.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from knowme.applications.reader import ReaderError, parse_bytes

# Suffix -> a coarse kind the frontend switches its renderer on. Anything not
# listed is plain text, which is what the reader falls back to anyway.
KINDS = {
    ".md": "markdown", ".markdown": "markdown",
    ".pdf": "pdf",
    ".html": "html", ".htm": "html",
    ".csv": "csv",
    ".json": "json",
    ".py": "code", ".js": "code", ".ts": "code", ".sh": "code",
    ".xml": "text", ".txt": "text",
}
MAX_TITLE = 200


class Document(TypedDict):
    id: str
    title: str
    kind: str
    suffix: str
    source: str
    added_by: str
    chars: int
    bytes: int
    created_at: str


def kind_for(name: str) -> str:
    suffix = Path(name.split("?", 1)[0].lower()).suffix
    return KINDS.get(suffix, "text")


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the documents table + its FTS index, the first time it is needed.

    The FTS table is EXTERNAL CONTENT (``content=documents``): the text is
    stored once, in ``documents``, and the index holds only the tokens. The
    three triggers keep the index in step, which is the same pattern db.py
    uses for facts_fts — including the ``'delete'``-then-insert dance on
    update, which is how you remove an entry from an external-content index.
    """
    conn.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        kind TEXT NOT NULL,
        suffix TEXT NOT NULL,
        path TEXT NOT NULL,              -- relative to home: documents/<uuid4>.pdf
        source TEXT DEFAULT '',          -- the filename or URL it came from
        content TEXT DEFAULT '',         -- the extracted text (FTS-indexed)
        chars INTEGER DEFAULT 0,
        bytes INTEGER DEFAULT 0,
        sha256 TEXT DEFAULT '',          -- re-adding the same file is a no-op
        added_by TEXT DEFAULT 'default',
        created_at TEXT NOT NULL
    )
    """)
    conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
        title, content, content=documents, content_rowid=rowid
    )
    """)
    # rowid, not id: this table's key is a TEXT uuid, so the rowid is the
    # integer handle FTS needs. (facts/episodes use new.id because theirs IS
    # an INTEGER PRIMARY KEY — same idiom, different key type.)
    conn.execute("""
    CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
        INSERT INTO documents_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
    END
    """)
    conn.execute("""
    CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
        INSERT INTO documents_fts(documents_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
    END
    """)
    conn.execute("""
    CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
        INSERT INTO documents_fts(documents_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
        INSERT INTO documents_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
    END
    """)
    conn.commit()


def _public(row: sqlite3.Row) -> Document:
    """A row as the dashboard sees it — never the text (it can be megabytes)."""
    return Document({
        "id": row["id"], "title": row["title"], "kind": row["kind"],
        "suffix": row["suffix"], "source": row["source"],
        "added_by": row["added_by"], "chars": row["chars"], "bytes": row["bytes"],
        "created_at": row["created_at"],
    })


def _title_from(name: str) -> str:
    """The human name for a document: the filename without its extension."""
    base = Path(name.split("?", 1)[0]).name or "未命名文档"
    stem = base.rsplit(".", 1)[0] or base
    return re.sub(r"\s+", " ", stem).strip()[:MAX_TITLE] or "未命名文档"


def save_document(conn: sqlite3.Connection, home: Path, *, name: str, raw: bytes,
                  text: str, source: str = "", added_by: str = "default") -> Document:
    """Write the original to disk and the text to SQLite; return the row.

    Re-adding a byte-identical document returns the EXISTING row instead of
    duplicating it — opening the same PDF twice should not grow the library,
    which is the whole reason ``sha256`` is stored.
    """
    _ensure_table(conn)
    digest = hashlib.sha256(raw).hexdigest()
    existing = conn.execute(
        "SELECT * FROM documents WHERE sha256=? ORDER BY created_at LIMIT 1", (digest,)
    ).fetchone()
    if existing:
        return _public(existing)

    suffix = Path(name.split("?", 1)[0].lower()).suffix or ".txt"
    doc_id = str(uuid4())
    stored = f"documents/{doc_id}{suffix}"      # uuid4 name, never the upload's
    folder = home / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}{suffix}").write_bytes(raw)

    conn.execute(
        "INSERT INTO documents (id, title, kind, suffix, path, source, content, "
        "chars, bytes, sha256, added_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, _title_from(name), kind_for(name), suffix, stored, source,
         text, len(text), len(raw), digest, added_by,
         datetime.now(UTC).isoformat(timespec="seconds")),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return _public(row)


def list_documents(conn: sqlite3.Connection, limit: int = 100) -> list[Document]:
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT * FROM documents ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_public(r) for r in rows]


def get_document(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    _ensure_table(conn)
    return conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()


def file_path(conn: sqlite3.Connection, home: Path, doc_id: str) -> Path | None:
    """Where the ORIGINAL bytes live. None when the row (or its file) is gone.

    The stored path is resolved and re-checked under ``home`` before it is
    returned, so a row edited by hand cannot turn this into an arbitrary-file
    read.
    """
    row = get_document(conn, doc_id)
    if row is None:
        return None
    target = (home / row["path"]).resolve()
    if home.resolve() not in target.parents or not target.is_file():
        return None
    return target


def delete_document(conn: sqlite3.Connection, home: Path, doc_id: str) -> bool:
    """Drop the row (and its FTS entry via the delete trigger) + the original."""
    _ensure_table(conn)
    target = file_path(conn, home, doc_id)
    cursor = conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()
    if cursor.rowcount and target is not None:
        target.unlink(missing_ok=True)
    return bool(cursor.rowcount)


def search_documents(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """Full-text search over titles and bodies.

    FTS5 treats punctuation as syntax, so the query is reduced to bare terms
    and each is quoted — otherwise a search for "O'Reilly" or "what is x?" is a
    syntax error rather than a search. Long documents are matched, not dumped:
    each hit carries a short snippet around the match, which is what an agent
    (and the sidebar) actually needs.
    """
    _ensure_table(conn)
    terms = re.findall(r"\w+", query or "")
    if not terms:
        return []
    match = " ".join(f'"{t}"' for t in terms)
    rows = conn.execute(
        "SELECT d.id, d.title, d.kind, d.chars, "
        "       snippet(documents_fts, 1, '', '', ' … ', 18) AS snippet "
        "FROM documents_fts JOIN documents d ON d.rowid = documents_fts.rowid "
        "WHERE documents_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, limit),
    ).fetchall()
    return [{"id": r["id"], "title": r["title"], "kind": r["kind"],
             "chars": r["chars"], "snippet": r["snippet"]} for r in rows]


def text_window(text: str, offset: int = 0, limit: int = 4000) -> dict:
    """A bounded slice of a document, for an agent tool result.

    A whole book must never enter the context, so every read tool goes through
    here: the caller gets ``limit`` characters, where it is in the document, and
    whether there is more. The window index is 1-based for humans reading the
    tool output.
    """
    total = len(text or "")
    start = max(0, min(int(offset or 0), total))
    end = min(total, start + max(1, int(limit)))
    return {
        "text": (text or "")[start:end],
        "offset": start,
        "limit": end - start,
        "chars": total,
        "has_more": end < total,
    }


def parse_and_save(conn: sqlite3.Connection, home: Path, *, name: str, raw: bytes,
                   content_type: str = "", source: str = "",
                   added_by: str = "default") -> Document:
    """Parse bytes through the Reader's extractor, then store both halves.

    One entry point for every way a document arrives (file upload, URL fetch,
    drag-and-drop), so the stored text can never depend on which door was used.
    """
    text = parse_bytes(name, raw, content_type)
    if not text.strip():
        raise ReaderError("文档解析后没有文本内容（可能是扫描版 PDF 或空文件）")
    return save_document(conn, home, name=name, raw=raw, text=text,
                         source=source, added_by=added_by)

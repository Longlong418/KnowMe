"""Knowledge base tools — Sapphire-style notes with [[links]].

Phase 2 implementation:
- Folder-based organization
- [[Link]] syntax parsing
- Markdown rendering
- Search and association
"""

from __future__ import annotations

import html
import re
import sqlite3
from datetime import UTC, datetime
from typing import TypedDict
from uuid import uuid4


class Note(TypedDict):
    """Note metadata and content."""
    id: str
    title: str
    folder: str
    content: str
    created_at: str
    updated_at: str
    agent_id: str


def _ensure_table(conn: sqlite3.Connection):
    """Create the notes table and add the Agent scope to old databases."""
    conn.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        folder TEXT DEFAULT 'default',
        content TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        agent_id TEXT DEFAULT 'default'
    )
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
    if "agent_id" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN agent_id TEXT DEFAULT 'default'")
    conn.commit()


def _note_from_row(row) -> Note:
    values = dict(row)
    return Note({
        "id": values["id"],
        "title": values["title"],
        "folder": values["folder"],
        "content": values["content"],
        "created_at": values["created_at"],
        "updated_at": values["updated_at"],
        "agent_id": values.get("agent_id", "default"),
    })


def parse_links(content: str) -> list[str]:
    """Extract [[wiki-link]] references from content."""
    return list(dict.fromkeys(link.strip() for link in re.findall(
        r"\[\[([^\]]+)\]\]", content
    ) if link.strip()))


def linkify_content(content: str) -> str:
    """Convert [[wiki-links]] to clickable links."""
    def replace_link(match):
        link_text = match.group(1).strip()
        safe = html.escape(link_text, quote=True)
        return f'<a href="#knowledge/{safe}">{safe}</a>'
    return re.sub(r'\[\[([^\]]+)\]\]', replace_link, content)


# The columns every note query returns, in one place.
_COLS = "id, title, folder, content, created_at, updated_at, agent_id"


def _agent_conds(agent_id: str | None) -> tuple[list[str], list]:
    """The WHERE conditions that scope a notes query to one Agent.

    ``agent_id=None`` means NO scoping — every Agent's notes. That is what the
    Dashboard's knowledge view asks for, because the human reading it owns all
    the notes; it is one knowledge base, not one per Agent. The Agents' own
    tools never pass None (they close over their own id), so an Agent still
    cannot read another Agent's notes. None is deliberately explicit: it has to
    be written out, so no existing caller can widen its scope by accident.
    """
    if agent_id is None:
        return [], []
    return ["agent_id = ?"], [agent_id]


def _where(conds: list[str]) -> str:
    return f" WHERE {' AND '.join(conds)}" if conds else ""


def _select(conn: sqlite3.Connection, conds: list[str], params: list,
            order: str = " ORDER BY updated_at DESC") -> list[Note]:
    """Run the one SELECT shape every note listing shares."""
    cursor = conn.execute(
        f"SELECT {_COLS} FROM notes{_where(conds)}{order}",
        tuple(params),
    )
    return [_note_from_row(row) for row in cursor.fetchall()]


def get_note(conn: sqlite3.Connection, note_id: str,
             agent_id: str | None = "default") -> Note | None:
    """Get a note by ID. Pass agent_id=None to find it whoever owns it."""
    _ensure_table(conn)
    conds, params = _agent_conds(agent_id)
    conds.insert(0, "id = ?")
    params.insert(0, note_id)
    row = conn.execute(
        f"SELECT {_COLS} FROM notes{_where(conds)}", tuple(params)
    ).fetchone()
    return _note_from_row(row) if row else None


def create_note(conn: sqlite3.Connection, title: str, folder: str = "default", content: str = "",
                agent_id: str = "default") -> Note:
    """Create a new note."""
    _ensure_table(conn)
    title = title.strip()
    if not title:
        raise ValueError("note title cannot be empty")
    folder = folder.strip() or "default"
    now = datetime.now(UTC).isoformat()
    note_id = str(uuid4())

    conn.execute(
        "INSERT INTO notes (id, title, folder, content, created_at, updated_at, agent_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (note_id, title, folder, content, now, now, agent_id)
    )
    conn.commit()

    return Note({"id": note_id, "title": title, "folder": folder, "content": content,
                 "created_at": now, "updated_at": now, "agent_id": agent_id})


def update_note(conn: sqlite3.Connection, note_id: str, content: str,
                title: str | None = None, folder: str | None = None,
                agent_id: str | None = "default") -> Note | None:
    """Update a note's content. agent_id=None updates it whoever owns it.

    Editing never reassigns a note: the row keeps the agent_id it was created
    with even when the human edits it from another Agent's view.
    """
    note = get_note(conn, note_id, agent_id)
    if not note:
        return None

    new_title = note["title"] if title is None else title.strip()
    new_folder = note["folder"] if folder is None else folder.strip() or "default"
    if not new_title:
        raise ValueError("note title cannot be empty")
    updated_at = datetime.now(UTC).isoformat()
    conds, params = _agent_conds(agent_id)
    conds.insert(0, "id = ?")
    params.insert(0, note_id)
    conn.execute(
        f"UPDATE notes SET title=?, folder=?, content=?, updated_at=?{_where(conds)}",
        (new_title, new_folder, content, updated_at, *params),
    )
    conn.commit()

    return {**note, "title": new_title, "folder": new_folder,
            "content": content, "updated_at": updated_at}


def delete_note(conn: sqlite3.Connection, note_id: str,
                agent_id: str | None = "default") -> bool:
    """Delete a note. agent_id=None deletes it whoever owns it."""
    _ensure_table(conn)
    conds, params = _agent_conds(agent_id)
    conds.insert(0, "id = ?")
    params.insert(0, note_id)
    cursor = conn.execute(f"DELETE FROM notes{_where(conds)}", tuple(params))
    conn.commit()
    return cursor.rowcount > 0


def list_notes(conn: sqlite3.Connection, folder: str | None = None,
               agent_id: str | None = "default") -> list[Note]:
    """List notes, optionally filtered by folder. agent_id=None lists all Agents'."""
    _ensure_table(conn)
    conds, params = _agent_conds(agent_id)
    if folder:
        conds.append("folder = ?")
        params.append(folder)
    return _select(conn, conds, params)


def list_folders(conn: sqlite3.Connection, agent_id: str | None = "default") -> list[str]:
    """List every folder that has a note. agent_id=None spans all Agents."""
    _ensure_table(conn)
    conds, params = _agent_conds(agent_id)
    cursor = conn.execute(
        f"SELECT DISTINCT folder FROM notes{_where(conds)} ORDER BY folder", tuple(params)
    )
    return [row["folder"] for row in cursor.fetchall()]


def search_notes(conn: sqlite3.Connection, query: str,
                 agent_id: str | None = "default") -> list[Note]:
    """Search notes by title or content. agent_id=None searches all Agents'."""
    _ensure_table(conn)
    conds, params = _agent_conds(agent_id)
    conds.append("(title LIKE ? OR content LIKE ?)")
    params += [f"%{query}%", f"%{query}%"]
    return _select(conn, conds, params)


def get_linked_notes(conn: sqlite3.Connection, note_id: str,
                     agent_id: str | None = "default") -> list[Note]:
    """Get notes that link TO this note. agent_id=None spans all Agents.

    A [[link]] in a note the Learning agent wrote can point at a note the Reader
    agent wrote — the human's knowledge base is one graph, so the backlinks
    shown next to a note have to cross Agents too, or a link you can click
    would have no backlink on the other end.
    """
    note = get_note(conn, note_id, agent_id)
    if not note:
        return []

    _ensure_table(conn)
    conds, params = _agent_conds(agent_id)
    conds.append("content LIKE ?")
    params.append(f"%[[{note['title']}]]%")
    conds.append("id != ?")
    params.append(note_id)
    return _select(conn, conds, params)


def make_knowledge_tools(conn: sqlite3.Connection, agent_id: str = "default") -> dict:
    """Return a dict of knowledge-tool-name -> Tool instance."""
    from knowme.core.tools import Tool

    return {
        "get_note": Tool(
            name="get_note",
            description="Get a note by its ID. "
                        "Use when the user asks to read a specific note.",
            input_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "The note ID to retrieve."},
                },
                "required": ["note_id"],
            },
            fn=lambda note_id, **_: get_note(conn, note_id, agent_id) or {"error": "Note not found"},
        ),
        "create_note": Tool(
            name="create_note",
            description="Create a new note with a title, folder, and initial content. "
                        "Use when the user wants to capture new information.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The note title."},
                    "folder": {"type": "string", "description": "The folder to organize this note.", "default": "default"},
                    "content": {"type": "string", "description": "Initial content.", "default": ""},
                },
                "required": ["title"],
            },
            fn=lambda title, folder="default", content="", **_: create_note(
                conn, title, folder, content, agent_id
            ),
        ),
        "update_note": Tool(
            name="update_note",
            description="Update the content of an existing note. "
                        "Use when the user wants to add or modify a note.",
            input_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "The note ID to update."},
                    "content": {"type": "string", "description": "New content."},
                    "title": {"type": "string", "description": "Optional new title."},
                    "folder": {"type": "string", "description": "Optional new folder."},
                },
                "required": ["note_id", "content"],
            },
            fn=lambda note_id, content, title=None, folder=None, **_: update_note(
                conn, note_id, content, title, folder, agent_id
            ) or {"error": "Note not found"},
        ),
        "delete_note": Tool(
            name="delete_note",
            description="Delete a note by its ID. "
                        "Use when the user explicitly asks to remove a note.",
            input_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "The note ID to delete."},
                },
                "required": ["note_id"],
            },
            fn=lambda note_id, **_: {"deleted": delete_note(conn, note_id, agent_id)},
        ),
        "list_notes": Tool(
            name="list_notes",
            description="List all notes, optionally filtered by folder. "
                        "Use when the user wants to see their notes.",
            input_schema={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "Filter by folder."},
                },
            },
            fn=lambda folder=None, **_: list_notes(conn, folder, agent_id),
        ),
        "search_notes": Tool(
            name="search_notes",
            description="Search notes by query string. "
                        "Use when the user wants to find information.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                },
                "required": ["query"],
            },
            fn=lambda query, **_: search_notes(conn, query, agent_id),
        ),
        "list_folders": Tool(
            name="list_folders",
            description="List all folders that contain notes. "
                        "Use when the user wants to see their organization structure.",
            input_schema={"type": "object", "properties": {}},
            fn=lambda **_: list_folders(conn, agent_id),
        ),
        "get_linked_notes": Tool(
            name="get_linked_notes",
            description="Get notes that link TO this note via [[wiki-links]]. "
                        "Use when analyzing note relationships.",
            input_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "The note ID to find backlinks for."},
                },
                "required": ["note_id"],
            },
            fn=lambda note_id, **_: get_linked_notes(conn, note_id, agent_id),
        ),
        "parse_links": Tool(
            name="parse_links",
            description="Extract all [[wiki-link]] references from text. "
                        "Use when analyzing a note's outgoing links.",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The content to parse."},
                },
                "required": ["content"],
            },
            fn=lambda content, **_: parse_links(content),
        ),
    }

"""Knowledge base tools — Sapphire-style notes with [[links]].

Phase 2 implementation:
- Folder-based organization
- [[Link]] syntax parsing
- Markdown rendering
- Search and association
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from knowme.db import connect


class Note(TypedDict):
    """Note metadata and content."""
    id: str
    title: str
    folder: str
    content: str
    created_at: str
    updated_at: str


def parse_links(content: str) -> list[str]:
    """Extract [[wiki-link]] references from content."""
    return re.findall(r'\[\[([^\]]+)\]\]', content)


def linkify_content(content: str) -> str:
    """Convert [[wiki-links]] to clickable links."""
    def replace_link(match):
        link_text = match.group(1)
        return f'<a href="#knowledge/{link_text}">{link_text}</a>'
    return re.sub(r'\[\[([^\]]+)\]\]', replace_link, content)


def get_note(note_id: str) -> Note | None:
    """Get a note by ID."""
    db = connect()
    cursor = db.execute(
        "SELECT id, title, folder, content, created_at, updated_at "
        "FROM notes WHERE id = ?",
        (note_id,)
    )
    row = cursor.fetchone()
    if not row:
        return None
    return Note({
        "id": row["id"],
        "title": row["title"],
        "folder": row["folder"],
        "content": row["content"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    })


def create_note(title: str, folder: str = "default", content: str = "") -> Note:
    """Create a new note."""
    now = datetime.utcnow().isoformat()
    note_id = f"{now.replace(':', '-')}-{abs(hash(title)) % 10000}"

    db = connect()
    db.execute(
        "INSERT INTO notes (id, title, folder, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (note_id, title, folder, content, now, now)
    )
    db.commit()

    return Note({
        "id": note_id,
        "title": title,
        "folder": folder,
        "content": content,
        "created_at": now,
        "updated_at": now,
    })


def update_note(note_id: str, content: str) -> Note | None:
    """Update a note's content."""
    note = get_note(note_id)
    if not note:
        return None

    db = connect()
    db.execute(
        "UPDATE notes SET content = ?, updated_at = ? WHERE id = ?",
        (content, datetime.utcnow().isoformat(), note_id)
    )
    db.commit()

    return {**note, "content": content, "updated_at": datetime.utcnow().isoformat()}


def delete_note(note_id: str) -> bool:
    """Delete a note."""
    db = connect()
    db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    db.commit()
    return True


def list_notes(folder: str | None = None) -> list[Note]:
    """List all notes, optionally filtered by folder."""
    db = connect()
    if folder:
        cursor = db.execute(
            "SELECT id, title, folder, content, created_at, updated_at FROM notes WHERE folder = ?",
            (folder,)
        )
    else:
        cursor = db.execute(
            "SELECT id, title, folder, content, created_at, updated_at FROM notes ORDER BY updated_at DESC"
        )
    return [Note(row) for row in cursor.fetchall()]


def list_folders() -> list[str]:
    """List all folders with notes."""
    db = connect()
    cursor = db.execute("SELECT DISTINCT folder FROM notes")
    return [row["folder"] for row in cursor.fetchall()]


def search_notes(query: str) -> list[Note]:
    """Search notes by title or content."""
    db = connect()
    cursor = db.execute(
        "SELECT id, title, folder, content, created_at, updated_at FROM notes "
        "WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC",
        (f"%{query}%", f"%{query}%")
    )
    return [Note(row) for row in cursor.fetchall()]


def get_linked_notes(note_id: str) -> list[Note]:
    """Get notes that link TO this note."""
    note = get_note(note_id)
    if not note:
        return []

    # Find all notes that have [[note_title]] in their content
    db = connect()
    pattern = f"%[[{note['title']}]]%"
    cursor = db.execute(
        "SELECT id, title, folder, content, created_at, updated_at FROM notes "
        "WHERE content LIKE ? AND id != ?",
        (pattern, note_id)
    )
    return [Note(row) for row in cursor.fetchall()]


def make_knowledge_tools() -> dict:
    """Return a dict of knowledge-tool-name -> Tool instance."""
    from knowme.tools.registry import Tool

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
            fn=lambda note_id, **_: get_note(note_id) or {"error": "Note not found"},
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
            fn=lambda title, folder="default", content="", **_: create_note(title, folder, content),
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
                },
                "required": ["note_id", "content"],
            },
            fn=lambda note_id, content, **_: update_note(note_id, content) or {"error": "Note not found"},
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
            fn=lambda note_id, **_: {"deleted": delete_note(note_id)},
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
            fn=lambda folder=None, **_: list_notes(folder),
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
            fn=lambda query, **_: search_notes(query),
        ),
        "list_folders": Tool(
            name="list_folders",
            description="List all folders that contain notes. "
                        "Use when the user wants to see their organization structure.",
            input_schema={"type": "object", "properties": {}},
            fn=lambda **_: list_folders(),
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
            fn=lambda note_id, **_: get_linked_notes(note_id),
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


# Auto-registration
_knowledge_tools = make_knowledge_tools()
__all__ = [
    "create_note", "delete_note", "get_note", "get_linked_notes",
    "list_folders", "list_notes", "linkify_content", "make_knowledge_tools",
    "parse_links", "search_notes", "update_note",
]

# Register in database schema if not exists
_connect = connect()
_connect.execute("""
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    folder TEXT DEFAULT 'default',
    content TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""")
_connect.commit()

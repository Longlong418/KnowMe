"""Reader tools — read local documents and inject selection as agent context.

Phase 2 Reader application: enables Agent to read files and have selected text
automatically injected into the turn context via the Context Bridge.

Available in the default tool registry when `KNOWME_APPLE_TOOLS` or similar is set,
or when explicitly included via AgentSpec.tools allowlist.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from knowme.tools.registry import Tool


# ---------------------------------------------------------------------------
# File reading helpers
# ---------------------------------------------------------------------------

def _read_file_text(path: Path) -> str:
    """Read a text file, best-effort encoding detection."""
    encoding = getattr(path, "_encoding", None) or "utf-8"
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        # Fall back to latin-1 which can read any byte sequence
        return path.read_text(encoding="latin-1")


def get_document(path: str) -> str:
    """Read an entire local document.

    Supported extensions (handled by open()):
      .md, .markdown, .txt, .py, .json, .csv, .jsonl, .html
    All others are read as raw UTF-8 / latin-1.

    Returns the full text content. The caller (agent) should parse as needed.
    """
    p = Path(path)
    if not p.is_file():
        return f"Error: file '{path}' not found."
    return _read_file_text(p)


# ---------------------------------------------------------------------------
# Selection helpers (mocked for now — dashboard integration adds real selection)
# ---------------------------------------------------------------------------

def get_selection() -> str:
    """Return the user's current text selection.

    In a full Reader UI implementation this would be wired to a DOM
    range/selection API.  Today it returns the empty string so that the
    model simply never gets a selection unless the UI supplies one.
    """
    # Mock: read from knownme state or return empty.
    # The real implementation will hook into the chat dock selection.
    return ""


def add_note(document_path: str, note_text: str) -> str:
    """Add an annotation/note to a document.

    In a complete implementation this would persist the note alongside the
    document.  Today it just returns a confirmation string.
    """
    doc = Path(document_path)
    if not doc.is_file():
        return f"Error: document '{document_path}' not found."
    # Append note metadata to a hidden sidecar file
    sidecar = doc.with_suffix(doc.suffix + ".notes")
    note_entry = f"[{datetime.now().isoformat()}] {note_text}\n"
    existing = ""
    try:
        existing = sidecar.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    sidecar.write_text(existing + note_entry, encoding="utf-8")
    return f"Note added to {document_path}."


def highlight(text: str, label: str = "highlight") -> str:
    """Mark text as highlighted/significant.

    Returns a marker string the model can use to track significant passages.
    """
    return f"[HIGHLIGHT:{label}] {text}"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

def make_reader_tools() -> dict:
    """Return a dict of reader-tool-name -> Tool instance.

    Called from `tools/__init__.py` registry builder to register the tools.
    """
    return {
        "get_document": Tool(
            name="get_document",
            description="Read the full contents of a local document. "
                        "Use when the user asks about a file on disk, "
                        "or wants to analyse a document's contents.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the document."},
                },
                "required": ["path"],
            },
            fn=get_document,
        ),
        "get_selection": Tool(
            name="get_selection",
            description="Return the user's current text selection. "
                        "Use when the model needs to act on what the user has highlighted "
                        "in a document, chat, or code editor.",
            input_schema={
                "type": "object",
                "properties": {},
            },
            fn=get_selection,
        ),
        "add_note": Tool(
            name="add_note",
            description="Add an annotation or note to a document. "
                        "Use when the user wants to remember something about a file "
                        "they have been reading or discussing.",
            input_schema={
                "type": "object",
                "properties": {
                    "document_path": {"type": "string", "description": "Path to the document."},
                    "note_text": {"type": "string", "description": "The note text to add."},
                },
                "required": ["document_path", "note_text"],
            },
            fn=lambda document_path, note_text, **_: add_note(document_path, note_text),
        ),
        "highlight": Tool(
            name="highlight",
            description="Mark selected text as a significant passage / highlight. "
                        "The model should call this after reading a portion of a document "
                        "to flag it for future reference.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to mark."},
                    "label": {"type": "string", "description": "Optional label for the highlight.", "default": "highlight"},
                },
                "required": ["text"],
            },
            fn=lambda text, label="highlight", **_: highlight(text, label),
        ),
    }


# ---------------------------------------------------------------------------
# Auto-registration when tools/__init__.py loads
# ---------------------------------------------------------------------------

_reader_tools = make_reader_tools()
_reader_names = list(_reader_tools.keys())


# Export individual tools for import by other modules
get_document_func = _reader_tools["get_document"].fn
get_selection_func = _reader_tools["get_selection"].fn
add_note_func = _reader_tools["add_note"].fn
highlight_func = _reader_tools["highlight"].fn

__all__ = [
    "get_document",
    "get_selection",
    "add_note",
    "highlight",
    "make_reader_tools",
]


# Auto-register when this module is imported by tools/__init__.py
# The tools/__init__.py builder checks for the _reader_* names and registers
# any Tool instances it finds. We assign the registry dict to a module-level
# variable the builder can discover.
_reader_tool_objects = _reader_tools
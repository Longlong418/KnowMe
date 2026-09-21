"""Safe, read-only project browsing for the Coding Workspace MVP.

The first Coding Workspace slice deliberately has a small contract: choose the
directory KnowMe was started in (or set ``KNOWME_PROJECT_ROOT``), browse text
files, and open one into the Agent Context Bridge.  It does not execute shell
commands or write arbitrary files.  Those operations need a separate approval
surface and remain on the roadmap.
"""

from __future__ import annotations

import os
from pathlib import Path

MAX_ENTRIES = 500
MAX_READ_BYTES = 240_000
SKIP_DIRECTORIES = frozenset({
    ".git", ".knowme", ".venv", "__pycache__", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})
SKIP_FILES = frozenset({".env", ".env.local", ".env.development", ".env.production"})
TEXT_SUFFIXES = frozenset({
    ".css", ".csv", ".html", ".ini", ".js", ".json", ".jsx", ".md",
    ".py", ".ps1", ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt",
    ".vue", ".xml", ".yaml", ".yml",
})


def project_root() -> Path:
    """Return the configured project root, defaulting to the launch directory."""
    raw = os.getenv("KNOWME_PROJECT_ROOT", "").strip()
    root = Path(raw).expanduser() if raw else Path.cwd()
    return root.resolve()


def _relative_path(root: Path, relative: str | None = None) -> Path:
    """Resolve a browser-supplied relative path without allowing traversal."""
    candidate = (root / (relative or "")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path is outside the configured project root")
    return candidate


def _is_text_file(path: Path) -> bool:
    return path.name not in SKIP_FILES and path.suffix.lower() in TEXT_SUFFIXES


def _entry(root: Path, path: Path) -> dict:
    relative = path.relative_to(root).as_posix()
    if path.is_dir():
        return {"path": relative, "name": path.name, "kind": "directory", "depth": len(path.relative_to(root).parts)}
    return {
        "path": relative,
        "name": path.name,
        "kind": "file",
        "depth": len(path.relative_to(root).parts) - 1,
        "size": path.stat().st_size,
    }


def list_entries(root: Path | None = None) -> list[dict]:
    """Return a bounded flat tree containing directories and readable text files."""
    root = (root or project_root()).resolve()
    if not root.is_dir():
        raise ValueError(f"project root does not exist: {root}")
    entries: list[dict] = []
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        directories[:] = sorted(d for d in directories if d not in SKIP_DIRECTORIES and not d.startswith("."))
        for directory in directories:
            entries.append(_entry(root, current_path / directory))
        for name in sorted(files):
            path = current_path / name
            if _is_text_file(path):
                entries.append(_entry(root, path))
            if len(entries) >= MAX_ENTRIES:
                return sorted(entries[:MAX_ENTRIES], key=lambda item: (item["path"].lower(), item["kind"]))
    return sorted(entries, key=lambda item: (item["path"].lower(), item["kind"]))


def read_file(relative: str, root: Path | None = None) -> dict:
    """Read one permitted text file, returning a truncation marker when needed."""
    root = (root or project_root()).resolve()
    path = _relative_path(root, relative)
    if not path.is_file() or not _is_text_file(path):
        raise ValueError("only readable text files inside the project root can be opened")
    if path.stat().st_size > MAX_READ_BYTES:
        raw = path.read_bytes()[:MAX_READ_BYTES]
        truncated = True
    else:
        raw = path.read_bytes()
        truncated = False
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
    return {
        "path": path.relative_to(root).as_posix(),
        "content": content,
        "size": path.stat().st_size,
        "truncated": truncated,
    }


def workspace_info(relative: str | None = None) -> dict:
    """Build the JSON payload used by the Coding Workspace view."""
    root = project_root()
    payload = {
        "root": str(root),
        "entries": list_entries(root),
        "selected": None,
    }
    if relative:
        payload["selected"] = read_file(relative, root)
    return payload


def workspace_action(payload: dict) -> dict:
    """Handle the read-only workspace API action."""
    try:
        action = payload.get("action", "list")
        if action == "list":
            return {"ok": True, **workspace_info(payload.get("path"))}
        if action == "read":
            return {"ok": True, "file": read_file(payload.get("path", ""))}
        return {"ok": False, "error": f"unknown workspace action: {action}"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


__all__ = ["MAX_ENTRIES", "MAX_READ_BYTES", "list_entries", "project_root",
           "read_file", "workspace_action", "workspace_info"]

"""Safe project access for the Coding Workspace: the page, and the rules.

This module is the ONE place that decides what is in bounds. Choosing the
project root (``KNOWME_PROJECT_ROOT``, or the launch directory), resolving a
relative path without letting it escape, and naming the files that are never
touched (.env, .git, .knowme, …) all happen here — tools/coding.py imports
these guards rather than reimplementing them, so the page and the agent's own
hands cannot disagree about the boundary.

The PAGE is still read-only, and the switches in ``coding.json`` say what the
agent may do: ``enabled``/``default_backend`` pick the local CLI to delegate to,
and ``allow_write`` gates the write_file / edit_file / run_command tools
(.knowme/coding_runs.py keeps the receipt of everything they change).  Browsing
and reading are always allowed; that is what makes ``allow_write`` the only
consent the user has to give.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from knowme.applications.coding_runs import read_detail

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


BACKENDS = {
    "pi": {"label": "pi", "command": "pi", "description": "轻量本地编码 Agent"},
    "claude": {"label": "Claude Code", "command": "claude", "description": "Anthropic 的本地编码 CLI"},
    "codex": {"label": "Codex", "command": "codex", "description": "OpenAI 的本地编码 CLI"},
}
DEFAULT_CODING_SETTINGS = {
    "default_backend": "pi",
    "enabled": {"pi": True, "claude": False, "codex": False},
    # Off until the user says otherwise: KnowMe editing your files is the one
    # thing here that cannot be undone by ignoring it. The Coding page's
    # "允许写和跑命令" checkbox is this field.
    "allow_write": False,
    # 改完自动跑一遍验收（applications/coding_verify.py）。默认开：改完不验，
    # 等于把「测试还过不过」推给下一个打开终端的人。
    "verify_auto": True,
    # 空 = 按项目标记自动认一条。填了就用你填的。
    "verify_command": "",
}


def coding_settings_path(home: Path) -> Path:
    return home / "coding.json"


def load_coding_settings(home: Path) -> dict:
    """Read the small, non-secret Coding Workspace configuration."""
    path = coding_settings_path(home)
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        settings = {}
    enabled = dict(DEFAULT_CODING_SETTINGS["enabled"])
    enabled.update({k: bool(v) for k, v in (settings.get("enabled") or {}).items()
                    if k in BACKENDS})
    default_backend = settings.get("default_backend", DEFAULT_CODING_SETTINGS["default_backend"])
    if default_backend not in BACKENDS:
        default_backend = "pi"
    allow_write = bool(settings.get("allow_write", DEFAULT_CODING_SETTINGS["allow_write"]))
    verify_auto = bool(settings.get("verify_auto", DEFAULT_CODING_SETTINGS["verify_auto"]))
    verify_command = str(settings.get("verify_command", "") or "").strip()
    return {"default_backend": default_backend, "enabled": enabled, "allow_write": allow_write,
            "verify_auto": verify_auto, "verify_command": verify_command}


def save_coding_settings(home: Path, payload: dict) -> dict:
    """Persist backend switches without ever storing credentials."""
    current = load_coding_settings(home)
    enabled = current["enabled"]
    incoming = payload.get("enabled") or {}
    for name in BACKENDS:
        if name in incoming:
            enabled[name] = bool(incoming[name])
    default_backend = payload.get("default_backend", current["default_backend"])
    if default_backend not in BACKENDS:
        raise ValueError("unknown coding backend")
    allow_write = current["allow_write"]
    if "allow_write" in payload:
        allow_write = bool(payload["allow_write"])
    verify_auto = current["verify_auto"]
    if "verify_auto" in payload:
        verify_auto = bool(payload["verify_auto"])
    verify_command = current["verify_command"]
    if "verify_command" in payload:
        verify_command = str(payload["verify_command"] or "").strip()
    result = {"default_backend": default_backend, "enabled": enabled,
              "allow_write": allow_write, "verify_auto": verify_auto,
              "verify_command": verify_command}
    home.mkdir(parents=True, exist_ok=True)
    coding_settings_path(home).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def coding_backends(home: Path) -> dict:
    """Return safe installation/status information for the Coding page."""
    settings = load_coding_settings(home)
    items = []
    for name, definition in BACKENDS.items():
        executable = shutil.which(definition["command"])
        items.append({
            "id": name,
            "label": definition["label"],
            "description": definition["description"],
            "command": definition["command"],
            "path": executable or "",
            "installed": bool(executable),
            "enabled": bool(settings["enabled"].get(name)),
            "default": settings["default_backend"] == name,
        })
    return {"settings": settings, "backends": items}


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


def workspace_action(payload: dict, home: Path | None = None) -> dict:
    """Handle the workspace API actions: list, read, and one stored diff.

    ``home`` is only needed for "run" — the receipt's detail files live under
    KNOWME_HOME, not under the project, because the project is the user's and
    they did not ask KnowMe to leave files in it.
    """
    try:
        action = payload.get("action", "list")
        if action == "list":
            return {"ok": True, **workspace_info(payload.get("path"))}
        if action == "read":
            return {"ok": True, "file": read_file(payload.get("path", ""))}
        if action == "run":
            if home is None:
                return {"ok": False, "error": "这一轮没有配置 KNOWME_HOME，读不到收据"}
            return {"ok": True, "detail": read_detail(home, payload.get("detail", ""))}
        return {"ok": False, "error": f"unknown workspace action: {action}"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


__all__ = ["MAX_ENTRIES", "MAX_READ_BYTES", "list_entries", "project_root",
           "read_file", "workspace_action", "workspace_info"]

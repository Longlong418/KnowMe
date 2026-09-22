"""KnowMe local Web client package."""

# The package keeps one small public surface while the implementation is split
# by responsibility: data, runtime, and HTTP transport.
# ruff: noqa: F401, I001
from .runtime import *
from .data import *
from .data import (
    _thread_history,
)
from .runtime import (
    _NOTION_EPISODES_TTL,
    _notion_lock,
)
from . import data as _data
from . import runtime as _runtime
from .server import Handler, main


def collect(agent_id: str = "default") -> dict:
    """Collect the data shown by the Web client."""
    # Keep the payload contract visible to diagnostics:
    # "agent_id", "settings", "tools", "facts", "episodes", "soul", "chat_log",
    # "sessions", "turns", "stats", "db", "skills", "trace_file",
    # "chat_pending", "graph".
    _sync_dependencies()
    for name in (
        "load_settings", "settings_info", "list_connections", "list_providers",
        "tools_info", "usage_summary", "list_models",
    ):
        setattr(_data, name, globals()[name])
    return _data.collect(agent_id)


def _sync_state() -> None:
    # Only the TTL: a caller may patch it on the package to age the cache out.
    # The Notion caches themselves are NOT forwarded — they live in data.py, in
    # the module whose code reads them (a copy here is what made the HTTP route
    # raise NameError; see the note next to them).
    _data._NOTION_EPISODES_TTL = _NOTION_EPISODES_TTL


def _sync_dependencies() -> None:
    """Forward the public Web dependency seam to the split modules."""
    for module in (_data, _runtime):
        for name in (
            "load_settings", "settings_info", "list_connections", "list_providers",
            "tools_info", "usage_summary", "list_models", "get_agent",
            "maybe_rotate_session", "dash_session", "application_contexts",
        ):
            if name in globals():
                setattr(module, name, globals()[name])
    _sync_state()


def memory_action(payload: dict) -> dict:
    """Forward memory mutations through the canonical Web data module."""
    _sync_dependencies()
    return _data.memory_action(payload)


def knowledge_action(payload: dict) -> dict:
    """Forward knowledge mutations through the canonical Web data module."""
    _sync_dependencies()
    return _data.knowledge_action(payload)


def chat_stream(message: str, emit, agent_id: str = "default") -> None:
    """Forward streaming turns through the canonical Web runtime module."""
    _sync_dependencies()
    return _runtime.chat_stream(message, emit, agent_id=agent_id)


def chat(message: str, agent_id: str = "default") -> dict:
    _sync_dependencies()
    return _runtime.chat(message, agent_id=agent_id)

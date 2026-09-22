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
    _notion_episodes,
    _notion_lock,
    _notion_store,
)
from . import data as _data
from . import runtime as _runtime
from .server import Handler, main


def collect(agent_id: str = "default") -> dict:
    """Collect the data shown by the Web client."""
    # Keep the payload contract visible to diagnostics:
    # "settings", "tools", "facts", "episodes", "soul", "chat_log",
    # "sessions", "turns", "stats", "db", "skills", "trace_file",
    # "chat_pending", "graph".
    _sync_dependencies()
    for name in (
        "load_settings", "settings_info", "list_connections", "list_providers",
        "tools_info", "usage_summary", "list_models",
    ):
        setattr(_data, name, globals()[name])
    result = _data.collect(agent_id)
    _pull_state()
    return result


def _sync_state() -> None:
    for name in ("_NOTION_EPISODES_TTL", "_notion_store", "_notion_episodes"):
        setattr(_data, name, globals()[name])


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


def _pull_state() -> None:
    for name in ("_notion_store", "_notion_episodes"):
        globals()[name] = getattr(_data, name)


def memory_action(payload: dict) -> dict:
    """Forward memory mutations through the canonical Web data module."""
    _sync_dependencies()
    result = _data.memory_action(payload)
    _pull_state()
    return result


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

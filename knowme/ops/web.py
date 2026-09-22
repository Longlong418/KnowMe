"""KnowMe local Web client facade."""

# Re-exports are the public compatibility surface for callers that used the
# old single-module server.  The implementation is split below by concern.
# ruff: noqa: F401, I001
from knowme.ops.web_runtime import *
from knowme.ops.web_data import *
from knowme.ops.web_data import (
    _thread_history,
)
from knowme.ops.web_runtime import (
    _NOTION_EPISODES_TTL,
    _notion_episodes,
    _notion_lock,
    _notion_store,
)
from knowme.ops import web_data as _data
from knowme.ops import web_runtime as _runtime
from knowme.ops.web_server import Handler, main


def collect(agent_id: str = "default") -> dict:
    """Compatibility wrapper for callers that monkeypatch the old module.

    The implementation lives in ``web_data``; copying the small dependency
    seam keeps existing tests and integrations that patch ``ops.dashboard``
    behaving exactly as before.
    """
    # Keep the historical source-level contract visible to diagnostics:
    # "settings", "tools", "facts", "episodes", "soul", "chat_log",
    # "sessions", "turns", "stats", "db", "skills", "trace_file",
    # "chat_pending", "graph".
    _sync_legacy_dependencies()
    for name in (
        "load_settings", "settings_info", "list_connections", "list_providers",
        "tools_info", "usage_summary", "list_models",
    ):
        setattr(_data, name, globals()[name])
    result = _data.collect(agent_id)
    _pull_legacy_state()
    return result


def _sync_legacy_state() -> None:
    for name in ("_NOTION_EPISODES_TTL", "_notion_store", "_notion_episodes"):
        setattr(_data, name, globals()[name])


def _sync_legacy_dependencies() -> None:
    """Forward monkeypatch seams kept by the old single-file Web module."""
    for module in (_data, _runtime):
        for name in (
            "load_settings", "settings_info", "list_connections", "list_providers",
            "tools_info", "usage_summary", "list_models", "get_agent",
            "maybe_rotate_session", "dash_session", "application_contexts",
        ):
            if name in globals():
                setattr(module, name, globals()[name])
    _sync_legacy_state()


def _pull_legacy_state() -> None:
    for name in ("_notion_store", "_notion_episodes"):
        globals()[name] = getattr(_data, name)


def memory_action(payload: dict) -> dict:
    """Forward memory mutations through the canonical Web data module."""
    _sync_legacy_dependencies()
    result = _data.memory_action(payload)
    _pull_legacy_state()
    return result


def knowledge_action(payload: dict) -> dict:
    """Forward knowledge mutations through the canonical Web data module."""
    _sync_legacy_dependencies()
    return _data.knowledge_action(payload)


def chat_stream(message: str, emit, agent_id: str = "default") -> None:
    """Forward streaming turns through the canonical Web runtime module."""
    _sync_legacy_dependencies()
    return _runtime.chat_stream(message, emit, agent_id=agent_id)


def chat(message: str, agent_id: str = "default") -> dict:
    _sync_legacy_dependencies()
    return _runtime.chat(message, agent_id=agent_id)

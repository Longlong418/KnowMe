"""DETERMINISTIC EVAL — the web's public surface, pinned.

This is a characterization net, not a feature test. It exists so that moving or
deleting code inside web.py cannot silently remove a URL the browser calls
or a key the page reads. Written BEFORE the dead-code deletion and the 1,695 ->
~880 line split, and it stayed green through both without a single edit to the
route lists — which is the whole point: the browser's contract never moved, only
the code behind it.

The handler list below is checked with getattr(web, ...), so it also pins
the re-exports. After the split, most handlers live in catalog, settings_api,
and browser_agent and are imported here. If one stops being reachable
from `web`, the router breaks and this fails.

If you add a route or a payload key on purpose, update the list below in the
same commit — that edit is the review signal that the public surface changed.
"""

from __future__ import annotations

import inspect

from knowme.ops import web
from knowme.ops.web import server

# Every path the POST router accepts.
POST_ROUTES = {
    "/api/chat",
    "/api/memory",
    "/api/settings",
    "/api/query",
    "/api/session",
    "/api/pin",
    "/api/extras",
    "/api/knowledge",
}

# Paths served on GET, either exactly or as a prefix.
GET_PATHS = {
    "/api/data",
    "/api/models",
    "/api/events",
    "/api/reveal",
    "/static/",
}

# Streaming endpoints. The browser reads these as SSE, so they are handled
# before the regular POST router.
STREAM_ROUTES = {"/api/chat/stream", "/api/graph/stream"}


def _source() -> str:
    return inspect.getsource(server)


def test_every_post_route_is_still_registered():
    src = _source()
    for path in POST_ROUTES:
        assert f'"{path}"' in src, f"POST route disappeared: {path}"


def test_every_get_path_is_still_served():
    src = _source()
    for path in GET_PATHS:
        assert f'"{path}"' in src, f"GET path disappeared: {path}"


def test_streaming_routes_survive():
    """The web's live chat and graph views depend on these."""
    src = _source()
    for path in STREAM_ROUTES:
        assert f'"{path}"' in src, f"streaming route disappeared: {path}"


def test_the_handlers_behind_the_routes_exist_and_are_callable():
    for name in ("collect", "chat", "chat_stream", "graph_stream", "memory_action",
                 "apply_settings", "run_query", "session_action", "pin_action",
                 "list_models", "events_since", "reveal_path", "settings_info",
                 "tools_info"):
        fn = getattr(web, name, None)
        assert callable(fn), f"handler missing or not callable: {name}"


def test_api_models_returns_picker_contract(monkeypatch):
    """The model picker depends on /api/models returning models and listed."""
    import io
    import json
    import urllib.request

    from knowme.ops import catalog

    monkeypatch.setenv("KNOWME_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.delenv("KNOWME_MODEL", raising=False)
    monkeypatch.delenv("KNOWME_SMALL_MODEL", raising=False)

    def fake_urlopen(req, timeout=10):
        return io.BytesIO(json.dumps(
            {"data": [{"id": "vendor/model:free"}]}
        ).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    catalog._models_cache.clear()

    result = catalog.list_models("openrouter")
    assert result["listed"] is True
    assert isinstance(result["models"], list)
    assert result["models"][0]["id"] == "vendor/model:free"

    catalog._models_cache.clear()


def test_collect_returns_the_keys_the_page_reads():
    """`/api/data` is read by every view in static/js/. These are the keys the
    frontend indexes into; dropping one blanks a tab with no error."""
    expected = {
        "settings", "tools", "facts", "episodes", "soul", "chat_log", "sessions",
        "turns", "stats", "db", "skills", "trace_file", "chat_pending", "graph",
        # Who the payload is FOR: the page refuses an answer about another agent
        # (static/js/main.js refresh). Added 2026-09-22 with that check.
        "agent_id",
    }
    src = inspect.getsource(web.collect)
    for key in expected:
        assert f'"{key}"' in src, f"collect() no longer returns: {key}"


def test_arena_routes_stay_removed():
    """The simplified web must not grow stale competition endpoints."""
    src = _source()
    assert "/api/compare" not in src
    assert "/api/memory-arena" not in src

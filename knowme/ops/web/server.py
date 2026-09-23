"""HTTP transport for the local Web client."""

# ruff: noqa: F401, I001


from __future__ import annotations

import errno
import json
import os
import shutil
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from knowme.agents import get_profile, list_profiles
from knowme.applications import ApplicationContextBridge
from knowme.applications.reader import ReaderError, load_upload, load_url
from knowme.config import load_settings
from knowme.db import connect
from knowme.integrations import (
    apply_integration,
    apply_provider,
    apply_provider_disabled,
    list_connections,
    list_providers,
    test_integration,
)
from knowme.ops import browser_agent, commands
from knowme.ops.browser_agent import agent_lock, dash_session, get_agent, maybe_rotate_session
from knowme.ops.catalog import list_models
from knowme.ops.pricing import price_for, usage_summary
from knowme.ops.settings_api import apply_settings, custom_provider_action, pin_action, settings_info
from knowme.ops.tracing import TraceEncodingError, iter_trace_lines
from knowme.ops.web.uploads import resolve as upload_resolve

# Windows commonly reserves 7777 (for example through Hyper-V/WSL port
# exclusions), while 8888 is conventionally available for local web clients.
# Keep the familiar 7777 default elsewhere, but make a clean Windows checkout
PORT = 8888 if os.name == "nt" else 7777

from .runtime import *
from .data import *

class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, *, no_cache: bool = False,
              status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The frontend files (app.js/style.css) change as we develop; without
        # this the browser serves a stale cached copy and edits look "missing".
        if no_cache:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/data" or self.path.startswith("/api/data?"):
            from urllib.parse import parse_qs, urlparse

            agent_id = parse_qs(urlparse(self.path).query).get("agent_id", ["default"])[0] or "default"
            try:
                payload = collect(agent_id)
            except Exception as exc:
                # NEVER let this raise. An exception here closes the connection
                # without a byte of response, and the browser can only reduce
                # that to "TypeError: Failed to fetch" — which is how a bug
                # inside collect() (a tool result that was not a string) looked
                # like a network fault for as long as the bad trace row existed.
                # An error body is something the page can name and show.
                payload = {"error": f"{type(exc).__name__}: {exc}"}
            self._send(json.dumps(payload, default=str).encode(), "application/json")
        elif self.path == "/api/agents":
            live = browser_agent.current_agents()
            payload = [
                {**profile.to_public(),
                 "status": "ready" if profile.id in live else "idle",
                 "session_id": (
                     live[profile.id].session.session_id
                     if profile.id in live else dash_session(profile.id)
                 )}
                for profile in list_profiles()
            ]
            self._send(json.dumps(payload).encode(), "application/json")
        elif self.path.startswith("/api/models"):
            from urllib.parse import parse_qs, urlparse

            prov = parse_qs(urlparse(self.path).query).get("provider", [None])[0]
            self._send(json.dumps(list_models(prov)).encode(), "application/json")
        elif self.path.startswith("/api/events"):
            from urllib.parse import parse_qs, urlparse

            raw = parse_qs(urlparse(self.path).query).get("cursor", [None])[0]
            cursor = int(raw) if raw and raw.lstrip("-").isdigit() else None
            self._send(json.dumps(events_since(cursor)).encode(), "application/json")
        elif self.path.startswith("/api/workspace"):
            from urllib.parse import parse_qs, urlparse

            relative = parse_qs(urlparse(self.path).query).get("path", [""])[0]
            self._send(json.dumps(workspace_info(relative or None), default=str).encode(),
                       "application/json")
        elif self.path.startswith("/api/reveal"):
            from urllib.parse import parse_qs, unquote, urlparse

            rel = unquote(parse_qs(urlparse(self.path).query).get("path", [""])[0])
            self._send(json.dumps(reveal_path(rel)).encode(), "application/json")
        elif self.path.startswith("/api/library/file"):
            # The ORIGINAL bytes of a library document. pdf.js needs the real
            # file — our extracted text has no figures and no layout — so the
            # browser fetches it here rather than re-uploading it.
            from urllib.parse import parse_qs, urlparse

            from knowme.db import connect

            doc_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            settings = load_settings()
            settings.ensure_home()
            body, _kind, suffix = library_file(connect(settings.home), settings.home, doc_id)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            ctype = {".pdf": "application/pdf", ".html": "text/html; charset=utf-8",
                     ".htm": "text/html; charset=utf-8", ".md": "text/plain; charset=utf-8"
                     }.get(suffix, "text/plain; charset=utf-8")
            self._send(body, ctype, no_cache=True)
        elif self.path.startswith("/api/uploads/"):
            # A picture attached to a past turn. The conversation stores the
            # reference, the file lives under <home>/uploads/ — see uploads.py.
            from urllib.parse import unquote

            settings = load_settings()
            settings.ensure_home()
            name = unquote(self.path.split("/api/uploads/", 1)[1].split("?")[0])
            path = upload_resolve(settings.home, name)
            if path is None:
                self.send_response(404)
                self.end_headers()
                return
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
                     ".webp": "image/webp"}.get(path.suffix, "application/octet-stream")
            self._send(path.read_bytes(), ctype)
        elif self.path.startswith("/static/"):
            self._serve_static(self.path)
        else:
            self._send((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")

    def _serve_static(self, path: str) -> None:  # the frontend files
        name = path.split("/static/", 1)[1].split("?")[0]
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        # The MIME type has to be right, not just present: a browser refuses to
        # EXECUTE a module served as application/octet-stream, so `.mjs` (the
        # vendored pdf.js build) fails with a console error and no pdf at all.
        # `.wasm` and the fonts are the same class of requirement.
        ctype = {".css": "text/css", ".js": "text/javascript",
                 ".mjs": "text/javascript",           # pdf.js ships ESM
                 ".svg": "image/svg+xml",
                 ".html": "text/html; charset=utf-8",
                 ".wasm": "application/wasm",         # JBIG2/JPEG2000 decoders
                 ".ttf": "font/ttf", ".pfb": "application/octet-stream",
                 ".json": "application/json",
                 }.get(target.suffix, "application/octet-stream")
        self._send(target.read_bytes(), ctype, no_cache=True)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send(
                json.dumps({"ok": False, "error": f"请求 JSON 无效：{exc}"}).encode(),
                "application/json", status=400,
            )
            return

        # /api/chat/stream streams harness events (SSE) as the turn runs.
        if self.path == "/api/chat/stream":
            message = (payload.get("message") or "").strip()
            agent_id = payload.get("agent_id") or "default"
            images = payload.get("images") or []
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the browser navigated away mid-stream — fine

            # A picture with no words is a complete question ("what is this?"),
            # so the empty check is on the turn, not on the text.
            if not message and not images:
                emit("done", {"error": "empty message"})
                return
            try:
                chat_stream(message, emit, agent_id=agent_id, images=images)
            except Exception as exc:  # surface as a terminal event, don't 500
                emit("done", {"error": f"{type(exc).__name__}: {exc}"})
            return
        if self.path == "/api/graph/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind, ev):
                try:
                    self.wfile.write(f"data: {json.dumps({'kind': kind, **ev}, default=str)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            graph_stream(payload, emit)
            return
        routes = {"/api/chat": None, "/api/memory": memory_action, "/api/settings": apply_settings,
                  "/api/query": run_query, "/api/session": session_action, "/api/pin": pin_action,
                  "/api/knowledge": knowledge_action,
                  "/api/library": library_action,
                  "/api/workspace": workspace_action,
                  "/api/reader": None,
                  "/api/connections": None, "/api/connections/test": None,
                  "/api/providers": None, "/api/extras": extras_action}
        if self.path not in routes:
            self._send(
                json.dumps({"ok": False, "error": f"没有这个 API：{self.path}"}).encode(),
                "application/json", status=404,
            )
            return
        try:
            if self.path == "/api/chat":
                message = (payload.get("message") or "").strip()
                images = payload.get("images") or []
                out = chat(message, payload.get("agent_id") or "default", images=images) \
                    if (message or images) else {"error": "empty message"}
            elif self.path == "/api/extras":
                out = extras_action(payload)
            elif self.path == "/api/reader":
                if payload.get("action") == "url":
                    out = {"ok": True, "document": load_url(str(payload.get("url", "")).strip())}
                elif payload.get("action") == "upload":
                    out = {"ok": True, "document": load_upload(str(payload.get("name", "")), str(payload.get("data", "")))}
                else:
                    raise ReaderError("Reader action 必须是 url 或 upload")
            elif self.path == "/api/connections":
                result = apply_integration(payload.get("key", ""), payload.get("values") or {},
                                           tuple(payload.get("clear") or ()), force=bool(payload.get("force")))
                if result.ok and payload.get("key") == "notion":
                    invalidate_notion_cache()
                out = asdict(result)
            elif self.path == "/api/connections/test":
                out = asdict(test_integration(payload.get("key", "")))
            elif self.path == "/api/providers":
                # A payload that only toggles availability goes to the enable/
                # disable path; the ＋ card's add/remove goes to its own action;
                # everything else is the existing provider apply.
                if payload.get("action") in ("add_custom", "remove_custom", "probe_models"):
                    out = custom_provider_action(payload)
                elif "disabled" in payload and set(payload) <= {"provider", "disabled"}:
                    out = asdict(apply_provider_disabled(payload.get("provider", ""),
                                                         disabled=bool(payload["disabled"])))
                else:
                    out = asdict(apply_provider(**payload))
            else:
                out = routes[self.path](payload)
        except Exception as exc:  # surface, don't 500 — the browser shows it
            out = {"error": f"{type(exc).__name__}: {exc}"}
        self._send(json.dumps(out, default=str).encode(), "application/json")

    def log_message(self, *args):  # keep the terminal quiet
        pass


def main() -> None:
    # Port precedence: KNOWME_WEB_PORT, then the conventional PORT (used by
    # deploy platforms and IDE preview panes), then the platform default. If it
    # is unavailable, walk on rather than failing the web startup.
    base = int(os.getenv("KNOWME_WEB_PORT") or os.getenv("KNOWME_DASHBOARD_PORT") or os.getenv("PORT") or PORT)
    for port in range(base, base + 10):  # walk past a busy port instead of crashing
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError as exc:
            # A Windows port exclusion raises WSAEACCES/10013, not
            # WSAEADDRINUSE/10048. Calling both "busy" sent people looking for
            # a phantom process, even though no process owned the port.
            if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
                reason = "already in use"
            elif exc.errno == errno.EACCES or getattr(exc, "winerror", None) == 10013:
                reason = "reserved by the system or access is denied"
            else:
                reason = f"unavailable ({exc})"
            print(f"port {port} {reason}, trying {port + 1}…")
            continue
        print(f"KnowMe web → http://localhost:{port}  (Ctrl-C to stop)")
        server.serve_forever()
        return
    raise SystemExit(
        f"no usable local port in {base}–{base + 9}; "
        "set KNOWME_WEB_PORT to an available port and try again"
    )


if __name__ == "__main__":
    main()

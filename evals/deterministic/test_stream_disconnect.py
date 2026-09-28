"""DETERMINISTIC EVAL — a client that vanishes mid-turn must not take the turn down.

The user presses F5, closes the tab, or navigates away while a turn is streaming.
What the server sees is a write that fails. That failed write must NOT travel
upwards: reaching the code that is running the turn means the turn dies inside
`notify()`, the tokens already paid for are gone, and nothing lands in chat_log,
the knowledge base or outbox — so "my reply disappeared when I refreshed" is
literally true.

The exception CLASS is platform-dependent, which is what made this a live bug on
a Windows checkout: Linux raises BrokenPipeError/ConnectionResetError, Windows
raises ConnectionAbortedError ([WinError 10053]). Catching only the Linux pair
reads as correct in review and works on the machine that wrote it.

Found with .claude/probe_disconnect.py (real server, real socket, fake model):
before the fix chat_log stayed empty and the research run never reached graph_end.
These tests are that finding distilled offline — the real Handler, the real
`emit`, and a socket that dies on cue.
"""

from __future__ import annotations

import io
import json

import pytest

from knowme.graph import END, START, Graph, Node
from knowme.ops import web
from knowme.ops.web import server


class _DeadConnection:
    """A socket whose writes stop working after `fail_after` of them: the browser
    read the headers and then went away.

    sendall(), not write(): with wbufsize 0 (what http.server uses) socketserver
    wraps the connection in a _SocketWriter and every response byte goes through
    here, so this is where the platform's exception has to come from."""

    def __init__(self, raw: bytes, fail_after: int, exc: type[Exception]):
        self.raw = raw
        self.fail_after = fail_after
        self.exc = exc
        self.writes = 0

    def sendall(self, data):
        self.writes += 1
        if self.writes > self.fail_after:
            raise self.exc(10053, "你的主机中的软件中止了一个已建立的连接。")

    def makefile(self, mode, *args, **kwargs):
        return io.BytesIO(self.raw)

    def settimeout(self, _):
        pass

    def close(self):
        pass


def _post(path: str, payload: dict, fail_after: int,
          exc: type[Exception] = ConnectionAbortedError) -> None:
    """Drive the REAL handler — constructing it IS running the request
    (BaseHTTPRequestHandler.__init__ -> handle -> handle_one_request -> do_POST),
    and nothing swallows an exception on the way out, which is what lets these
    tests fail the way the bug did.

    `fail_after` counts writes including the header block, which end_headers()
    sends in one — so 1 makes the FIRST streamed frame the one that meets the
    dead socket."""
    body = json.dumps(payload).encode()
    raw = (f"POST {path} HTTP/1.0\r\nContent-Type: application/json\r\n"
           f"Content-Length: {len(body)}\r\n\r\n").encode() + body
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    try:
        server.Handler(_DeadConnection(raw, fail_after, exc), ("127.0.0.1", 0), srv)
    finally:
        srv.server_close()


@pytest.fixture
def scripted(monkeypatch):
    """A tiny scripted graph behind the real graph_stream — same engine, same
    events, no model and no network (the trick from test_graph_stream.py). The
    runner table maps a name to "module:function" and resolves it per call, so
    patching the module attribute is enough."""

    def fake_run(observer=None, **kw):
        g = Graph("gather")
        g.add_node(Node("a", lambda s: {"x": 1}, kind="tool"))
        g.add_node(Node("b", lambda s: {"digest": "D"}, kind="llm"))
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        from knowme.graph import run_graph

        return run_graph(g, {}, observer=observer)

    import knowme.ops.gather as gather_mod

    monkeypatch.setattr(gather_mod, "run_gather", fake_run)
    return fake_run


def test_a_workflow_run_survives_the_client_going_away(scripted, monkeypatch):
    """The graph path has no catch-all around graph_stream, so the exception
    came straight out of the engine's own notify() and the run died without a
    graph_end — measured with the probe, not theorised from the code."""
    seen: list[str] = []
    real = web.graph_stream

    def spy(payload, emit):
        # The handler's own emit and the real runner: the only fake thing here
        # is the socket.
        real(payload, lambda kind, ev: (seen.append(kind), emit(kind, ev))[1])

    monkeypatch.setattr(server, "graph_stream", spy)
    _post("/api/graph/stream", {"workflow": "gather"}, fail_after=1)

    # Every frame after the first met the dead socket and was dropped; the run
    # still finished. That is the point — the work has to survive, the stream is
    # only how you were watching it.
    assert seen.count("node_end") == 2 and seen[-1] == "done", seen


def test_a_chat_turn_survives_the_client_going_away(monkeypatch):
    """The turn runs inside `with agent_lock`, and the reply is written to
    chat_log after it — so a raise here also means the row that makes a reload
    show the answer never happens."""
    done: list[dict] = []

    def stub(message, emit, agent_id="default", images=None):
        emit("gate", {"decision": "full"})       # dies here
        emit("done", {"reply": "还是写完了"})
        done.append({"message": message, "agent_id": agent_id})

    monkeypatch.setattr(server, "chat_stream", stub)
    _post("/api/chat/stream", {"message": "你好", "agent_id": "default"}, fail_after=1)

    assert done == [{"message": "你好", "agent_id": "default"}], "这一轮被断线打断了"


@pytest.mark.parametrize("exc", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
def test_every_way_a_socket_dies_is_swallowed(exc, monkeypatch):
    """The fix widened the catch to ConnectionError. It must not have narrowed it
    to the Windows name — the other two are what a macOS/Linux checkout raises
    for the very same situation."""
    seen: list[str] = []

    def stub(message, emit, agent_id="default", images=None):
        emit("gate", {})                          # the write that meets `exc`
        emit("done", {"reply": "ok"})
        seen.append("finished")

    monkeypatch.setattr(server, "chat_stream", stub)
    _post("/api/chat/stream", {"message": "你好"}, fail_after=1, exc=exc)

    assert seen == ["finished"], f"{exc.__name__} 没被吞掉"

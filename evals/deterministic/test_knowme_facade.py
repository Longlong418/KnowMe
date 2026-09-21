"""KnowMe is a facade — a turn through it leaves the same footprint it always did.

chat_log rows, the meta keys on the assistant row, and the trace event sequence
for one scripted turn were recorded on the pre-refactor code; this pins them so
the AgentRuntime extraction cannot quietly change what a turn writes.
"""

from __future__ import annotations

import json

from evals.helpers import ScriptedClient, make_knowme, response, text_block, tool_block


def _gate(retrieve=False):
    return response([text_block(json.dumps({"retrieve": retrieve, "query": "", "reason": "t"}))])


def test_one_tool_turn_footprint(tmp_path):
    turn = [
        response([tool_block("save_note", {"subject": "raj", "content": "likes tennis"})],
                 stop_reason="tool_use"),
        response([text_block("Noted.")]),
    ]
    app = make_knowme(tmp_path / "home", client=ScriptedClient([_gate()] + turn))
    result = app.respond("remember raj likes tennis", source="cli")

    assert result.reply == "Noted."
    assert result.iterations == 2
    assert [c["tool"] for c in result.tool_calls] == ["save_note"]

    rows = app.conn.execute("SELECT role, meta FROM chat_log ORDER BY id").fetchall()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    meta = json.loads(rows[1]["meta"])
    assert set(meta) == {"gate", "graph", "iterations", "latency_ms", "tools",
                         "steps", "model", "provider"}
    assert meta["gate"] == {"decision": "skip", "reason": "t"}
    assert meta["graph"] is None
    assert meta["tools"] == [{"tool": "save_note", "status": "ok"}]
    assert meta["model"] == app.settings.model
    # the persisted timeline mirrors the trace order below: gate, then per
    # iteration one llm step and its tool step, then the closing llm
    assert [s["kind"] for s in meta["steps"]] == ["gate", "llm", "tool", "llm"]

    kinds = [json.loads(line)["type"] for line in app.tracer.path.read_text(encoding="utf-8").splitlines()]
    assert kinds == ["turn_start", "gate", "llm", "tool", "llm", "turn_end"]

    # working memory folded the tool activity into the assistant entry
    assert "[tools used]" in app.session.history[1]["content"]
    assert (app.settings.home / "MEMORY.md").exists()


def test_facade_exposes_the_runtime_and_default_spec(tmp_path):
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    assert app.spec.name == "default"
    assert app.runtime.tools is app.tools and app.runtime.memory is app.memory
    for attr in ("session", "settings", "conn", "memory", "tools", "client", "tracer", "mcp_bridge"):
        assert hasattr(app, attr), attr

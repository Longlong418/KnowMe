"""DETERMINISTIC EVAL — per-turn telemetry is persisted and reloadable.

Sean's catch: reopening a chat thread dropped the gate decision, seconds, and
iterations because they were only ever computed live, never saved. Now the
assistant row carries a meta JSON so a restored thread renders the full card."""

from __future__ import annotations

import json

from evals.helpers import ScriptedClient, make_knowme, response, text_block, tool_block


def test_turn_meta_is_saved_with_gate_and_iterations(tmp_path):
    gate = response([text_block('{"retrieve": true, "query": "alex", "reason": "asks about alex"}')])
    turn = [
        response([tool_block("save_note", {"subject": "alex", "content": "likes mornings"})], "tool_use"),
        response([text_block("Noted.")]),
    ]
    app = make_knowme(tmp_path / "home", client=ScriptedClient([gate] + turn))
    app.respond("remember alex likes mornings")

    row = app.conn.execute(
        "SELECT meta FROM chat_log WHERE role='assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    meta = json.loads(row["meta"])
    assert meta["gate"]["decision"] == "retrieve"
    assert meta["iterations"] == 2                    # tool turn + final answer
    assert isinstance(meta["latency_ms"], int)
    assert [t["tool"] for t in meta["tools"]] == ["save_note"]
    # which brain answered — shown per card, survives a reopened thread
    assert meta["model"] == app.settings.model
    assert meta["provider"] == app.settings.provider
    # the turn timeline: gate fires during context assembly (before the loop),
    # then each iteration contributes its llm call and its tool
    steps = meta["steps"]
    assert [s["kind"] for s in steps] == ["gate", "llm", "tool", "llm"]
    assert steps[0]["label"] == "retrieve"
    assert steps[1]["label"].startswith("iter 1 ·")
    assert steps[2]["label"] == "save_note"
    assert steps[2]["status"] == "ok"
    assert all(isinstance(s["ms"], int) for s in steps)


def test_steps_are_bounded_and_use_known_kinds(tmp_path):
    """meta.steps is shape-stable: every kind is from the closed set the
    frontend knows, every step has exactly the five keys the renderer reads,
    and no turn can outgrow the cap — the graph can fan out harder than the
    loop ever will (a plain loop turn tops out at ~2·max_iterations steps)."""
    from knowme.core.runtime import MAX_STEPS, STEP_KINDS, record_step

    gate = response([text_block('{"retrieve": false, "query": "", "reason": "math"}')])
    app = make_knowme(tmp_path / "home",
                      client=ScriptedClient([gate, response([text_block("4")])]))
    app.respond("what is 2+2?")
    row = app.conn.execute(
        "SELECT meta FROM chat_log WHERE role='assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    steps = json.loads(row["meta"])["steps"]
    assert all(s["kind"] in STEP_KINDS for s in steps)
    assert all(set(s) == {"kind", "label", "ms", "detail", "status"} for s in steps)
    assert [s["kind"] for s in steps] == ["gate", "llm"]   # skipped retrieval, one pass

    # the bounds, driven straight at the recorder: 100 events stay 40, and a
    # wall of text comes out truncated on both fields
    many: list = []
    big = "x" * 5000
    for i in range(100):
        record_step(many, 0.0, "tool", f"tool_{i} {big}", big, ms=i)
    assert len(many) == MAX_STEPS
    assert many[-1]["label"].startswith("tool_39")   # the cap STOPS appending:
    assert many[0]["label"].startswith("tool_0")     # the first 40 are kept
    assert len(many[-1]["label"]) <= 80
    assert len(many[-1]["detail"]) <= 400
    assert many[-1]["ms"] == 39    # a self-measured event keeps its own clock
    # a list detail joins into one readable line
    joined: list = []
    record_step(joined, 0.0, "node", "n", ["wrote a", "wrote b"], ms=5)
    assert joined[0]["detail"] == "wrote a, wrote b"



def test_no_tool_turn_still_saves_meta(tmp_path):
    gate = response([text_block('{"retrieve": false, "query": "", "reason": "math"}')])
    app = make_knowme(tmp_path / "home", client=ScriptedClient([gate, response([text_block("4")])]))
    app.respond("what is 2+2?")
    row = app.conn.execute(
        "SELECT meta FROM chat_log WHERE role='assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    meta = json.loads(row["meta"])
    assert meta["gate"]["decision"] == "skip"
    assert meta["tools"] == []


def test_old_rows_without_meta_are_tolerated(tmp_path):
    """A row written before meta existed (NULL) must not break anything."""
    app = make_knowme(tmp_path / "home", client=ScriptedClient([]))
    app.memory.log_chat("hi", "hello", session_id="s1", source="cli", meta=None)
    row = app.conn.execute(
        "SELECT meta FROM chat_log WHERE role='assistant'"
    ).fetchone()
    assert row["meta"] is None

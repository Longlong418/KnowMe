"""AgentRuntime — one turn, for any agent.

This is what KnowMe.respond() and _run_full_turn() were, with the agent made a
parameter. The runtime owns nothing agent-specific: it is handed a client, a
connection, a memory, a tool registry and a tracer once, and then runs any
(AgentSpec, Session, message) through the same steps every time:

    run_turn
      ├─ front_door(message)          optional — the triage graph, fail-open
      └─ run_loop_turn
           ├─ session.build_system(spec.persona, spec.model)     stable
           ├─ session.build_turn_context(message, extra=...)     volatile
           ├─ spec.context_policy.fit(history)                   fit the window
           └─ run_loop(...)                                      THE loop
      ├─ meta: gate / graph / iterations / latency / tools / steps / model
      ├─ session.add_exchange   memory.maybe_consolidate   memory.export_markdown
      └─ tracer.end_turn

Two seams exist for what comes next and are unused by the default agent:

  extra_context   text an Application contributes to this turn — the open
                  document, the selection (the Application Context Bridge)
  front_door      a callable that may answer the turn without the loop; None
                  from it means "not mine", and the loop runs as usual

The window size comes in as a function (`context_for`) rather than an import,
because it lives in knowme.ops.pricing and the core may not import ops.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from knowme.config import Settings
from knowme.core.context.policy import FitContext
from knowme.core.events import Observer, compose
from knowme.core.loop import LoopResult, run_loop
from knowme.core.models import Roles
from knowme.core.session import Session
from knowme.core.spec import AgentSpec, resolve
from knowme.core.tools import ToolRegistry

FrontDoor = Callable[[str, Observer, bool], "LoopResult | None"]
ContextFor = Callable[[str, str], int]   # (provider, model) → window in tokens


def _image_block(image: dict) -> dict:
    """One attached picture as a content block, in ANTHROPIC's shape.

    Every message this runtime builds is Anthropic-shaped — the OpenAI-speaking
    providers are bridged in core/models.py:_to_openai — so this is the only
    place that has to spell out an image. Getting that wrong here would show up
    as a provider 400 that names a field nobody wrote.
    """
    return {"type": "image",
            "source": {"type": "base64", "media_type": image["mime"],
                       "data": image["data"]}}

# meta.steps is a bounded transcript of WHAT happened during a turn, in order —
# the timeline the conversation view draws from. It cannot grow with the turn:
# a 40-step cap and truncated strings keep a chat_log row small no matter how
# wild the loop got, and the kinds below are a closed set the frontend knows.
STEP_KINDS = frozenset({"gate", "context", "route", "graph", "node",
                        "llm", "tool", "consolidation"})
MAX_STEPS = 40
_STEP_LABEL_MAX = 80
_STEP_DETAIL_MAX = 400

# 轨迹是给用户看的，所以这里存的每一句都必须是中文人话。
# 这些表就是全部说法，前端 js/trace.js 里有一份一模一样的（render.js 直播时
# 自己拼一遍，因为服务端的那份要等这一轮跑完才到）——改文案要两边一起改。
_STOP_REASON_ZH = {
    "end_turn": "直接回答",
    "tool_use": "要调用工具",
    "max_tokens": "到达输出上限，回复被截断",
    "stop_sequence": "遇到停止词",
    "refusal": "模型拒绝回答",
    "pause_turn": "暂停，等待继续",
}
_GATE_ZH = {"retrieve": "要查记忆", "skip": "不用查记忆"}
_TARGET_ZH = {"quick_reply": "直接回答", "quick": "直接回答", "full": "完整流程"}
_COMPACT_ZH = {
    "tool_budget": "先说清工具结果在哪",
    "micro_compact": "旧的工具结果收成一行指针",
    "state_summary": "整段对话压成摘要",
}


def _zh(mapping: dict, key) -> str:
    """表里有就用表里的，没有就原样输出 —— 供应商多了一个新 stop_reason 时，
    用户看到的应该是一个陌生的英文词，而不是一句「未知」。"""
    return mapping.get(str(key), str(key or ""))


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    iterations: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def as_loop_result(self) -> LoopResult:
        """The same turn, in the shape callers of the plain loop already get.

        `meta` MUST come along. This is the only door out of the runtime for a
        normal turn, and the dashboard renders its timeline from meta.steps —
        so dropping it here broke every turn with `'LoopResult' object has no
        attribute 'meta'`, while the stored history (which has meta in SQLite)
        kept rendering fine and made it look like a frontend problem.
        """
        return LoopResult(reply=self.reply, tool_calls=self.tool_calls,
                          iterations=self.iterations, meta=self.meta)


def _status(output: str) -> str:
    low = (output or "").lower()
    return "error" if ("failed" in low or "timed out" in low or low.startswith("error")) else "ok"


def _merged(first: LoopResult, second: LoopResult) -> LoopResult:
    """Two rounds of one turn, as one result.

    The verification retry IS this turn — it happened because of this message,
    and the text the user reads is the second round's — so the tool calls and
    the iteration count are added up rather than dropped, and `meta.iterations`
    tells the truth about a turn that really did run twice. The first reply is
    thrown away on purpose: it is the round that failed verification.
    """
    return LoopResult(reply=second.reply,
                      tool_calls=[*first.tool_calls, *second.tool_calls],
                      iterations=first.iterations + second.iterations)


def record_step(steps: list, t0: float, kind: str, label: str, detail,
                ms: int | None = None, status: str = "ok") -> None:
    """Append one timeline step (module-level so the bounds are testable — a
    real turn can only reach ~2·max_iterations steps, never the cap).

    `detail` may be a list of facts; it joins into one line. Arrival time
    within the turn becomes the step's ms unless the event measured itself.
    """
    if len(steps) >= MAX_STEPS:
        return
    if isinstance(detail, (list, tuple)):
        detail = ", ".join(str(x) for x in detail)
    steps.append({
        "kind": kind,
        "label": str(label)[:_STEP_LABEL_MAX],
        "ms": ms if ms is not None else int((time.perf_counter() - t0) * 1000),
        "detail": str(detail)[:_STEP_DETAIL_MAX],
        "status": status,
    })


class AgentRuntime:
    def __init__(self, settings: Settings, *, client, conn, memory, tools: ToolRegistry,
                 tracer, context_for: ContextFor, roles: Roles | None = None):
        self.settings = settings
        self.client = client
        self.conn = conn
        self.memory = memory
        self.tools = tools
        self.tracer = tracer
        self.context_for = context_for
        # Only the summariser role is used here (see the FitContext below). Not
        # given one, it stays what it always was: this client, this small model.
        self.roles = roles or Roles(client, settings.small_model, client, settings.small_model)

    def run_turn(self, spec: AgentSpec, session: Session, user_message: str, *,
                 observer: Observer | None = None, source: str = "cli",
                 stream: bool = False, extra_context: str = "",
                 front_door: FrontDoor | None = None,
                 images: list[dict] | None = None) -> TurnResult:
        """One full turn: assemble working memory → run the loop → persist.
        `source` tags whether the message arrived through the CLI or web client,
        so the unified chat can show its origin. `stream=True` streams the reply
        text token by token to the observer. Everything that happens is both
        shown (observer) and recorded (tracer).

        `images` are pictures attached to THIS turn (already saved to disk — see
        ops/web/uploads.py). They reach the model on the request's user message
        and are recorded in the chat log by reference; they do not enter
        session.history."""
        resolved = resolve(spec, self.settings)
        t0 = time.perf_counter()
        # capture the gate + graph decisions as they flow by, so we can persist
        # them with the turn (the reopened-thread telemetry the dashboard shows),
        # and append a bounded step per event for the turn timeline.
        captured: dict = {}
        steps: list[dict] = []

        def _step(kind: str, label: str, ev: dict, ms: int | None = None,
                  status: str = "ok"):
            record_step(steps, t0, kind, label, ev.get("_detail", ""), ms, status)

        def _capture(kind, ev):
            if kind == "context":
                captured["context"] = {
                    "application_chars": ev.get("application_chars", 0),
                    "history_messages": ev.get("history_messages", 0),
                    "sent_messages": ev.get("sent_messages", 0),
                    "compaction": ev.get("compaction", []),
                }
                _step("context",
                      f"历史 {ev.get('history_messages', 0)} 条 → 送出 "
                      f"{ev.get('sent_messages', 0)} 条",
                      {"_detail": "；".join(
                          [f"附加上下文 {ev.get('application_chars', 0)} 字",
                           *[f"压缩：{_zh(_COMPACT_ZH, c)}"
                             for c in ev.get("compaction") or []]])})
            if kind == "gate":
                captured["gate"] = {"decision": ev.get("decision"), "reason": ev.get("reason")}
                _step("gate", _zh(_GATE_ZH, ev.get("decision")),
                      {"_detail": ev.get("reason") or ""})
            if kind == "route":
                captured["graph_route"] = {"target": ev.get("target"), "reason": ev.get("reason")}
                _step("route",
                      f"{ev.get('workflow') or 'graph'} → {_zh(_TARGET_ZH, ev.get('target'))}",
                      {"_detail": ev.get("reason") or ""})
            if kind == "triage":
                captured["triage_reason"] = ev.get("reason")
            if kind == "graph_end":
                captured["graph_path"] = ev.get("path")
                _step("graph",
                      f"工作流 {ev.get('workflow')} · {' → '.join(ev.get('path') or [])}",
                      {"_detail": "；".join(
                          [f"共 {ev.get('steps')} 步",
                           *([f"出错：{ev['error']}"] if ev.get("error") else [])])},
                      ms=ev.get("ms"))
            if kind == "node_end":
                detail = [f"写入了 {k}" for k in ev.get("keys") or []]
                if ev.get("error"):
                    detail.append(f"出错：{ev.get('error')}")
                _step("node", str(ev.get("node") or ""), {"_detail": "；".join(detail)},
                      ms=ev.get("ms"), status="error" if ev.get("error") else "ok")
            if kind == "llm":
                usage = ev.get("usage") or {}
                _step("llm",
                      f"第 {ev.get('iteration')} 轮 · {_zh(_STOP_REASON_ZH, ev.get('stop_reason'))}",
                      {"_detail": f"输入 {usage.get('in', '?')} tokens，"
                                  f"输出 {usage.get('out', '?')} tokens"})
            if kind == "tool":
                _step("tool", str(ev.get("tool") or ""),
                      {"_detail": ev.get("output") or ""},
                      status=_status(ev.get("output")))
            if kind == "consolidation":
                _step("consolidation", f"新增 {ev.get('new_facts', 0)} 条记忆", {})
        notify = compose(observer, self.tracer.event, _capture)
        # turn_start/end are already written by Tracer.turn/end_turn.  Send
        # them only to the live observer + capture path here, otherwise each
        # boundary appears twice in JSONL and splits one run into two cards.
        live_notify = compose(observer, _capture)

        identity = {
            "agent_id": resolved.name,
            "session_id": session.session_id,
            "model": resolved.model,
            "source": source,
        }
        with self.tracer.turn(user_message, **identity):
            live_notify("turn_start", identity)
            # The front door is optional and can NEVER make a turn worse: none
            # → this is exactly the plain path; one → it may answer, and any
            # failure anywhere falls open to the loop below (same fail-open
            # rule as the retrieval gate).
            result = None
            if front_door is not None:
                try:
                    result = front_door(user_message, notify, stream)
                except Exception as exc:
                    notify("graph_end", {"workflow": "triage", "ms": 0, "steps": 0,
                                         "path": [], "error": repr(exc)})
                    result = None
            if result is None:
                result = self.run_loop_turn(spec, session, user_message, notify, stream,
                                            extra_context=extra_context, images=images)

            # 验收：这一轮真动过项目，就跑一遍项目的验收命令；没过就把它交回
            # 模型，只再修一轮。放这一个位置是有意的 —— front_door 和普通循环
            # （graph 的 full_agent 节点就是同一个 run_loop_turn，LoopResult 原样
            # 带回来）两条路都从这里出去，写两处迟早分歧。
            # 导入写在函数里：core 不能在导入期依赖 applications。
            from knowme.applications import coding_verify

            if coding_verify.should_verify(result.tool_calls):
                def _verify_step(outcome):
                    """验收那一步进轨迹。`status` 必须**按关键字**传：describe() 的
                    第三个值在 record_step 里对应的位置是 ms，拆开传就会把一个
                    "error" 塞进时长里，红的验收在轨迹上还是绿的。"""
                    label, detail, status = coding_verify.describe(outcome)
                    record_step(steps, t0, "tool", label, detail, status=status)

                outcome = coding_verify.run(self.settings, self.conn,
                                            session=session, agent_id=resolved.name)
                _verify_step(outcome)
                # 修不动就把红的留在收据里让人决定，而不是一直烧 token 到「绿」
                # 为止（那往往意味着它去改测试的断言了）。
                for _ in range(coding_verify.MAX_RETRIES):
                    if not outcome["ran"] or outcome["ok"]:
                        break
                    retry = self.run_loop_turn(
                        spec, session, coding_verify.retry_message(outcome),
                        notify, stream, extra_context="", images=None)
                    result = _merged(result, retry)
                    outcome = coding_verify.run(self.settings, self.conn,
                                                session=session, agent_id=resolved.name)
                    _verify_step(outcome)

            quick = captured.get("graph_route", {}).get("target") == "quick_reply"
            meta = {
                "gate": captured.get("gate"),
                "graph": ({"workflow": "triage",
                           "route": "quick" if quick else "full",
                           "reason": captured.get("triage_reason", ""),
                           "path": captured.get("graph_path")}
                          if "graph_route" in captured else None),
                "iterations": result.iterations,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "tools": [{"tool": c["tool"], "status": _status(c["output"])}
                          for c in result.tool_calls],
                # the ordered, bounded timeline the conversation view draws —
                # same shape the live SSE path builds in the browser.
                "steps": steps,
                # which brain answered this turn — so a reopened thread (or a
                # thread you switched models mid-way) shows it per card. A quick
                # graph turn was answered by the small model; say so honestly.
                "model": resolved.small_model if quick else resolved.model,
                "provider": self.settings.provider,
            }
            context_meta = captured.get("context")
            # Preserve the historical meta shape for ordinary chat turns.  A
            # context record is persisted only when something noteworthy
            # happened (Application injection or compaction); the live event is
            # still emitted on every turn.
            if context_meta and (
                context_meta["application_chars"] or context_meta["compaction"]
            ):
                meta["context"] = context_meta
            session.add_exchange(user_message, result.reply, tool_calls=result.tool_calls,
                                 source=source, meta=meta, images=images)
            if self.memory is not None:
                self.memory.maybe_consolidate(notify=notify)
                self.memory.export_markdown()   # keep MEMORY.md in sync

            end_event = {
                **identity,
                "iterations": result.iterations,
                "latency_ms": meta["latency_ms"],
                "tools": meta["tools"],
            }
            live_notify("turn_end", end_event)

        self.tracer.end_turn(result.reply, result.iterations, **identity,
                             latency_ms=meta["latency_ms"], tools=meta["tools"])
        return TurnResult(reply=result.reply, tool_calls=result.tool_calls,
                          iterations=result.iterations, meta=meta)

    def run_loop_turn(self, spec: AgentSpec, session: Session, user_message: str,
                      notify: Observer, stream: bool, extra_context: str = "",
                      images: list[dict] | None = None) -> LoopResult:
        """The classic turn: assemble working memory, run THE loop. The graph's
        full_agent node calls this SAME method, so loop-as-a-node can never
        drift from loop-as-default."""
        resolved = resolve(spec, self.settings)
        system = session.build_system(resolved.system_prompt, resolved.model)
        # Everything per-turn rides WITH the user's message, never in the system
        # prompt: the clock, the gated retrieval, the app's context. The system
        # prompt therefore stays byte-identical turn over turn, so a provider's
        # prefix cache can hold onto it — prompt caching is a prefix match, and
        # a per-turn change anywhere in the prefix re-bills everything after it.
        #
        # Only the PROMPT gets the context block; `history` keeps the bare
        # message (see add_exchange), so context never accumulates across turns.
        context = session.build_turn_context(user_message, notify=notify, extra=extra_context)
        prompt = f"{context}\n\n{user_message}"

        # COMPRESSION HAPPENS HERE, on a copy, on the way to the model — never in
        # the stored conversation. session.history and chat_log hold the complete
        # record, because that is what the person reading the dashboard is owed;
        # these rewrites exist only so the request fits. The chain and its
        # trigger rule are the spec's context_policy (core/context/policy.py).
        fit = resolved.context_policy.fit(session.history, FitContext(
            system=system, prompt=prompt, full_history=session.history,
            home=self.settings.home, conn=self.conn, session_id=session.session_id,
            # The summariser's own client and model. These two fields mean
            # exactly that — they are just filled from the summary ROLE now
            # instead of always being the loop's client and the shared small
            # model, which is why nothing in context/policy.py had to change.
            client=self.roles.summary_client, small_model=self.roles.summary_model,
            settings=self.settings,
            limit=self.context_for(self.settings.provider, resolved.model),
            trigger=self.settings.context_trigger,
        ))
        if fit.persisted is not None:
            # Working memory becomes the summary; chat_log keeps every
            # message, so the user's own record is untouched.
            session.history = fit.persisted
        # Attached pictures ride on THIS message and nowhere else. session.history
        # holds plain strings — snip_compact, micro_compact, the tool budget and
        # the state summary all treat an entry as text — so putting blocks in
        # there would break four modules at once. The consequence, chosen on
        # purpose: the model sees the picture this turn (including every tool
        # iteration of it) and not on later turns. It stays in the conversation,
        # so you can still scroll back to it.
        content: str | list = prompt
        if images:
            content = ([{"type": "text", "text": prompt}] if prompt else []) + [
                _image_block(img) for img in images
            ]
        messages = fit.sent + [{"role": "user", "content": content}]

        # Context construction used to be invisible even though it is one of
        # the most important decisions an agent runtime makes.  This event does
        # not expose private prompt text; it exposes the useful facts a person
        # needs to understand the run: what was attached and which compaction
        # stages changed the request.
        if extra_context or fit.ran:
            notify("context", {
                "agent_id": resolved.name,
                "session_id": session.session_id,
                "application_chars": len(extra_context),
                "history_messages": len(session.history),
                "sent_messages": len(fit.sent),
                "compaction": fit.ran or [],
            })

        # AgentSpec.tools is an allowlist.  Keeping the full registry on the
        # runtime is useful (tools are built once and can hold resources), but
        # each agent must only show and execute the tools declared in its spec.
        tools = self.tools if resolved.tools is None else self.tools.subset(resolved.tools)

        return run_loop(
            client=self.client,
            model=resolved.model,
            system=system,
            messages=messages,
            tools=tools,
            max_iterations=resolved.max_iterations,
            max_tokens=resolved.max_tokens,
            observer=notify,
            stream=stream,
        )

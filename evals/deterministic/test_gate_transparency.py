"""DETERMINISTIC EVAL — 门控查了什么、查到没查到，用户看得见。

这条链路以前只把 `decision` 和一句 `reason` 走完全程：`retrieval_gate` 本来就
返回 `(retrieve, query, reason)` 三元组，`query` 在 `memory/__init__.py` 那一行
被扔掉；命中的那几条更是被 `"\\n".join()` 成一段字符串喂给模型，没有第二个人看过。
这一轮不是加新能力，是把已经算出来但被丢掉的两个信息接到页面上。

三条底线，每一条都在这文件里钉着：

  1. **喂给模型的那段字符串一个字节都没变** —— 透明化不能顺手改掉模型的输入。
  2. **没有 query/hits 的事件渲染出来和从前逐字节一样** —— 旧 trace 文件、旧
     chat_log 行原样显示，不迁移也不回填。
  3. **最坏情况塞得进 `_STEP_DETAIL_MAX`** —— 详情被静默切尾比看不见更糟，因为
     你会以为"就查到这几条"。
"""

from __future__ import annotations

import json

from evals.helpers import ScriptedClient, make_knowme, response, text_block, tool_block

# ---- 单元级别：直接驱动 gated_retrieve，不需要跑一整轮 ---------------------


def _gate_reply(retrieve: bool, query: str, reason: str = "提到了以前的事"):
    """门控模型那一次调用的假回复（就是 retrieval_gate 解析的那个 JSON）。"""
    return response([text_block(json.dumps(
        {"retrieve": retrieve, "query": query, "reason": reason},
        ensure_ascii=False))])


def _memory_with(client, tmp_path, facts=(), episodes=()):
    """一个装好记忆的 Memory —— 用 KnowMe 建，因为建库/建表那些事它已经做了。"""
    app = make_knowme(tmp_path / "home", client=client)
    for subject, content in facts:
        app.memory.facts.add(subject, content)
    for summary in episodes:
        app.memory.episodes.add(summary, "2026-09-01")
    return app.memory


def test_the_event_carries_the_query_and_the_hits_it_found(tmp_path):
    """notify 出去的 payload：决策、reason、检索词、以及命中了哪几条 ——
    而且每条都带着「这是长期事实还是某天的经历」。"""
    client = ScriptedClient([_gate_reply(True, "钠离子")])
    memory = _memory_with(client, tmp_path,
                          facts=[("钠离子", "钠离子电池的产业化在 2026 年提速")],
                          episodes=["聊过钠离子电池的成本"])

    events = []
    memory.gated_retrieve("钠离子电池现在怎么样了", notify=lambda k, ev: events.append((k, ev)))

    assert [kind for kind, _ in events] == ["gate"]
    ev = events[0][1]
    assert ev["decision"] == "retrieve"
    assert ev["query"] == "钠离子"
    assert ev["reason"] == "提到了以前的事"
    kinds = [(h["kind"], h["text"]) for h in ev["hits"]]
    # 事实在前、经历在后：两个 store 的字符串形状不一样，来源不能靠猜。
    assert kinds[0][0] == "事实"
    assert kinds[-1][0] == "经历"
    assert all(t for _, t in kinds)


def test_what_the_model_is_told_has_not_changed_by_one_byte(tmp_path):
    """透明化最危险的副作用：顺手改了模型的输入。这里把改造前的算法（两个 store
    的结果各自拿出来，拼成一段）独立写一遍，要求两边逐字节相等。"""
    client = ScriptedClient([_gate_reply(True, "钠离子")])
    memory = _memory_with(client, tmp_path,
                          facts=[("钠离子", "钠离子电池的产业化在 2026 年提速")],
                          episodes=["聊过钠离子电池的成本"])

    fed = memory.gated_retrieve("钠离子电池现在怎么样了", notify=lambda k, ev: None)

    facts = memory.facts.search("钠离子", memory.settings.retrieval_top_k)
    episodes = memory.episodes.search("钠离子", top_k=3)
    assert facts and episodes, "这条测试要先真的查到东西，否则比的是两个空串"
    assert fed == "\n".join(facts + episodes)


def test_a_skip_reports_no_hits_and_goes_out_the_moment_it_is_decided(tmp_path):
    """没查就没有命中可言 —— skip 那一支的 payload 里不该凭空冒出 hits。"""
    client = ScriptedClient([_gate_reply(False, "", "这道题不用查")])
    memory = _memory_with(client, tmp_path, facts=[("钠离子", "随便一条")])

    events = []
    fed = memory.gated_retrieve("2+2 等于几", notify=lambda k, ev: events.append(ev))

    assert fed == ""                      # 不检索就什么都不喂给模型
    assert events == [{"decision": "skip", "reason": "这道题不用查", "query": ""}]
    assert "hits" not in events[0]


# ---- 文案：新增的字段怎么变成那一行/那一段 --------------------------------


def test_an_event_without_a_query_renders_exactly_as_it_used_to():
    """锁死「旧记录不变形」：上线之前写下的那些事件没有 query/hits，走的就是
    今天这两句 —— 一个字节都不差。"""
    from knowme.core.runtime import gate_detail, gate_label

    old = {"decision": "retrieve", "reason": "问的是 alex"}
    assert gate_label(old) == "要查记忆"
    assert gate_detail(old) == "问的是 alex"

    assert gate_label({"decision": "skip", "reason": "数学题"}) == "不用查记忆"
    assert gate_detail({"decision": "skip", "reason": "数学题"}) == "数学题"


def test_the_worst_case_detail_still_fits_in_one_step():
    """7 条命中（每条都顶到截断）、最长的检索词、最长的 reason —— 拼出来必须
    短于 _STEP_DETAIL_MAX，否则 record_step 会**静默**把尾巴切掉，页面上看起来
    就像"只查到这几条"。

    「还有 N 条没显示」那行是唯一允许消失的东西，所以最坏情况要连它一起算进去。
    """
    from knowme.core.runtime import (
        _GATE_CLIP,
        _GATE_MAX_HITS,
        _STEP_DETAIL_MAX,
        gate_detail,
    )

    ev = {
        "decision": "retrieve",
        "reason": "理" * 15,                       # 提示词要求 ≤15 字
        "query": "词" * _GATE_CLIP,
        "hits": [{"kind": "事实", "text": "长" * _GATE_CLIP}
                 for _ in range(_GATE_MAX_HITS + 2)],
    }
    detail = gate_detail(ev)

    assert "…还有 2 条没显示" in detail          # 超过 5 条要自己说出来
    assert len(detail) <= _STEP_DETAIL_MAX, (
        f"最坏情况 {len(detail)} 字，超了 {_STEP_DETAIL_MAX} —— 会被静默截断"
    )


# ---- 端到端：一个真回合，meta 里存的和页面上画的是同一份 ------------------


def test_a_real_turn_stores_the_query_and_the_hits_in_meta(tmp_path):
    from knowme.core.runtime import gate_detail

    gate = _gate_reply(True, "alex", "问的是 alex")
    turn = [
        response([tool_block("save_note", {"subject": "alex", "content": "likes mornings"})],
                 "tool_use"),
        response([text_block("Noted.")]),
    ]
    app = make_knowme(tmp_path / "home", client=ScriptedClient([gate] + turn))
    app.memory.facts.add("alex", "alex 喜欢早起")

    app.respond("alex 什么时候有空")

    row = app.conn.execute(
        "SELECT meta FROM chat_log WHERE role='assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    meta = json.loads(row["meta"])

    assert meta["gate"]["query"] == "alex"
    assert [h["kind"] for h in meta["gate"]["hits"]] == ["事实"]
    # 第一步就是把同一个 payload 拼成的那句人话
    first = meta["steps"][0]
    assert first["kind"] == "gate"
    assert first["label"] == "要查记忆 ·「alex」 · 命中 1 条"
    assert first["detail"].startswith("问的是 alex\n检索词：alex\n命中 1 条：\n1. 事实 · ")
    # 存下来的那段，就是把存下来的那个 payload 交给同一个函数算出来的
    assert first["detail"] == gate_detail(meta["gate"])

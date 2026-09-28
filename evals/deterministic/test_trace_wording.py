"""DETERMINISTIC EVAL — 同一轮轨迹在两条路上各拼一遍，说的必须是同一句话。

直播那一步是浏览器自己拼的（render.js 收到一个 SSE 事件就记一步，因为服务端
存好的那份要等这一轮跑完才到），跑完之后用的是服务端存在 meta.steps 里的那份。
两份文案表分别在 `core/runtime.py` 和 `static/js/trace.js` 里 —— 这条测试把两边
钉在一起：谁改了一边、忘了另一边，它就会红。

（这就是「同一个问题两个决定点」：轨迹的用户看得见英文还是中文，取决于当初是哪
条路拼的。表分开是没法避免的——一边 Python 一边 JS——但可以测。）
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from evals.helpers import ScriptedClient, make_knowme, response, text_block, tool_block

TRACE_JS = Path(__file__).resolve().parents[2] / "knowme/ops/static/js/trace.js"

# trace.js 是 classic script（共享全局作用域），顶层只定义常量和函数，没有一处
# 会在载入时调用别人 —— 所以整份 eval 进来就能直接问它问题。
HARNESS = """
const fs = require("fs");
eval(fs.readFileSync(%s, "utf8"));
const GATE_EV = %s;
process.stdout.write(JSON.stringify({
  llm_label:  stepLabel("llm", {iteration: 1, stop_reason: "tool_use"}),
  llm_detail: stepDetail("llm", {in: 0, out: 0}),
  gate:       stepLabel("gate", {decision: "retrieve"}),
  gate_full:  stepLabel("gate", GATE_EV),
  gate_detail: stepDetail("gate", GATE_EV),
  context_label:  stepLabel("context", {history: 12, sent: 8}),
  context_detail: stepDetail("context", {chars: 0, compaction: ["micro_compact"]}),
  consolidation:  stepLabel("consolidation", {new_facts: 2}),
}));
"""

# 用户看到的样子，写在这里一次 —— 两边都得是这个。改文案就该改这里。
LLM_LABEL = "第 1 轮 · 要调用工具"
LLM_DETAIL = "输入 0 tokens，输出 0 tokens"
GATE_LABEL = "要查记忆"

# 门控那一行/那一段的完整形态：同一个 payload 喂给两边的实现，比出来必须一模一样。
# 这个 payload 只有一份 —— js 那份从 HARNESS 里吃它，py 那份在这里用它。
GATE_EV = {
    "decision": "retrieve",
    "reason": "提到了以前的事",
    "query": "alex 早起",
    "hits": [{"kind": "事实", "text": "alex 喜欢早起"},
             {"kind": "事实", "text": "alex 在大连海事大学读硕士"},
             {"kind": "经历", "text": "昨天聊过 alex 的见面时间"}],
}
GATE_LABEL_FULL = "要查记忆 ·「alex 早起」 · 命中 3 条"
GATE_DETAIL_FULL = ("提到了以前的事\n检索词：alex 早起\n命中 3 条：\n"
                    "1. 事实 · alex 喜欢早起\n"
                    "2. 事实 · alex 在大连海事大学读硕士\n"
                    "3. 经历 · 昨天聊过 alex 的见面时间")


def _js_steps() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 不在 PATH 里")
    # ensure_ascii 保持默认：中文变成 \uXXXX 进 JS 源码，免得撞上 Windows 上
    # node -e 参数的编码。
    script = HARNESS % (json.dumps(str(TRACE_JS)), json.dumps(GATE_EV))
    done = subprocess.run([node, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert done.returncode == 0, f"node harness 挂了：{done.stderr[:400]}"
    return json.loads(done.stdout)


def test_the_live_timeline_reads_as_plain_chinese():
    """浏览器那份（直播时你盯着看的那几行）。"""
    got = _js_steps()
    assert got["llm_label"] == LLM_LABEL
    assert got["llm_detail"] == LLM_DETAIL
    assert got["gate"] == GATE_LABEL
    assert got["gate_full"] == GATE_LABEL_FULL
    assert got["gate_detail"] == GATE_DETAIL_FULL
    assert got["context_label"] == "历史 12 条 → 送出 8 条"
    assert got["context_detail"] == "附加上下文 0 字；压缩：旧的工具结果收成一行指针"
    assert got["consolidation"] == "新增 2 条记忆"


def test_the_browser_and_the_server_build_the_gate_line_the_same_way():
    """门控那一行/那一段在两边各写了一份实现（直播是浏览器拼的，回看读服务端
    存的 meta.steps）。这里把同一个 payload 喂给两边，要求逐字节相等 —— 而且
    先各自等于写死的那句人话，因为「两边一致地错」也是错。"""
    from knowme.core.runtime import gate_detail, gate_label

    js = _js_steps()
    assert gate_label(GATE_EV) == GATE_LABEL_FULL == js["gate_full"]
    assert gate_detail(GATE_EV) == GATE_DETAIL_FULL == js["gate_detail"]


def test_the_stored_timeline_says_the_very_same_thing(tmp_path):
    """服务端那份（刷新之后、从 chat_log 读回来的那张卡片）。同一个输入，两边
    必须一字不差 —— 直播中文、回看英文是同一个 bug 的两个症状。"""
    gate = response([text_block('{"retrieve": true, "query": "alex", "reason": "问的是 alex"}')])
    turn = [
        response([tool_block("save_note", {"subject": "alex", "content": "早起"})], "tool_use"),
        response([text_block("记下了。")]),
    ]
    app = make_knowme(tmp_path / "home", client=ScriptedClient([gate] + turn))
    app.respond("记住 alex 喜欢早起")

    row = app.conn.execute(
        "SELECT meta FROM chat_log WHERE role='assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    steps = json.loads(row["meta"])["steps"]
    assert [s["kind"] for s in steps] == ["gate", "llm", "tool", "llm"]
    # 这一轮真的查了记忆（query=alex），但库里空的 ⇒ 命中 0 条。
    assert steps[0]["label"] == "要查记忆 ·「alex」 · 命中 0 条"
    assert steps[0]["detail"] == "问的是 alex\n检索词：alex\n没有命中任何记忆"
    assert steps[1]["label"] == LLM_LABEL
    assert steps[1]["detail"] == LLM_DETAIL
    assert steps[3]["label"] == "第 2 轮 · 直接回答"

    # 两边一个字都不能差 —— 服务端这一步拼出来的，和刚才那个写死的 payload
    # 喂给浏览器实现拼出来的，是同一种写法。
    js = _js_steps()
    assert js["llm_label"] == steps[1]["label"]
    assert js["llm_detail"] == steps[1]["detail"]
    assert GATE_LABEL == "要查记忆"          # 没有 query 的老记录还是这一句
    assert js["gate"] == GATE_LABEL

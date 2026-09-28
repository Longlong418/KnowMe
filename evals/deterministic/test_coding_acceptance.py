"""DETERMINISTIC EVAL — 一整轮对话里的验收，以及「没过再修一轮」。

Phase 30 的行为锁。上面那个文件测的是「验收怎么认命令、怎么记账」；这个文件测的是
**它在一次真实的 turn 里什么时候发生**：

  * 改完就过 → 只有一条验收收据，而且**没有第二次模型调用**（脚本只准备了三条回答，
    多调一次就 IndexError —— 天然的锁，不用另写断言）；
  * 先是挂的 → 系统把失败输出交回去，再修一轮，验第二次，一红一绿；
  * 修不动 → **只再修一轮**就停，把红的留在收据里，并且不撒谎（最终回答是那一轮的话）；
  * 这一轮没动过东西 → 完全不验；
  * 认不出命令 / 关掉了自动验收 / 总开关关着 → 不跑、不记账，但轨迹里说清楚为什么。

Hermetic：模型是脚本化的（evals/helpers.ScriptedClient），验收跑的是真的 pytest，
项目是 tmp_path 里的真目录，KNOWME_HOME 是临时的。
"""

from __future__ import annotations

import pytest

from evals.helpers import ScriptedClient, make_knowme, response, text_block, tool_block
from knowme.agents.catalog import get_profile
from knowme.applications import coding_runs, coding_workspace
from knowme.db import connect

# 每一轮开工前，都会先花一次小模型调用问「这句话要不要查记忆」。它不是这一轮的重点，
# 但它在脚本里的位置是固定的，所以要占一行。
GATE = response([text_block('{"retrieve": false, "query": "", "reason": "测试"}')])

PROJECT = """def value():
    return 1
"""
BROKEN_TEST = """from app import value


def test_value():
    assert value() == 2
"""


class RecordingClient(ScriptedClient):
    """把每次请求都记下来 —— 断言「塞回去的那句话里有没有失败输出」靠它。"""

    def __init__(self, script):
        super().__init__(script)
        self.calls: list[dict] = []

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return super()._create(**kwargs)


def prompts(client) -> list[str]:
    """每次请求里用户那一侧最后一段文本（工具结果那种结构化消息算空串）。"""
    out = []
    for call in client.calls:
        content = call["messages"][-1]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content
                              if isinstance(part, dict) and part.get("type") == "text")
        out.append(str(content))
    return out


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KNOWME_HOME", str(home))
    return home


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """一个有 pyproject.toml 的小项目：这就是「自动认成 pytest」的那条路。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    (root / "app.py").write_text(PROJECT, encoding="utf-8")
    monkeypatch.setenv("KNOWME_PROJECT_ROOT", str(root))
    return root


def allow_write(home, **fields) -> None:
    coding_workspace.save_coding_settings(home, {"allow_write": True, **fields})


def coding_agent(home, client):
    """Coding 那个 agent 本身 —— tools 是它的白名单，不能用默认 agent 冒充。"""
    return make_knowme(home, client=client, spec=get_profile("coding").spec)


def verify_rows(home, root):
    return [r for r in coding_runs.recent_runs(connect(home), root) if r["kind"] == "verify"]


def verify_steps(result) -> list[dict]:
    return [s for s in result.meta["steps"] if s["label"].startswith("验收")]


def user_rows(home) -> list[str]:
    rows = connect(home).execute(
        "SELECT content FROM chat_log WHERE role = 'user' ORDER BY rowid").fetchall()
    return [r["content"] for r in rows]


def write(path: str, content: str, call_id: str = "tu_1"):
    return response([tool_block("write_file", {"path": path, "content": content}, call_id)],
                    "tool_use")


# ---------------------------------------------------- 改完就过：不啰嗦第二遍


def test_a_green_verification_does_not_ask_the_model_again(home, proj):
    """项目本来就是好的：一条验收收据，两次模型调用（工具那轮 + 收尾那轮）。"""
    (proj / "test_demo.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    allow_write(home)
    client = RecordingClient([GATE,
                              write("notes.txt", "记一笔\n"),
                              response([text_block("改好了。")])])
    result = coding_agent(home, client).respond("写一个 notes.txt")

    rows = verify_rows(home, proj)
    assert len(rows) == 1 and rows[0]["returncode"] == 0
    assert len(client.calls) == 3                     # 门控 + 循环的两轮，仅此而已
    assert not any("验收没过" in p for p in prompts(client))
    assert result.reply == "改好了。"
    steps = verify_steps(result)
    assert len(steps) == 1 and steps[0]["label"].startswith("验收 · ") \
        and steps[0]["detail"].startswith("通过")
    assert steps[0]["status"] == "ok" and isinstance(steps[0]["ms"], int)


# ---------------------------------------------------- 挂了：自动再修一轮


def test_a_red_verification_goes_back_to_the_model_once(home, proj):
    """一红一绿：失败输出被塞回模型 → 它改对 → 再验一次通过。"""
    (proj / "test_demo.py").write_text(BROKEN_TEST, encoding="utf-8")
    allow_write(home)
    client = RecordingClient([
        GATE,
        write("app.py", "def value():\n    return 1\n"),      # 还是错的
        response([text_block("我改了 app.py。")]),
        GATE,                                                  # 再修那一轮也有门控
        write("app.py", "def value():\n    return 2\n", "tu_2"),
        response([text_block("这次对了。")]),
    ])
    result = coding_agent(home, client).respond("把 app.py 改成返回 2")

    rows = verify_rows(home, proj)                        # 新到旧
    assert [r["returncode"] for r in rows] == [0, 1]
    assert "失败" in rows[1]["summary"] and rows[0]["summary"] == "验收通过"

    # 最终回答是**第二**轮的话：第一轮那句已经不作数了
    assert result.reply == "这次对了。"
    # 两轮的工具调用都算这一轮的（meta.iterations 也是合并后的：2 + 2）
    assert [c["tool"] for c in result.tool_calls] == ["write_file", "write_file"]
    assert result.meta["iterations"] == 4

    back = [p for p in prompts(client) if "验收没过" in p]
    assert len(back) == 1
    assert "1 failed" in back[0]                          # 失败输出真的在里面
    assert "不要为了让测试变绿而改测试的断言" in back[0]     # 以及那条规矩

    # 中间那轮不进对话历史：你翻对话只看到自己说的那一句
    assert user_rows(home) == ["把 app.py 改成返回 2"]
    assert len(verify_steps(result)) == 2


def test_it_gives_up_after_one_retry_and_says_so(home, proj):
    """修不动就停在红的上面，让人来决定 —— 而不是一直烧 token 到「绿」为止。"""
    (proj / "test_demo.py").write_text(BROKEN_TEST, encoding="utf-8")
    allow_write(home)
    # 脚本只有六条：真要再修第二轮，ScriptedClient 会 IndexError 把测试炸掉。
    client = RecordingClient([
        GATE,
        write("app.py", "def value():\n    return 1\n"),
        response([text_block("改了。")]),
        GATE,
        write("app.py", "def value():\n    return 1\n", "tu_2"),
        response([text_block("我还是没修好。")]),
    ])
    result = coding_agent(home, client).respond("把 app.py 改成返回 2")

    rows = verify_rows(home, proj)
    assert [r["returncode"] for r in rows] == [1, 1]       # 两条红的，第二条是最新的
    assert result.reply == "我还是没修好。"                  # 页面上不撒谎
    assert len(client.calls) == 6
    # 轨迹里两步都是红点，而且 ms 还是个数字 —— describe() 的第三个值一旦按位置
    # 传给 record_step，它会掉进 ms 里，红的那一步看起来就是绿的。
    for step in verify_steps(result):
        assert step["status"] == "error" and isinstance(step["ms"], int)
    assert user_rows(home) == ["把 app.py 改成返回 2"]


# ---------------------------------------------------- 不验的那些情况


def test_a_read_only_turn_is_not_verified(home, proj):
    """只看了两眼没什么可验收的 —— 一个进程都不该起。"""
    (proj / "test_demo.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    allow_write(home)
    client = RecordingClient([GATE,
                              response([tool_block("read_file", {"path": "app.py"})],
                                       "tool_use"),
                              response([text_block("它返回 1。")])])
    result = coding_agent(home, client).respond("app.py 返回几？")

    assert verify_rows(home, proj) == []
    assert verify_steps(result) == []
    assert len(client.calls) == 3


def test_the_write_switch_is_the_only_gate_that_matters(home, proj):
    """总开关关着：改动被拒（工具回一句话），验收也根本不跑，而且说得出来为什么。"""
    coding_workspace.save_coding_settings(home, {"allow_write": False})
    client = RecordingClient([GATE,
                              write("notes.txt", "记一笔\n"),
                              response([text_block("开关是关的。")])])
    result = coding_agent(home, client).respond("写一个 notes.txt")

    assert not (proj / "notes.txt").exists()
    assert verify_rows(home, proj) == []
    steps = verify_steps(result)
    assert len(steps) == 1 and steps[0]["label"] == "验收 · 没跑"
    assert "开关" in steps[0]["detail"]


def test_turning_auto_verification_off_silences_it(home, proj):
    (proj / "test_demo.py").write_text(BROKEN_TEST, encoding="utf-8")
    allow_write(home, verify_auto=False)
    client = RecordingClient([GATE,
                              write("app.py", "def value():\n    return 2\n"),
                              response([text_block("改完了。")])])
    result = coding_agent(home, client).respond("把 app.py 改成返回 2")

    assert verify_rows(home, proj) == []
    assert len(client.calls) == 3                 # 没有第二次模型调用
    steps = verify_steps(result)
    assert len(steps) == 1 and "自动验收" in steps[0]["detail"]


def test_an_unrecognised_project_says_so_instead_of_guessing(tmp_path, home, monkeypatch):
    """认不出命令：不跑、不改任何东西，但轨迹里那句是「没找到验收命令」——绝不静默。"""
    root = tmp_path / "docs-only"
    root.mkdir()
    monkeypatch.setenv("KNOWME_PROJECT_ROOT", str(root))
    allow_write(home)
    client = RecordingClient([GATE,
                              write("note.md", "# 笔记\n"),
                              response([text_block("写好了。")])])
    result = coding_agent(home, client).respond("写一份笔记")

    assert verify_rows(home, root) == []
    steps = verify_steps(result)
    assert len(steps) == 1 and "没找到验收命令" in steps[0]["detail"]


def test_the_detail_command_is_what_actually_runs(home, proj):
    """页面上填的命令就是真跑的那条 —— 不是「自动认」的那条。"""
    """项目里的测试是过的，但页面填的命令说了算：真跑的是填的那条。"""
    (proj / "test_demo.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    allow_write(home, verify_command="echo custom-ok")
    client = RecordingClient([GATE,
                              write("notes.txt", "记一笔\n"),
                              response([text_block("好了。")])])
    coding_agent(home, client).respond("写一个 notes.txt")

    rows = verify_rows(home, proj)
    assert len(rows) == 1 and rows[0]["target"] == "echo custom-ok"
    assert "custom-ok" in coding_runs.read_detail(home, rows[0]["detail"])

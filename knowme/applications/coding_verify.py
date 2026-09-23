"""验收：KnowMe 自己动过手之后，由它跑一遍项目的验收命令。

为什么要有这一层：Phase 29 给了 KnowMe 一双手（读、写、跑命令）和一张收据，但收据
只说「改了哪几行」，不说「测试还过不过」。改完东西没人跑测试，等于把判断推给下一个
打开终端的人 —— 而这一轮之后，那个人是 KnowMe。

三条边界，和 `tools/coding.py` 共用同一套，不另起一套：

* **验收命令只来自两处**：用户在 Coding 页填的那条，或者下面那张固定表认出来的。
  没有任何工具会写 `coding.json`，所以模型编不出一条命令让 KnowMe 去跑。
* 跑之前过一遍 `tools/coding.py` 的黑名单，环境变量照旧摘掉密钥（`_command_env`）。
* **总开关（allow_write）管着它**：关着就根本不跑，连收据都不记。你没同意它动这个
  目录的时候，它也没理由去跑你的测试。

「验收」是**真的在跑你项目里的代码**（conftest.py、Makefile 里的东西都算），和黑名单
那件事一样：这是卫生，不是沙箱。要跑不可信的东西，请自己在容器里跑。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 认项目类型用到的标记文件。顺序就是优先级 —— 一个有 pyproject.toml 又有
# package.json 的仓库（这个仓库就是），先按 Python 认。
_PYTHON_MARKERS = ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg")

# 这一轮只要跑过其中任何一个，就值得验收一次。只有读（read_file / search_files /
# git_status / git_diff）不算 —— 光看了两眼没什么可验收的。
# `delegate_task` 算：交给本机 CLI 去改也是改，而且更该有人回头看一眼。
MUTATING_TOOLS = frozenset({"write_file", "edit_file", "run_command", "delegate_task"})

# 一套测试 60 秒跑不完是常态（tools/coding.py 的 run_command 默认就是 60 秒，
# 那是给「随手跑一条命令」用的）。MAX_TIMEOUT=600 仍然是硬顶。
VERIFY_TIMEOUT = 300

# 一审不过就把它交回模型，让它自己再修一轮 —— 只给一轮。修不动就把红的留在
# 收据里，让人来决定，而不是让模型一直烧 token 直到「绿」为止（那往往意味着
# 它去改测试的断言了）。
MAX_RETRIES = 1

# 塞回给模型的输出上限。开头和结尾都留着 —— pytest 的结论在最后，编译器的
# 第一个错在最前（和 tools/coding.py:_head_tail 一个道理）。
MAX_RETRY_OUTPUT = 4000

# 轨迹里那一步的正文取输出最后一行（pytest 的 "1 failed, 12 passed in 0.4s"
# 就在那儿），裁到这个长度。
_TAIL_LINE_MAX = 120


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _npm_test_script(path: Path) -> bool:
    """`package.json` 里真的有一个 test 脚本吗？

    必须真看一眼：没有 test 脚本的 package.json 太常见了，盲跑 `npm test`
    只会得到一句 "missing script"，那不是验收，是噪音。
    """
    try:
        data = json.loads(_read(path) or "{}")
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    scripts = data.get("scripts")
    return bool(isinstance(scripts, dict) and str(scripts.get("test") or "").strip())


def _make_target(path: Path, name: str) -> bool:
    """Makefile 里真有这个目标吗？`.PHONY: test` 不算目标，`test:` 才算。"""
    return re.search(rf"^{re.escape(name)}\s*:", _read(path), re.MULTILINE) is not None


def detect_command(root: Path | str) -> str:
    """这个项目该跑什么来验收？认不出来就返回 ""，绝不瞎猜一条。

    认到第一个就停。命令是一句**字符串**（走 shell），所以 npm 那种 Windows 上
    是 .cmd shim 的东西也能起来。
    """
    root = Path(root)

    def has(*names: str) -> bool:
        return any((root / name).exists() for name in names)

    if has(*_PYTHON_MARKERS) or (root / "tests").is_dir():
        # sys.executable 而不是裸 `pytest`：KnowMe 跑在自己的 venv 里，而 PATH 上
        # 经常没有 pytest。引号是为了路径带空格也不炸（shell=True）。
        # -p no:cacheprovider：不在你的项目里留下 .pytest_cache —— 和「收据写在
        # KNOWME_HOME 而不是你的项目里」是同一个立场。
        return f'"{sys.executable}" -m pytest -q -p no:cacheprovider'
    if (root / "package.json").is_file() and _npm_test_script(root / "package.json"):
        return "npm test"
    if (root / "Makefile").is_file() and _make_target(root / "Makefile", "test"):
        return "make test"
    if (root / "go.mod").is_file():
        return "go test ./..."
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    return ""


def verify_command(home, root: Path | str) -> str:
    """配置里填了就用配置里的，否则自动认。"""
    from knowme.applications.coding_workspace import load_coding_settings

    try:
        configured = str(load_coding_settings(home).get("verify_command") or "").strip()
    except (OSError, ValueError):
        configured = ""
    return configured or detect_command(root)


def should_verify(tool_calls) -> bool:
    """这一轮真的动过东西吗？纯判断，好测，也是唯一决定「要不要验收」的地方。"""
    return any(str((call or {}).get("tool") or "") in MUTATING_TOOLS
               for call in tool_calls or [])


def run(settings, conn, *, session=None, agent_id: str = "default") -> dict:
    """跑一遍验收，返回结果（给轨迹和模型用）。

    「这一次没验」也有返回值（`ran=False` + `note` 说明为什么），理由会进轨迹 ——
    绝不静默。你至少该在轨迹里看到「为什么这次没验」。
    """
    from knowme.applications.coding_workspace import load_coding_settings, project_root

    try:
        coding = load_coding_settings(settings.home)
    except (OSError, ValueError):
        return _skipped("读不到 coding.json")

    # 总开关。验收也是「跑命令」，所以管它的就是同一个开关 —— 不新增第二个
    # 需要你理解和维护的旋钮。关着的时候什么都还没被改过，也就没什么可验的
    # （唯一例外是交给人本机 CLI 改的：那种情况下面这句会告诉你为什么没验）。
    if not coding.get("allow_write"):
        return _skipped("写和跑命令的开关关着")
    if not coding.get("verify_auto", True):
        return _skipped("自动验收关着")

    root = project_root()
    command = verify_command(settings.home, root)
    if not command:
        return _skipped("没找到验收命令，去 Coding 页设置里填一条")

    from knowme.tools.coding import run_verify

    # 黑名单、环境变量、超时、记账都在 tools/coding.py 那一处 —— 这里不复制。
    return run_verify(settings, conn, session=session, agent_id=agent_id, command=command)


def _skipped(note: str) -> dict:
    return {"ran": False, "ok": None, "command": "", "returncode": None,
            "output": "", "note": note}


def describe(outcome: dict | None) -> tuple[str, str, str]:
    """轨迹里那一步的 (label, detail, status)。

    **调用者要把 status 按关键字传**（`record_step(..., label, detail, status=status)`）：
    `record_step` 的第三个位置是 `ms`，照顺序拆开传会把 "error" 塞进时长里，红的验收
    在轨迹上还会是绿的。

    用现成的 "tool" kind：`trace.js` 的 STEP_TITLES 认它是「工具」，渲染成
    `工具 · 验收 · pytest -q　1.2 秒` + 绿点/红点 + 详情展开。所以这一条新增的
    步骤**不需要动前端**，也不需要动 core/runtime.py ↔ trace.js 那两份必须一起改的
    中文文案表 —— 本轮特意挑的形状。
    """
    if outcome is None:
        return "验收 · 没跑", "", "ok"
    if not outcome.get("ran"):
        return "验收 · 没跑", str(outcome.get("note") or ""), "ok"
    command = str(outcome.get("command") or "")
    tail = _tail_line(outcome.get("output") or "")
    if outcome.get("ok"):
        return f"验收 · {command}", f"通过{tail}", "ok"
    if outcome.get("returncode") is None:
        return f"验收 · {command}", "超时（已经杀掉）", "error"
    return (f"验收 · {command}",
            f"失败（退出 {outcome['returncode']}）{tail}", "error")


def _tail_line(output: str) -> str:
    """输出最后一行非空的内容 —— pytest 的结论就在那儿。"""
    for line in reversed(str(output).splitlines()):
        if line.strip():
            return f" · {line.strip()[:_TAIL_LINE_MAX]}"
    return ""


def retry_message(outcome: dict) -> str:
    """一审没过时塞回给模型的那句话。

    它**不进对话历史**：`session.add_exchange` 仍然只在整轮结束时调一次（你原来
    那句 + 最终回复），所以你翻对话看不到一条「（系统）验收没过」的假用户消息。
    但它做的事在轨迹里看得见 —— 失败那一步 + 再修一轮那一步。
    """
    from knowme.tools.coding import _head_tail

    output = _head_tail(str(outcome.get("output") or "").strip(), MAX_RETRY_OUTPUT)
    return (
        "（系统）验收没过。\n"
        f"我跑了：{outcome.get('command') or '(没有命令)'}\n"
        f"退出码 {outcome.get('returncode')}\n"
        f"{output or '(没有输出)'}\n\n"
        "请修到它过。不要为了让测试变绿而改测试的断言 —— 除非测试本身就写错了，"
        "那要说清楚理由。改完直接说结论，我会再验一次。"
    )

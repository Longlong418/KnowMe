"""DETERMINISTIC EVAL — 验收：KnowMe 动完手之后，自己跑一遍项目的测试。

Phase 30。Phase 29 给了它一双手和一张收据，但收据只说「改了哪几行」，不说「测试还
过不过」。这个文件锁的就是补上的那一半：

  * 命令从哪来 —— 只有两处：你填的，或者那张固定表认出来的。没有任何工具能写
    `coding.json`，所以模型编不出一条命令让 KnowMe 去跑。
  * 什么时候不跑 —— 总开关关着 / 关掉了自动验收 / 认不出命令，三个都**一个进程都不许
    起**，而且要说清楚为什么（`note` 会进轨迹，绝不静默）。
  * 跑了之后记什么 —— 一条 `verify` 收据，`returncode` 三个状态分得开：数字是真实退出
    码，`None` 是超时被杀，`-1` 是压根没跑起来。

Hermetic：项目是 tmp_path 下的真目录，跑的是真的 pytest（没有网络、没有模型、不碰
用户真实的 .knowme —— conftest 已经把 KNOWME_HOME 指到临时目录）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from knowme.applications import coding_runs, coding_verify, coding_workspace
from knowme.config import load_settings
from knowme.db import connect
from knowme.tools import coding

HAS_GIT = bool(shutil.which("git"))


def git_init(root: Path) -> None:
    for argv in (["git", "init"], ["git", "add", "-A"],
                 ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-m", "start"]):
        subprocess.run(argv, cwd=root, capture_output=True, check=True)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KNOWME_HOME", str(home))
    return home


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """一个最小的 Python 项目。有 pyproject.toml = 「按 Python 认」。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    monkeypatch.setenv("KNOWME_PROJECT_ROOT", str(root))
    return root


def configure(home, **fields) -> None:
    """页面上那个「保存」按钮做的事。"""
    coding_workspace.save_coding_settings(home, {"allow_write": True, **fields})


def verify(home):
    """`coding_verify.run` —— 运行时在每一轮末尾调的就是它。"""
    return coding_verify.run(load_settings(), connect(home), session=None, agent_id="coding")


def verify_rows(home, root):
    conn = connect(home)
    return [r for r in coding_runs.recent_runs(conn, root) if r["kind"] == "verify"]


@pytest.fixture
def no_process(monkeypatch):
    """间谍：真起了 subprocess 就把测试炸掉，并留下次数。"""
    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("这一步不该起任何进程")

    monkeypatch.setattr(coding.subprocess, "run", spy)
    return calls


# ------------------------------------------------------- 认哪条命令


def test_a_python_project_is_recognised_by_any_of_its_markers(tmp_path):
    for marker in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"):
        root = tmp_path / marker.replace(".", "_")
        root.mkdir()
        (root / marker).write_text("", encoding="utf-8")
        command = coding_verify.detect_command(root)
        # 用 sys.executable 而不是裸 `pytest`：KnowMe 跑在自己的 venv 里，
        # PATH 上经常没有 pytest。
        assert command.startswith(f'"{sys.executable}"') and "-m pytest" in command, marker
    # 一个只有 tests/ 目录的项目也一样
    bare = tmp_path / "bare"
    (bare / "tests").mkdir(parents=True)
    assert "-m pytest" in coding_verify.detect_command(bare)


def test_python_wins_over_node_in_a_mixed_repo(tmp_path):
    """这个仓库自己就是这种：有 pyproject.toml，也有 package.json。"""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "jest"}}', encoding="utf-8")
    assert "-m pytest" in coding_verify.detect_command(tmp_path)


def test_a_package_json_without_a_test_script_is_not_a_verification(tmp_path):
    """盲跑 `npm test` 只会得到一句 "missing script" —— 那不是验收，是噪音。"""
    (tmp_path / "package.json").write_text(
        '{"scripts": {"build": "webpack"}}', encoding="utf-8")
    assert coding_verify.detect_command(tmp_path) == ""
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "jest"}}', encoding="utf-8")
    assert coding_verify.detect_command(tmp_path) == "npm test"


def test_a_makefile_needs_a_real_test_target(tmp_path):
    (tmp_path / "Makefile").write_text(".PHONY: test\n", encoding="utf-8")
    assert coding_verify.detect_command(tmp_path) == ""
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    assert coding_verify.detect_command(tmp_path) == "make test"


def test_go_and_cargo(tmp_path):
    go, rust = tmp_path / "go", tmp_path / "rust"
    go.mkdir()
    (go / "go.mod").write_text("module demo\n", encoding="utf-8")
    assert coding_verify.detect_command(go) == "go test ./..."
    rust.mkdir()
    (rust / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert coding_verify.detect_command(rust) == "cargo test"


def test_an_unrecognised_project_gets_no_command(tmp_path):
    """认不出来就是空字符串 —— 绝不瞎猜一条。"""
    (tmp_path / "notes.md").write_text("hi\n", encoding="utf-8")
    assert coding_verify.detect_command(tmp_path) == ""


def test_the_configured_command_beats_the_detected_one(home, proj):
    assert "-m pytest" in coding_verify.verify_command(home, proj)   # 没配 = 自动认
    configure(home, verify_command="python -m unittest")
    assert coding_verify.verify_command(home, proj) == "python -m unittest"
    configure(home, verify_command="   ")                            # 清空 = 回到自动认
    assert "-m pytest" in coding_verify.verify_command(home, proj)


def test_the_two_new_fields_have_the_documented_defaults(home):
    """老库里的 coding.json 只有三个键，读回来必须补齐，而不是 KeyError。"""
    fresh = coding_workspace.load_coding_settings(home)
    assert fresh["verify_auto"] is True      # 改完不验，等于把判断推给下一个人
    assert fresh["verify_command"] == ""


# ------------------------------------------------------- 什么时候验


def test_should_verify_only_when_something_actually_changed():
    assert coding_verify.should_verify([]) is False
    assert coding_verify.should_verify([{"tool": "read_file"}, {"tool": "git_diff"}]) is False
    for name in ("write_file", "edit_file", "run_command", "delegate_task"):
        assert coding_verify.should_verify([{"tool": name}]) is True, name


def test_the_write_switch_stops_verification_before_any_process_starts(
        home, proj, no_process):
    """总开关关着 = 你还没同意它动这个目录，它就没理由去跑你的测试。"""
    coding_workspace.save_coding_settings(home, {"allow_write": False})
    outcome = verify(home)
    assert outcome["ran"] is False and no_process == []
    assert "开关" in outcome["note"]
    # 连收据都不该留下 —— 没发生的事不值得记账
    assert verify_rows(home, proj) == []


def test_turning_off_auto_verification_stops_it(home, proj, no_process):
    configure(home, verify_auto=False)
    outcome = verify(home)
    assert outcome["ran"] is False and no_process == []
    assert "自动验收" in outcome["note"]
    assert verify_rows(home, proj) == []


def test_no_command_means_no_run_and_a_reason(tmp_path, monkeypatch, home, no_process):
    """认不出命令时不跑，但要说清楚为什么 —— 沉默才是最坏的答案。"""
    empty = tmp_path / "empty-project"
    empty.mkdir()
    monkeypatch.setenv("KNOWME_PROJECT_ROOT", str(empty))
    configure(home)
    outcome = verify(home)
    assert outcome["ran"] is False and no_process == []
    assert "没找到验收命令" in outcome["note"]


# ------------------------------------------------------- 跑了之后记什么


def _write_test(root: Path, body: str) -> None:
    (root / "test_demo.py").write_text(body, encoding="utf-8")


def test_a_passing_project_records_a_passing_verification(home, proj):
    _write_test(proj, "def test_ok():\n    assert True\n")
    configure(home)
    outcome = verify(home)

    assert outcome["ran"] is True and outcome["ok"] is True
    assert outcome["returncode"] == 0
    rows = verify_rows(home, proj)
    assert len(rows) == 1
    assert rows[0]["returncode"] == 0 and rows[0]["summary"] == "验收通过"
    assert rows[0]["target"].endswith("-m pytest -q -p no:cacheprovider")
    # 输出落在 .log 里（不是 .diff：验收没有 diff 可看）
    assert rows[0]["detail"].endswith(".log")
    assert "passed" in coding_runs.read_detail(home, rows[0]["detail"])
    # 轨迹那一步（kind 是现成的 "tool"）
    label, detail, status = coding_verify.describe(outcome)
    assert label.startswith("验收 · ") and detail.startswith("通过") and status == "ok"


def test_a_failing_project_records_a_failing_verification(home, proj):
    _write_test(proj, "def test_broken():\n    assert False\n")
    configure(home)
    outcome = verify(home)

    assert outcome["ran"] is True and outcome["ok"] is False
    assert outcome["returncode"] == 1
    rows = verify_rows(home, proj)
    assert len(rows) == 1 and rows[0]["returncode"] == 1
    assert "失败" in rows[0]["summary"] and "1" in rows[0]["summary"]
    assert "1 failed" in coding_runs.read_detail(home, rows[0]["detail"])

    label, detail, status = coding_verify.describe(outcome)
    assert label.startswith("验收 · ") and detail.startswith("失败（退出 1）")
    assert status == "error" and "1 failed" in detail


def test_the_retry_message_carries_the_failure_and_the_rule(home, proj):
    """一审没过时塞回给模型的那句话：我跑了什么、退出码几、错在哪，
    以及那条最要紧的规矩 —— 别为了让测试变绿去改断言。"""
    _write_test(proj, "def test_broken():\n    assert False\n")
    configure(home)
    message = coding_verify.retry_message(verify(home))
    assert "验收没过" in message and "1 failed" in message
    assert "不要为了让测试变绿而改测试的断言" in message


def test_a_denied_command_is_recorded_as_never_started(home, proj):
    """黑名单里的命令不能假装没发生：它得有一条收据，而且不能是「通过」。

    `returncode=-1` 和超时的 `None` 是两回事 —— 页面上「没跑起来」和「超时」
    也得是两句话。
    """
    configure(home, verify_command="git push origin main")
    outcome = verify(home)
    assert outcome["ran"] is False and outcome["returncode"] == -1
    rows = verify_rows(home, proj)
    assert len(rows) == 1 and rows[0]["returncode"] == -1
    assert rows[0]["summary"] == "验收没跑起来"
    assert "黑名单" in coding_runs.read_detail(home, rows[0]["detail"])
    label, detail, status = coding_verify.describe(outcome)
    assert label == "验收 · 没跑" and status == "ok"   # 「没跑」不是「失败」
    assert "黑名单" in detail                          # 为什么没跑，写在轨迹里


@pytest.mark.skipif(not HAS_GIT, reason="收据要真的 git 仓库")
def test_verification_does_not_take_a_late_baseline(home, proj):
    """起点必须取在改动**之前**。验收本身不改东西，所以它不能自己记一个起点 ——
    那一轮的起点应该已经由 write_file 记下了（Phase 29 的教训）。"""
    _write_test(proj, "def test_ok():\n    assert True\n")
    git_init(proj)
    configure(home)
    conn = connect(home)
    project = coding._Project(load_settings(), conn, session=None, agent_id="coding")
    project.ensure_baseline()                       # 改之前
    (proj / "note.txt").write_text("changed\n", encoding="utf-8")
    project.record("write", "note.txt", before="", after="changed\n")

    verify(home)

    rows = coding_runs.recent_runs(conn, proj)
    assert sorted(r["kind"] for r in rows) == ["baseline", "verify", "write"]

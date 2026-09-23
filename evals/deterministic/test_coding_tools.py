"""DETERMINISTIC EVAL — KnowMe's own hands on the project, and the receipt.

Phase 29. Until now the Coding Agent could only delegate; these are the tools
that let it look at the code and change it itself, so the three rules they rest
on are what this file pins:

  * SCOPE — nothing outside the project root, and never the .env family.
  * CONSENT — reads are free, writes and commands need the Coding page's
    allow_write switch, read at CALL time so flipping it needs no rebuild.
  * RECEIPT — every change lands in coding_runs with the diff, and the baseline
    it can be undone to is taken before the first one.

Hermetic: no CLI is spawned, no network. The project is a tmp_path directory —
a real `git init` where git exists, skipped where it does not.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowme.agents.catalog import get_profile
from knowme.applications import coding_runs, coding_workspace
from knowme.config import load_settings
from knowme.db import connect
from knowme.tools import coding

HAS_GIT = bool(shutil.which("git"))


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A small project with the three kinds of file that matter: readable code,
    a nested directory, and a secret."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def hi():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# hello\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=super-secret\n", encoding="utf-8")
    monkeypatch.setenv("KNOWME_PROJECT_ROOT", str(root))
    return root


@pytest.fixture
def hands(tmp_path, project, monkeypatch):
    """The eight tools, built the way the app builds them."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KNOWME_HOME", str(home))
    conn = connect(home)
    settings = load_settings()
    built = coding.make_tools(settings, conn, session=None, agent_id="coding")
    return SimpleNamespace(by_name={t.name: t for t in built}, conn=conn,
                           home=home, settings=settings, root=project)


def allow_write(hands, allowed: bool = True) -> None:
    """The Coding page's switch — written straight to coding.json."""
    coding_workspace.save_coding_settings(hands.home, {"allow_write": allowed})


def run(hands, name: str, **kwargs) -> str:
    return hands.by_name[name].fn(**kwargs)


# --------------------------------------------------------------- scope


def test_listing_hides_secrets_and_noise(hands):
    listed = run(hands, "list_files")
    assert "src/" in listed and "README.md" in listed
    assert ".env" not in listed

    (hands.root / ".git").mkdir()
    (hands.root / ".git" / "config").write_text("x", encoding="utf-8")
    assert ".git/" not in run(hands, "list_files")


def test_reads_stay_inside_the_project(hands):
    assert "return 1" in run(hands, "read_file", path="src/app.py")
    (hands.root.parent / "secret.txt").write_text("outside the root", encoding="utf-8")

    # An absolute path never even gets to the containment check...
    assert "请给相对路径" in run(hands, "read_file", path=str(hands.root.parent / "secret.txt"))
    # ...and climbing out relatively is refused BY THE CONTAINMENT GUARD. The
    # message is pinned to that guard on purpose: this path is also caught by
    # the later relative_to() call, so asserting only "没能执行" would keep
    # passing with the guard deleted (it did, until this was sharpened).
    for bad in ("../secret.txt", "src/../../secret.txt"):
        answer = run(hands, "read_file", path=bad)
        assert "不在项目根目录" in answer, bad
        assert "outside the root" not in answer, bad
    # /etc/passwd is absolute on Linux and drive-relative on Windows, so the
    # containment guard answers on one and the "give me a relative path" check
    # on the other. Both are refusals; pinning either one would pin the wrong
    # thing on the other platform.
    unix_absolute = run(hands, "read_file", path="/etc/passwd")
    assert "请给相对路径" in unix_absolute or "不在项目根目录" in unix_absolute


def test_the_env_family_is_neither_listed_nor_readable(hands):
    answer = run(hands, "read_file", path=".env")
    assert "密钥文件" in answer
    assert "super-secret" not in answer
    # ...and search does not become the way around it. (The no-match sentence
    # echoes the query back, so the file NAME is what has to be absent.)
    hits = run(hands, "search_files", query="super-secret")
    assert ".env" not in hits
    assert "没有找到" in hits


def test_read_file_windows_lines_and_numbers_them(hands):
    (hands.root / "big.py").write_text(
        "".join(f"line {n}\n" for n in range(1, 21)), encoding="utf-8")
    window = run(hands, "read_file", path="big.py", start=3, end=5)
    assert "line 3" in window and "line 5" in window and "line 6" not in window
    assert "共 20 行" in window  # says there is more


def test_search_reports_file_line_and_text(hands):
    hits = run(hands, "search_files", query="return 1")
    assert hits == "src/app.py:2: return 1"
    assert "没有找到" in run(hands, "search_files", query="nothing-matches-this")
    assert run(hands, "search_files", query="return", glob="*.md") == "没有找到「return」"


# --------------------------------------------------------------- consent


def test_write_and_run_are_refused_until_the_switch_is_on(hands):
    refused = run(hands, "write_file", path="src/app.py", content="x = 1\n")
    assert "开关现在是关的" in refused
    assert (hands.root / "src" / "app.py").read_text(encoding="utf-8") == "def hi():\n    return 1\n"
    assert "开关现在是关的" in run(hands, "run_command", command="echo hi")
    assert "开关现在是关的" in run(hands, "edit_file", path="src/app.py",
                                   old_text="return 1", new_text="return 2")

    # The switch is read per call: the SAME tool objects must start working,
    # with no rebuild in between.
    allow_write(hands)
    assert "已写入" in run(hands, "write_file", path="src/app.py", content="x = 1\n")
    assert (hands.root / "src" / "app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "退出码 0" in run(hands, "run_command", command="echo hi")

    allow_write(hands, False)
    assert "开关现在是关的" in run(hands, "run_command", command="echo hi")


def test_reads_still_work_with_the_switch_off(hands):
    assert "return 1" in run(hands, "read_file", path="src/app.py")
    assert run(hands, "git_status")  # a sentence either way, never a crash


def test_edit_file_refuses_ambiguity_and_touches_nothing(hands):
    allow_write(hands)
    target = hands.root / "src" / "app.py"
    before = target.read_bytes()

    missing = run(hands, "edit_file", path="src/app.py", old_text="no such text", new_text="x")
    assert "没有找到这段原文" in missing

    (hands.root / "twice.py").write_text("a = 1\na = 1\n", encoding="utf-8")
    twice_before = (hands.root / "twice.py").read_bytes()
    ambiguous = run(hands, "edit_file", path="twice.py", old_text="a = 1\n", new_text="a = 2\n")
    assert "出现了 2 次" in ambiguous
    assert (hands.root / "twice.py").read_bytes() == twice_before

    assert target.read_bytes() == before  # neither refusal wrote anything

    # replace_all is the way through, and it says how many it replaced.
    assert "替换了 2 处" in run(hands, "edit_file", path="twice.py", old_text="a = 1\n",
                               new_text="a = 2\n", replace_all=True)
    assert (hands.root / "twice.py").read_text(encoding="utf-8") == "a = 2\na = 2\n"


def test_edit_reports_the_diff_to_the_model(hands):
    allow_write(hands)
    answer = run(hands, "edit_file", path="src/app.py", old_text="    return 1",
                 new_text="    return 42")
    assert "+1 −1" in answer
    assert "-    return 1" in answer and "+    return 42" in answer


def test_run_command_refuses_the_denylist_before_spawning(hands, monkeypatch):
    allow_write(hands)
    spawned = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        spawned.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(coding.subprocess, "run", spy)
    answer = run(hands, "run_command", command="git push origin main")
    assert "黑名单" in answer
    # The receipt takes its baseline with its own git call, so the assertion is
    # that the DENIED command never reached a process — not that nothing ran.
    reached = " ".join(str(part) for args in spawned
                       for part in (args[0] if isinstance(args[0], (list, tuple)) else [args[0]]))
    assert "push" not in reached


def test_run_command_returns_output_and_gives_up_on_timeout(hands):
    allow_write(hands)
    assert "退出码 0" in run(hands, "run_command", command="echo hi")

    slow = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    answer = run(hands, "run_command", command=slow, timeout_seconds=1)
    assert "超过 1 秒" in answer


def test_commands_do_not_see_secret_environment_variables(hands, monkeypatch):
    allow_write(hands)
    monkeypatch.setenv("SOMETHING_API_KEY", "sk-should-not-be-visible")
    probe = f'"{sys.executable}" -c "import os;print(os.environ.get(\'SOMETHING_API_KEY\', \'gone\'))"'
    assert "gone" in run(hands, "run_command", command=probe)


# --------------------------------------------------------------- the receipt


def git_init(root: Path) -> None:
    for argv in (["git", "init"], ["git", "add", "-A"],
                 ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "start"]):
        subprocess.run(argv, cwd=root, capture_output=True, check=True)


@pytest.mark.skipif(not HAS_GIT, reason="needs a real git")
def test_every_write_leaves_a_receipt_and_a_baseline(hands):
    git_init(hands.root)
    allow_write(hands)

    run(hands, "write_file", path="src/app.py", content="def hi():\n    return 2\n")
    run(hands, "edit_file", path="README.md", old_text="# hello", new_text="# hi")

    rows = coding_runs.recent_runs(hands.conn, hands.root)
    assert [r["kind"] for r in rows] == ["edit", "write", "baseline"]
    baseline, write, edit = rows[-1], rows[-2], rows[0]
    assert baseline["head"], "the baseline records the commit the session started from"
    assert (write["insertions"], write["deletions"]) == (1, 1)
    assert write["target"] == "src/app.py"
    assert edit["target"] == "README.md"

    detail = coding_runs.read_detail(hands.home, write["detail"])
    assert "-    return 1" in detail and "+    return 2" in detail
    # The baseline's diff is the "before" picture — empty here, because the
    # tree was clean when the first change was made.
    assert baseline["has_detail"] is False

    # ...and the panel's one way to fetch it, through the existing route.
    served = coding_workspace.workspace_action(
        {"action": "run", "detail": write["detail"], "agent_id": "coding"}, hands.home)
    assert served["ok"] is True and "return 2" in served["detail"]
    assert coding_workspace.workspace_action(
        {"action": "run", "detail": "../state.db", "agent_id": "coding"}, hands.home)["ok"] is False


@pytest.mark.skipif(not HAS_GIT, reason="needs a real git")
def test_the_baseline_carries_work_that_was_already_uncommitted(hands):
    """The point of taking it lazily: whatever was dirty BEFORE KnowMe touched
    anything must be in the undo target, not silently absorbed into it."""
    git_init(hands.root)
    (hands.root / "README.md").write_text("# edited by the user\n", encoding="utf-8")
    allow_write(hands)
    run(hands, "write_file", path="src/app.py", content="x = 1\n")

    baseline = coding_runs.recent_runs(hands.conn, hands.root)[-1]
    detail = coding_runs.read_detail(hands.home, baseline["detail"])
    assert "edited by the user" in detail


def test_writes_are_recorded_even_without_a_repository(hands):
    allow_write(hands)
    assert "已写入" in run(hands, "write_file", path="README.md", content="# new\n")
    rows = coding_runs.recent_runs(hands.conn, hands.root)
    assert [r["kind"] for r in rows] == ["write"]  # no baseline, no crash
    assert coding_runs.read_detail(hands.home, rows[0]["detail"]).startswith("--- a/README.md")


def test_the_coding_agent_can_actually_see_these_tools():
    """Registration is not enough — AgentSpec.tools is the allowlist, and a tool
    that is built but never listed is a tool the model never learns about."""
    allowed = get_profile("coding").spec.tools
    assert {"list_files", "read_file", "search_files", "git_status", "git_diff",
            "write_file", "edit_file", "run_command"} <= set(allowed)


def test_the_switch_defaults_to_off(tmp_path):
    """A fresh home: the one thing that must never be on by accident."""
    fresh = tmp_path / "fresh-home"
    fresh.mkdir()
    assert coding_workspace.load_coding_settings(fresh)["allow_write"] is False
    assert coding_workspace.coding_backends(fresh)["settings"]["allow_write"] is False

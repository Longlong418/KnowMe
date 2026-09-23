"""KnowMe's own hands on the project: list, read, search, write, run.

Until now the Coding Agent could only DELEGATE: hand a task to a local CLI and
read back a sentence. This module is the other half — KnowMe can look at the
code itself and change it herself, which is what makes the delegated result
reviewable instead of taken on faith.

The rules, in one place, because they are the whole design:

  * SCOPE. Everything is resolved against ``project_root()`` (KNOWME_PROJECT_ROOT,
    or the directory KnowMe was started in) and refused if it lands outside it.
    The guards are the ones already in applications/coding_workspace.py —
    `_relative_path` for containment, `SKIP_FILES` for the .env family,
    `SKIP_DIRECTORIES` for .git/.knowme/.venv — so the page and the tools can
    never disagree about what is in bounds.
  * READS ARE FREE. list/read/search/git are always registered.
  * WRITES AND COMMANDS NEED THE SWITCH. `allow_write` in .knowme/coding.json,
    toggled on the Coding page and read at CALL time, so flipping it takes
    effect on the next tool call with no rebuild. When it is off the tools
    still exist and say so — a model that knows it has withheld hands asks the
    user to enable them; a model that has no such tool invents a workaround.
  * THE DENYLIST IS NOT A SANDBOX. run_command shells out, so a determined
    command can do anything the KnowMe process can. The list below refuses the
    handful of mistakes that are unrecoverable, and nothing more. Say that to
    the user rather than implying safety.

Everything that changes the working tree goes through applications/coding_runs,
which writes the baseline row and one row per change. A write that cannot be
recorded is still a write, so failures there are swallowed — the receipt is
evidence, not a gate.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path

from knowme.applications import coding_runs
from knowme.applications.coding_workspace import (
    SKIP_DIRECTORIES,
    SKIP_FILES,
    TEXT_SUFFIXES,
    _relative_path,
    load_coding_settings,
    project_root,
)
from knowme.core.tools import Tool
from knowme.tools._env import delegate_env

MAX_READ_LINES = 2000
DEFAULT_READ_LINES = 400
MAX_MATCHES = 100
MAX_FILE_CHARS = 60_000
MAX_DIFF_CHARS = 6000
MAX_OUTPUT_CHARS = 8000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600

# Unrecoverable mistakes, refused before a process exists. Deliberately short:
# a long list invites trust it cannot earn (see the module docstring).
_DENIED: tuple[tuple[str, str], ...] = (
    (r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME|\*)", "递归删除 / 或 ~ 或 *"),
    (r"\b(del|rd|rmdir)\s+/[sq]\b", "Windows 递归删除"),
    (r"\bgit\s+push\b", "git push（提交/推送由你自己来）"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard（会丢改动）"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*[fdx]", "git clean（会删未跟踪文件）"),
    (r"\bgit\s+checkout\s+--\s+\.", "git checkout 覆盖整个工作区"),
    (r"\bsudo\b", "sudo"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "关机/重启"),
    (r"\bmkfs|\bdd\s+if=|>\s*/dev/(sd|nvme|hd)", "直接写块设备"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\bcurl\b[^|;&]*\|\s*(ba|z)?sh\b", "把网上下载的内容直接喂给 shell"),
    (r"\bwget\b[^|;&]*\|\s*(ba|z)?sh\b", "把网上下载的内容直接喂给 shell"),
    (r":\s*\(\s*\)\s*\{.*\}\s*;?\s*:", "fork 炸弹"),
)
# Names that look like credentials. KnowMe itself reads .env into the process
# environment, so a command that dumps the environment would hand the model
# exactly what read_file refuses to show it. Cheap, honest, and stated in the
# tool description — this is hygiene, not a sandbox.
_SECRET_ENV_RE = re.compile(r"(API_?KEY|_TOKEN|SECRET|PASSWORD|_PASSWD|_PWD)", re.IGNORECASE)

_WRITE_OFF = (
    "写文件和跑命令的开关现在是关的（.knowme/coding.json 里的 allow_write）。"
    "请告诉用户：在 Coding Workspace 页面上打开「允许写和跑命令」之后重试。"
    "读文件、搜索和 git 查看现在仍然可用。"
)


def _command_env() -> dict[str, str]:
    """The environment a model-chosen command runs with."""
    return {k: v for k, v in delegate_env().items() if not _SECRET_ENV_RE.search(k)}


def _resolve(root: Path, relative: str, *, writing: bool = False) -> Path:
    """A model-supplied relative path resolved inside the root, or ValueError.

    A leading '-' is refused outright: these paths travel on to git as argv
    elements, and `-x` there is a flag, not a filename (the same argument
    github.py makes for matching its repo argument against a regex).
    """
    raw = (relative or "").strip()
    if Path(raw).is_absolute():
        # _relative_path would catch this too (an absolute path replaces the
        # root), but saying so beats "outside the project root".
        raise ValueError("请给相对路径，例如 src/app.py")
    if raw.startswith("-"):
        raise ValueError("路径不能以 - 开头")
    try:
        path = _relative_path(root, raw)
    except ValueError as exc:
        raise ValueError(f"「{raw}」不在项目根目录 {root} 里面") from exc
    parts = path.relative_to(root).parts
    blocked = [part for part in parts if part in SKIP_DIRECTORIES]
    if blocked:
        raise ValueError(f"{blocked[0]}/ 不在可访问范围内")
    if writing and path.name in SKIP_FILES:
        raise ValueError(f"{path.name} 是密钥文件，工具不改它——请用户自己用编辑器改")
    return path


def _check_readable(path: Path) -> None:
    """Reads are for source files, not for secrets and not for binaries."""
    if path.name in SKIP_FILES:
        raise ValueError(f"{path.name} 是密钥文件，不通过工具读取")
    if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix:
        raise ValueError(f"{path.suffix} 不是常见文本后缀，工具不读它")
    try:
        head = path.read_bytes()[:4096]
    except OSError as exc:
        raise ValueError(f"读不了这个文件：{exc}") from exc
    if b"\x00" in head:
        raise ValueError("这看起来是二进制文件")


def _walk(root: Path, start: Path):
    """Directories then files, one level at a time, minus the noise.

    Same rule as the Coding page's tree (coding_workspace.list_entries): the
    big generated directories and any dot-directory are skipped while
    DISCOVERING. A file inside one is still readable if you name it — the walk
    is curated, direct access is not.
    """
    for current, directories, files in os.walk(start):
        directories[:] = sorted(d for d in directories
                                if d not in SKIP_DIRECTORIES and not d.startswith("."))
        yield Path(current), sorted(files)


def _clip(text: str, limit: int, what: str = "内容") -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… {what}还有 {len(text) - limit} 字，已截断"


def _head_tail(text: str, limit: int) -> str:
    """Command output: keep both ends. pytest's summary is last, a compiler's
    first error is first, and dropping either one costs the model a rerun."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n…（中间省略 {len(text) - limit} 字）…\n{text[-half:]}"


class _Project:
    """One turn's view of the project: where it is, who is asking, what is allowed.

    Settings, connection and session are read at CALL time, not captured at
    build time — the write switch and the active thread both change without a
    rebuild, and a captured session id would point at the thread you were in
    when the process started.
    """

    def __init__(self, settings, conn, session=None, agent_id: str = "default"):
        self.settings = settings
        self.conn = conn
        self.session = session
        self.agent_id = agent_id

    @property
    def root(self) -> Path:
        return project_root()

    @property
    def session_id(self) -> str:
        return getattr(self.session, "session_id", "") or ""

    def write_allowed(self) -> bool:
        try:
            return bool(load_coding_settings(self.settings.home).get("allow_write"))
        except (OSError, ValueError):
            return False

    def record(self, kind: str, target: str, **kwargs) -> None:
        """Best effort: a receipt that fails must never fail the change."""
        try:
            coding_runs.record(self.conn, self.settings.home, self.root, kind, target,
                               session_id=self.session_id, agent_id=self.agent_id,
                               **kwargs)
        except Exception:
            pass  # see the module docstring: the receipt is evidence, not a gate

    def ensure_baseline(self) -> None:
        """Take the before-picture, BEFORE the first change of this session.

        Called at the top of every mutating tool rather than left to record():
        by the time a change is recorded it is already on disk, so a baseline
        taken then would contain it — and the baseline is what an undo restores
        to, so it has to be the state the change was made FROM.
        """
        try:
            coding_runs.baseline_for(self.conn, self.settings.home, self.root,
                                     self.session_id, self.agent_id)
        except Exception:
            pass

    # -- reads ---------------------------------------------------------------

    def list_files(self, path: str = "") -> str:
        root = self.root
        base = _resolve(root, path)
        if not base.is_dir():
            raise ValueError(f"{path or '.'} 不是目录")
        directories, files = [], []
        for name in sorted(os.listdir(base)):
            if (base / name).is_dir():
                # Same noise rule as the page's tree: generated directories and
                # dot-directories are not listed (a file inside one is still
                # readable if you name it).
                if name not in SKIP_DIRECTORIES and not name.startswith("."):
                    directories.append(f"{name}/")
            elif name not in SKIP_FILES:
                # .env and its siblings are neither listed nor readable, so the
                # model cannot report a secret it has no use for either.
                files.append(name)
        header = f"{base.relative_to(root).as_posix() or '.'}/"
        listed = directories + files
        if not listed:
            return f"{header}\n(空目录)"
        return "\n".join([header, *(f"  {name}" for name in listed)])

    def read_file(self, path: str, start: int = 1, end: int = 0) -> str:
        target = _resolve(self.root, path)
        if not target.is_file():
            raise ValueError(f"没有这个文件：{path}")
        _check_readable(target)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(1, int(start or 1))
        last = int(end) if end else first + DEFAULT_READ_LINES - 1
        last = min(last, first + MAX_READ_LINES - 1, len(lines))
        if first > len(lines):
            return f"{path} 只有 {len(lines)} 行，第 {first} 行之后没有内容"
        # Line numbers are for reading, not for copying: edit_file wants the
        # text itself, which is why they are outside the text column.
        body = "\n".join(f"{n:>5} | {lines[n - 1]}" for n in range(first, last + 1))
        note = "" if last >= len(lines) else f"\n…（共 {len(lines)} 行，这里到第 {last} 行）"
        return _clip(body + note, MAX_FILE_CHARS, "文件")

    def search_files(self, query: str, path: str = "", glob: str = "") -> str:
        if not query:
            raise ValueError("query 不能为空")
        root = self.root
        base = _resolve(root, path)
        if not base.is_dir():
            raise ValueError(f"{path or '.'} 不是目录")
        needle = query.lower()
        hits: list[str] = []
        for current, files in _walk(root, base):
            for name in files:
                if glob and not fnmatch.fnmatch(name, glob):
                    continue
                target = current / name
                if target.name in SKIP_FILES or target.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                try:
                    text = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                relative = target.relative_to(root).as_posix()
                for number, line in enumerate(text.splitlines(), 1):
                    if needle in line.lower():
                        hits.append(f"{relative}:{number}: {line.strip()[:200]}")
                        if len(hits) >= MAX_MATCHES:
                            hits.append(f"…（只显示前 {MAX_MATCHES} 条）")
                            return "\n".join(hits)
        return "\n".join(hits) if hits else f"没有找到「{query}」"

    def git_status(self) -> str:
        root = self.root
        status = coding_runs.git(root, "status", "--porcelain", "--untracked-files=all")
        if status is None:
            return "这台机器上没有 git。"
        if status.returncode != 0:
            return f"这里不是 git 仓库（{status.output.strip()[:200]}）"
        summary = coding_runs.git(root, "diff", "--shortstat", "HEAD", "--", ".")
        stat = summary.output.strip() if summary is not None and summary.returncode == 0 else ""
        body = status.output.strip() or "工作区是干净的，没有未提交的改动。"
        return _clip(f"{body}\n\n{stat}".strip(), MAX_DIFF_CHARS, "输出")

    def git_diff(self, path: str = "") -> str:
        root = self.root
        args = ["diff", "HEAD", "--", path or "."]
        if path:
            _resolve(root, path)  # validates; git gets the same relative string
        diff = coding_runs.git(root, *args)
        if diff is None:
            return "这台机器上没有 git。"
        if diff.returncode != 0:
            return f"看不了 diff（{diff.output.strip()[:200]}）"
        if not diff.output.strip():
            return "工作区没有未提交的改动。未跟踪的新文件不在 diff 里，用 git_status 看。"
        return _clip(diff.output, MAX_DIFF_CHARS, "diff")

    # -- writes --------------------------------------------------------------

    def write_file(self, path: str, content: str) -> str:
        root = self.root
        self.ensure_baseline()
        target = _resolve(root, path, writing=True)
        before = ""
        if target.is_file():
            if b"\x00" in target.read_bytes()[:4096]:
                # A diff of a binary is unreadable and cannot be undone by
                # hand, so this is the one file kind the receipt cannot cover.
                raise ValueError(f"{path} 看起来是二进制文件，工具不覆盖它")
            before = target.read_text(encoding="utf-8", errors="replace")
        if before == content:
            return f"{path} 内容没变，没有写入。"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        relative = target.relative_to(root).as_posix()
        patch = coding_runs.unified_diff(before, content, relative)
        self.record("write", relative, before=before, after=content,
                    summary=("新建文件" if not before else "覆盖"))
        return _wrote(relative, patch)

    def edit_file(self, path: str, old_text: str, new_text: str,
                  replace_all: bool = False) -> str:
        root = self.root
        self.ensure_baseline()
        target = _resolve(root, path, writing=True)
        if not target.is_file():
            raise ValueError(f"没有这个文件：{path}")
        if b"\x00" in target.read_bytes()[:4096]:
            raise ValueError(f"{path} 看起来是二进制文件，工具不改它")
        before = target.read_text(encoding="utf-8", errors="replace")
        if not old_text:
            raise ValueError("old_text 不能为空；要整份覆盖请用 write_file")
        found = before.count(old_text)
        if found == 0:
            raise ValueError(
                f"在 {path} 里没有找到这段原文，文件没有改动。"
                "请先用 read_file 看一眼当前内容（注意缩进和空格要一模一样）。")
        if found > 1 and not replace_all:
            raise ValueError(
                f"这段原文在 {path} 里出现了 {found} 次，文件没有改动。"
                "请把 old_text 写得更长一些（带上前后文）以确保唯一，"
                "或者传 replace_all=true 表示全部替换。")
        after = before.replace(old_text, new_text) if replace_all \
            else before.replace(old_text, new_text, 1)
        target.write_text(after, encoding="utf-8")
        relative = target.relative_to(root).as_posix()
        patch = coding_runs.unified_diff(before, after, relative)
        replaced = found if replace_all else 1
        self.record("edit", relative, before=before, after=after,
                    summary=f"替换 {replaced} 处")
        # The count belongs in the answer, not just the receipt: with
        # replace_all a model that guessed "two places" needs to be told.
        return f"替换了 {replaced} 处。\n" + _wrote(relative, patch)

    def run_command(self, command: str, timeout_seconds: int = DEFAULT_TIMEOUT) -> str:
        self.ensure_baseline()
        for pattern, why in _DENIED:
            if re.search(pattern, command, re.IGNORECASE):
                return (f"拒绝执行：这条命令命中了黑名单（{why}）。\n"
                        "黑名单只是防手滑，不是沙箱。确实要跑的话请用户自己在终端里跑。")
        root = self.root
        timeout = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT), MAX_TIMEOUT))
        try:
            result = subprocess.run(command, shell=True, cwd=root, text=True,
                                    capture_output=True, check=False,
                                    timeout=timeout, env=_command_env())
        except subprocess.TimeoutExpired:
            self.record("command", command, summary=f"超时（{timeout} 秒）",
                        command=command, output="", returncode=None)
            return (f"命令超过 {timeout} 秒还没结束，已经放弃等待。"
                    "默认超时 60 秒，最多 600 秒——长任务可以在 timeout_seconds 里加大。")
        except OSError as exc:
            return f"命令起不来：{exc}"
        output = (result.stdout or "") + (result.stderr or "")
        self.record("command", command, command=command, output=output,
                    returncode=result.returncode)
        body = _head_tail(output.strip(), MAX_OUTPUT_CHARS) or "(没有输出)"
        return f"退出码 {result.returncode}\n{body}"


def _guard(fn, *args, **kwargs) -> str:
    """A refusal is a sentence the model can act on, not a traceback.

    Same contract as github.py: the loop must survive a bad path, and the model
    needs to know which of "outside the project" / "no such file" / "the switch
    is off" happened.
    """
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        return f"没能执行：{exc}"
    except OSError as exc:
        return f"没能执行：{exc}"


def _wrote(relative: str, patch: str) -> str:
    """What the model sees after a write: the diff, not a happy sentence."""
    if not patch:
        return f"{relative} 已写入（内容与之前相同）。"
    insertions, deletions = coding_runs.counts(patch)
    return (f"{relative} 已写入（+{insertions} −{deletions}）：\n"
            + _clip(patch, MAX_DIFF_CHARS, "diff"))


def make_tools(settings, conn, session=None, agent_id: str = "default") -> list[Tool]:
    """The eight tools. Writes are refused in ONE place — here — so the switch
    cannot be half-applied. `project` is built per call for the same reason."""
    def project() -> _Project:
        return _Project(settings, conn, session=session, agent_id=agent_id)

    def gated(fn):
        def call(**kwargs) -> str:
            if not project().write_allowed():
                return _WRITE_OFF
            return fn(project(), **kwargs)
        return call

    def free(fn):
        return lambda **kwargs: fn(project(), **kwargs)

    return [
        Tool(
            name="list_files",
            description=(
                "列出项目里的目录和文件（一层，不是递归）。项目根目录由用户设定，"
                "工具只能在这个目录里活动；.git/.knowme/.venv 之类的目录不列出。"
            ),
            input_schema={"type": "object", "properties": {
                "path": {"type": "string",
                         "description": "相对项目根的目录，留空就是根目录"},
            }},
            fn=free(lambda p, path="": _guard(p.list_files, path)),
        ),
        Tool(
            name="read_file",
            description=(
                "读项目里的一个文本文件，返回带行号的内容（行号只供你定位，编辑时要写原文）。"
                "默认读前 400 行，最多 2000 行。读不了 .env 这类密钥文件，也读不了二进制。"
            ),
            input_schema={"type": "object", "properties": {
                "path": {"type": "string", "description": "相对项目根的路径，例如 src/app.py"},
                "start": {"type": "integer", "description": "从第几行开始，默认 1"},
                "end": {"type": "integer", "description": "读到第几行，默认 start+399"},
            }, "required": ["path"]},
            fn=free(lambda p, path, start=1, end=0: _guard(p.read_file, path, start, end)),
        ),
        Tool(
            name="search_files",
            description=(
                "在项目里按纯文本（不区分大小写，不是正则）搜索，返回 `文件:行号: 内容`，最多 100 条。"
            ),
            input_schema={"type": "object", "properties": {
                "query": {"type": "string", "description": "要找的文字"},
                "path": {"type": "string", "description": "只在哪个子目录里找，默认整个项目"},
                "glob": {"type": "string", "description": "只看匹配的文件名，例如 *.py"},
            }, "required": ["query"]},
            fn=free(lambda p, query, path="", glob="": _guard(
                p.search_files, query, path, glob)),
        ),
        Tool(
            name="git_status",
            description="看项目当前的 git 状态：哪些文件改了、哪些是新文件，以及改动行数统计。",
            input_schema={"type": "object", "properties": {}},
            fn=free(lambda p: _guard(p.git_status)),
        ),
        Tool(
            name="git_diff",
            description=(
                "看未提交的改动内容（相对 HEAD 的 diff）。新建的未跟踪文件不在里面，用 git_status 看。"
            ),
            input_schema={"type": "object", "properties": {
                "path": {"type": "string", "description": "只看某个文件/目录，默认整个项目"},
            }},
            fn=free(lambda p, path="": _guard(p.git_diff, path)),
        ),
        Tool(
            name="write_file",
            description=(
                "写入或整体覆盖项目里的一个文本文件（会自己建目录）。返回这次写入的 diff。"
                "需要用户先在 Coding Workspace 打开「允许写和跑命令」，否则会被拒绝。"
                "只改文件内容，不会 git 提交。"
            ),
            input_schema={"type": "object", "properties": {
                "path": {"type": "string", "description": "相对项目根的路径"},
                "content": {"type": "string", "description": "完整的新内容"},
            }, "required": ["path", "content"]},
            fn=gated(lambda p, path, content: _guard(p.write_file, path, content)),
        ),
        Tool(
            name="edit_file",
            description=(
                "把文件里的一段原文精确替换掉（old_text 必须和文件里一字不差）。"
                "找不到、或者出现了不止一次，都会拒绝并且不碰文件——这时先用 read_file 看当前内容。"
                "需要用户先打开「允许写和跑命令」。"
            ),
            input_schema={"type": "object", "properties": {
                "path": {"type": "string", "description": "相对项目根的路径"},
                "old_text": {"type": "string", "description": "要被替换掉的原文，含缩进"},
                "new_text": {"type": "string", "description": "替换成什么"},
                "replace_all": {"type": "boolean", "description": "true 表示这一处全部替换"},
            }, "required": ["path", "old_text", "new_text"]},
            fn=gated(lambda p, path, old_text, new_text, replace_all=False: _guard(
                p.edit_file, path, old_text, new_text, replace_all)),
        ),
        Tool(
            name="run_command",
            description=(
                "在项目根目录里跑一条 shell 命令（比如 pytest、npm test），返回退出码和输出。"
                "默认 60 秒超时，最长 600 秒。需要用户先打开「允许写和跑命令」。"
                "危险命令（递归删根、git push、sudo 等）会被黑名单拒绝——但黑名单不是沙箱。"
                "跑命令时会剥掉名字像密钥的环境变量。"
            ),
            input_schema={"type": "object", "properties": {
                "command": {"type": "string", "description": "要跑的命令行"},
                "timeout_seconds": {"type": "integer", "description": "超时秒数，默认 60，最多 600"},
            }, "required": ["command"]},
            fn=gated(lambda p, command, timeout_seconds=DEFAULT_TIMEOUT: _guard(
                p.run_command, command, timeout_seconds)),
        ),
    ]


__all__ = ["MAX_MATCHES", "MAX_READ_LINES", "make_tools"]

"""Small, safe building blocks for evaluating delegated coding work.

The evaluator deliberately runs commands without a shell and passes the same
filtered environment used by ``delegate_task``. That keeps API keys and other
denied variables out of code written by a delegated agent.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from knowme.tools._env import delegate_env as _delegate_env


@dataclass(frozen=True)
class CodingResult:
    """The stable result shape returned for one coding evaluation step."""

    command: tuple[str, ...]
    returncode: int | None
    output: str
    timed_out: bool = False


def run_command(command: Sequence[str], cwd: Path, timeout: int = 30) -> CodingResult:
    """Run one executable without a shell and capture its output."""
    argv = tuple(str(part) for part in command)
    try:
        result = subprocess.run(
            list(argv), cwd=cwd, capture_output=True, text=True,
            timeout=timeout, check=False, env=_delegate_env(),
        )
    except subprocess.TimeoutExpired as exc:
        output = "".join(str(part) for part in (exc.stdout, exc.stderr) if part)
        return CodingResult(argv, None, output, timed_out=True)
    return CodingResult(argv, result.returncode, result.stdout + result.stderr)


def run_python(script: Path, cwd: Path, timeout: int = 30) -> CodingResult:
    """Run a generated Python file with the current interpreter."""
    argv = (sys.executable, str(script))
    try:
        result = subprocess.run(
            list(argv), cwd=cwd, capture_output=True, text=True,
            timeout=timeout, check=False, env=_delegate_env(),
        )
    except subprocess.TimeoutExpired as exc:
        output = "".join(str(part) for part in (exc.stdout, exc.stderr) if part)
        return CodingResult(argv, None, output, timed_out=True)
    return CodingResult(argv, result.returncode, result.stdout + result.stderr)


def run_suite(commands: Sequence[Sequence[str]], cwd: Path, timeout: int = 30) -> list[CodingResult]:
    """Run a small command suite, stopping after the first failed step."""
    results: list[CodingResult] = []
    for command in commands:
        process = subprocess.Popen(
            [str(part) for part in command], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=_delegate_env(),
        )
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            results.append(CodingResult(tuple(str(part) for part in command), None, output, True))
            break
        result = CodingResult(tuple(str(part) for part in command), process.returncode, output)
        results.append(result)
        if result.returncode != 0:
            break
    return results

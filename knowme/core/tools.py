"""Tool registry — the 'Agentic Tools' box on the whiteboard.

A tool is three things: a name+description the model reads, a JSON schema for
its arguments, and a Python function that runs. That's it. (Registry pattern
adapted from launch-agentic-rag's app/agents/tools/registry.py.)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def as_text(output: object) -> str:
    """A tool's result in the ONE form the rest of the system reads it in.

    `Tool.fn` is declared `-> str` and almost every tool obeys, but not all: the
    notes tools (`search_notes`, `list_notes`, `list_folders`) return Python
    lists. Everything downstream nevertheless treats a tool result as text —
    the trace writes it, `meta.tools` classifies it with `.lower()`, the web
    payload summarises it with `.split(". ")`, the CLI slices it with `[:80]`.

    One list therefore took the whole dashboard down: `/api/data` raised
    AttributeError in the middle of `collect()`, answered with an empty
    connection instead of JSON, and the page could only report
    `TypeError: Failed to fetch` — with no clue which tool or which Agent.

    This is about the RECORD of the call, not the call: the model still receives
    whatever the tool returned.
    """
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(output)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., str]  # tools return a string the model observes
    # A long-running tool can opt in to STREAM progress while it works: set
    # wants_notify=True and accept a `_notify(kind, event)` keyword. The loop's
    # observer is passed through, so gateways/traces see inside the tool
    # (delegate_task uses this to relay pi's live events). The underscore keeps
    # it out of the model-facing schema — the model never supplies it.
    wants_notify: bool = False

    def to_api(self) -> dict[str, Any]:
        """The shape the Messages API expects in its `tools=` parameter."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_api() for t in self._tools.values()]

    def subset(self, names) -> ToolRegistry:
        """A registry holding only `names` — an agent's allowlist applied to the
        full set. Tools are shared, not copied; only the membership differs.
        Unknown names are ignored rather than raised: a spec written for a
        tool that is switched off (apple_tools, gh_tool) should still run."""
        out = ToolRegistry()
        for name in names:
            if name in self._tools:
                out.register(self._tools[name])
        return out

    def execute(self, name: str, args: dict[str, Any], notify=None) -> str:
        """Run one tool call safely: the model observes errors as text instead
        of crashing the loop (execute_tool_safely pattern)."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            if tool.wants_notify:
                return tool.fn(**args, _notify=notify or (lambda kind, ev: None))
            return tool.fn(**args)
        except Exception as exc:  # surface, don't crash — the model can retry
            return f"Error running {name}: {exc}"

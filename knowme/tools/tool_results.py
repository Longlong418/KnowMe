"""read_tool_result — the way back to a tool output that was compressed.

Compression is only safe because it is reversible. runtime/tool_budget.py
replaces a long tool result with a pointer plus an excerpt before it enters the
history; this is the other half of that promise. Without it the pointer would
name a file the agent has no way to open — the agent has no general file-reading
tool — and "the full output is on disk" would be a black hole.

The id is a bare filename, resolved under KNOWME_HOME/tool_results/. Taking an
id rather than a path keeps the prompt short and makes traversal impossible by
construction: anything containing a separator or ".." is rejected before any
path is built.
"""

from __future__ import annotations

from pathlib import Path

from knowme.core.context.tool_budget import STORED_DIR
from knowme.core.tools import Tool


def make_tool(home: Path) -> Tool:
    def read_tool_result(id: str) -> str:
        name = (id or "").strip()
        if not name or any(ch in name for ch in "/\\") or ".." in name:
            return (f"'{id}' is not a tool-result id — pass the bare filename shown "
                    f"in the compression notice.")
        path = home / STORED_DIR / name
        if not path.is_file():
            return f"No stored tool result named '{name}'."
        return path.read_text(encoding="utf-8")

    return Tool(
        name="read_tool_result",
        description=(
            "Read back the full output of an earlier tool call that was too long to "
            "keep in your history, so only an excerpt of it was kept. Use the id shown "
            "in the 'full output saved to ...' notice. Call this instead of guessing "
            "when a past result looks truncated."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string",
                       "description": "the bare filename from the compression notice"},
            },
            "required": ["id"],
        },
        fn=read_tool_result,
    )

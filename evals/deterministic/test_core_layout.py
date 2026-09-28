"""The core layout — two rules the platform refactor depends on.

1. Every OLD path is an alias of its NEW path: the same module object, not a
   copy. A copy would mean a monkeypatch on `knowme.runtime.session` misses
   the code `knowme.core.session` actually runs — and every existing test that
   patches through the old path would pass while testing nothing.

2. knowme.core imports nothing from knowme.ops / knowme.graph / knowme.gateway.
   Those are consumers of the core; a core that reaches back into its consumers
   cannot be reused by a second agent, a second app, or a second gateway.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

ALIASES = {
    "knowme.loop.agent": "knowme.core.loop",
    "knowme.loop.models": "knowme.core.models",
    "knowme.tools.registry": "knowme.core.tools",
    "knowme.runtime.session": "knowme.core.session",
    "knowme.runtime.tool_entries": "knowme.core.context.tool_entries",
    "knowme.runtime.tool_budget": "knowme.core.context.tool_budget",
    "knowme.runtime.snip_compact": "knowme.core.context.snip_compact",
    "knowme.runtime.micro_compact": "knowme.core.context.micro_compact",
    "knowme.runtime.state_summary": "knowme.core.context.state_summary",
}


@pytest.mark.parametrize(("old", "new"), sorted(ALIASES.items()))
def test_old_path_is_the_new_module(old: str, new: str) -> None:
    assert importlib.import_module(old) is importlib.import_module(new)


def test_core_does_not_import_its_consumers() -> None:
    # A fresh interpreter, so nothing another test imported can mask a leak.
    code = (
        "import sys, knowme.core.loop, knowme.core.models, knowme.core.tools, "
        "knowme.core.session, knowme.core.events, knowme.core.context.tool_budget, "
        "knowme.core.context.snip_compact, knowme.core.context.micro_compact, "
        "knowme.core.context.state_summary; "
        "print(sorted(m for m in sys.modules if m.startswith(('knowme.ops', "
        "'knowme.graph', 'knowme.gateway'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "[]", out.stdout

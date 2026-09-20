"""Moved to knowme.core.context.tool_budget.

This alias keeps old imports AND monkeypatches working through Phase 1 of the
platform refactor: after the line below, `import knowme.runtime.tool_budget`
and `import knowme.core.context.tool_budget` are the SAME module object, so a
test that patches an attribute on one is patching the other.
"""

import sys

from knowme.core.context import tool_budget as _target

sys.modules[__name__] = _target

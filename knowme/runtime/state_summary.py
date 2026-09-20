"""Moved to knowme.core.context.state_summary.

This alias keeps old imports AND monkeypatches working through Phase 1 of the
platform refactor: after the line below, `import knowme.runtime.state_summary`
and `import knowme.core.context.state_summary` are the SAME module object, so a
test that patches an attribute on one is patching the other.
"""

import sys

from knowme.core.context import state_summary as _target

sys.modules[__name__] = _target

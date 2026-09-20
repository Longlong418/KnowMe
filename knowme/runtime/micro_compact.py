"""Moved to knowme.core.context.micro_compact.

This alias keeps old imports AND monkeypatches working through Phase 1 of the
platform refactor: after the line below, `import knowme.runtime.micro_compact`
and `import knowme.core.context.micro_compact` are the SAME module object, so a
test that patches an attribute on one is patching the other.
"""

import sys

from knowme.core.context import micro_compact as _target

sys.modules[__name__] = _target

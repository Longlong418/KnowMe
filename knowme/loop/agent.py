"""Moved to knowme.core.loop.

This alias keeps old imports AND monkeypatches working through Phase 1 of the
platform refactor: after the line below, `import knowme.loop.agent` and
`import knowme.core.loop` are the SAME module object, so a test that patches an
attribute on one is patching the other.
"""

import sys

from knowme.core import loop as _target

sys.modules[__name__] = _target

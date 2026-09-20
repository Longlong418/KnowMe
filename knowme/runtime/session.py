"""Moved to knowme.core.session.

This alias keeps old imports AND monkeypatches working through Phase 1 of the
platform refactor: after the line below, `import knowme.runtime.session` and
`import knowme.core.session` are the SAME module object, so a test that patches
an attribute on one is patching the other.
"""

import sys

from knowme.core import session as _target

sys.modules[__name__] = _target

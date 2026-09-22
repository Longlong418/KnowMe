"""knowme-agent — a minimal, transparent, local-first KnowMe.

The implementation lives under ``knowme.core``.  The aliases below are kept
in one place for callers written before the core layout was introduced; new
code should import ``knowme.core`` directly.
"""

import sys

from knowme.core import context as _context
from knowme.core import loop as _loop
from knowme.core import models as _models
from knowme.core import session as _session
from knowme.core import tools as _tools
from knowme.core.context import (
    micro_compact as _micro_compact,
)
from knowme.core.context import (
    snip_compact as _snip_compact,
)
from knowme.core.context import (
    state_summary as _state_summary,
)
from knowme.core.context import (
    tool_budget as _tool_budget,
)
from knowme.core.context import (
    tool_entries as _tool_entries,
)

# Legacy imports remain valid without keeping a second source tree.  This is a
# compatibility map, not another implementation: every name points at the
# canonical module in ``knowme.core``.
for _legacy, _canonical in {
    "knowme.loop": "knowme.core",
    "knowme.loop.agent": "knowme.core.loop",
    "knowme.loop.models": "knowme.core.models",
    "knowme.runtime": "knowme.core",
    "knowme.runtime.session": "knowme.core.session",
    "knowme.runtime.micro_compact": "knowme.core.context.micro_compact",
    "knowme.runtime.snip_compact": "knowme.core.context.snip_compact",
    "knowme.runtime.state_summary": "knowme.core.context.state_summary",
    "knowme.runtime.tool_budget": "knowme.core.context.tool_budget",
    "knowme.runtime.tool_entries": "knowme.core.context.tool_entries",
}.items():
    sys.modules.setdefault(_legacy, sys.modules[_canonical])

# Package-style imports such as ``from knowme.runtime import tool_entries``
# also need attributes on the aliased package object.
sys.modules["knowme.loop"].agent = _loop
sys.modules["knowme.loop"].models = _models
sys.modules["knowme.runtime"].session = _session
for _name, _module in {
    "micro_compact": _micro_compact,
    "snip_compact": _snip_compact,
    "state_summary": _state_summary,
    "tool_budget": _tool_budget,
    "tool_entries": _tool_entries,
}.items():
    sys.modules["knowme.runtime"].__dict__[_name] = _module

del _legacy, _canonical, _loop, _models, _session, _tools, _context, _name, _module
del _micro_compact, _snip_compact, _state_summary, _tool_budget, _tool_entries

__version__ = "0.1.0"

"""Operational adapters and the local Web client.

``dashboard`` remains a module alias for one release so existing integrations
and persisted test harnesses can migrate without keeping a second server file.
"""

import sys

from knowme.ops import web as dashboard
from knowme.ops import web_server

sys.modules.setdefault("knowme.ops.dashboard", dashboard)
# Existing route tests and third-party diagnostics inspect the old module's
# source path. Point that compatibility name at the canonical HTTP transport,
# where the route table now lives.
dashboard.__file__ = web_server.__file__

__all__ = ["dashboard", "web"]

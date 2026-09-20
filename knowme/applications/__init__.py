"""Application-side building blocks.

Applications are workspaces (Reader, Knowledge, Memory, Coding), not agents.
They contribute current state to an agent turn through ``ApplicationContextBridge``.
"""

from knowme.applications.context_bridge import ApplicationContextBridge, ApplicationState

__all__ = ["ApplicationContextBridge", "ApplicationState"]

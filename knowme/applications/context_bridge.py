"""A small, explicit bridge between an Application and an Agent turn.

The browser can change quickly while an agent is running.  Passing arbitrary UI
objects into the core would couple the loop to the frontend, so Applications
publish a plain snapshot instead.  The runtime receives only the rendered text
for the current ``(agent, session)`` pair.

This store is deliberately process-local for now: it represents transient UI
state, not durable user knowledge.  Notes and memories belong in their own
stores; an open document and a selection do not.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ApplicationState:
    application: str
    resource: str = ""
    content: str = ""
    selection: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApplicationContextBridge:
    """Thread-safe transient Application state, isolated by agent and session."""

    def __init__(self, *, max_content_chars: int = 24_000,
                 max_selection_chars: int = 8_000) -> None:
        self.max_content_chars = max_content_chars
        self.max_selection_chars = max_selection_chars
        self._states: dict[tuple[str, str], ApplicationState] = {}
        self._lock = threading.RLock()

    def publish(self, *, agent_id: str, session_id: str, application: str,
                resource: str = "", content: str = "", selection: str = "",
                metadata: dict[str, Any] | None = None) -> ApplicationState:
        """Replace the current snapshot for one agent conversation.

        Bounds are applied here, at the trust boundary, so a dropped multi-MB
        file cannot accidentally become a multi-MB model request.
        """
        state = ApplicationState(
            application=(application or "unknown").strip()[:80],
            resource=(resource or "").strip()[:1_000],
            content=(content or "")[: self.max_content_chars],
            selection=(selection or "")[: self.max_selection_chars],
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._states[(agent_id or "default", session_id or "default")] = state
        return state

    def get(self, agent_id: str, session_id: str) -> ApplicationState | None:
        with self._lock:
            return self._states.get((agent_id or "default", session_id or "default"))

    def clear(self, agent_id: str, session_id: str) -> bool:
        with self._lock:
            return self._states.pop(
                (agent_id or "default", session_id or "default"), None
            ) is not None

    def rekey(self, agent_id: str, old_session: str, new_session: str) -> bool:
        """Move one agent's snapshot to another thread id.

        The dashboard rotates an agent's thread after an idle gap, and that
        rotation lands at the START of the next turn — after the Application
        already published (the reader publishes when you open a document).
        Without this the snapshot stayed filed under the old thread and the new
        one found nothing: the document you had open the whole time came off
        exactly when you asked about it.

        The snapshot describes what is on SCREEN, not what was said, so it
        belongs to whichever thread the agent is about to answer in.
        """
        key = (agent_id or "default", old_session or "default")
        new_key = (agent_id or "default", new_session or "default")
        with self._lock:
            state = self._states.pop(key, None)
            if state is None:
                return False
            self._states[new_key] = state
            return True

    def render(self, agent_id: str, session_id: str) -> str:
        """Render a snapshot as an unambiguous, model-facing context block."""
        state = self.get(agent_id, session_id)
        if state is None:
            return ""
        parts = [
            "[application context]",
            f"Application: {state.application}",
        ]
        if state.resource:
            parts.append(f"Current resource: {state.resource}")
        if state.content:
            parts.extend(("Document content:", state.content))
        if state.selection:
            parts.extend(("Current selection:", state.selection))
        if state.metadata:
            safe_meta = ", ".join(
                f"{key}={value}" for key, value in sorted(state.metadata.items())
                if isinstance(value, (str, int, float, bool))
            )
            if safe_meta:
                parts.append("Application state: " + safe_meta[:2_000])
        return "\n".join(parts)

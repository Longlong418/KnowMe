"""The observer protocol — one signature shared by the loop, the graph engine,
the tracer and every gateway.

    notify(kind: str, event: dict) -> None

Kinds the harness emits today: turn_start, gate, llm, tool, text, route, triage,
graph_start, node_start, node_end, graph_end, consolidation, turn_end. Observers
are composed, not chained — every one of them sees every event.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

LoopEvent = dict[str, Any]
Observer = Callable[[str, LoopEvent], None]


def compose(*observers: Observer | None) -> Observer:
    """Fan one event out to several observers; None entries are skipped, so a
    caller can pass an optional gateway observer straight through."""
    live = [o for o in observers if o is not None]

    def notify(kind: str, event: LoopEvent) -> None:
        for o in live:
            o(kind, event)

    return notify

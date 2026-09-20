"""AgentSpec — what makes one agent different from another.

The platform plan's one-line definition, as a dataclass:

    Agent = Prompt + Tools + Memory + Context Policy + Model

Every field is optional and None means "whatever Settings says", so the
DEFAULT_SPEC below is exactly the single agent this repo has always run. A
second agent is a second instance of this with some fields filled in — it does
not get its own loop, its own compressors or its own session code, because the
AgentRuntime (core/runtime.py) runs any spec the same way.

Phase 0 wires the spec through the runtime without changing behaviour. The
fields that only mean something once there are several agents (tools as an
allowlist, memory_scope) are declared now so the shape is settled, and marked
where the runtime does not read them yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from knowme.config import Settings

if TYPE_CHECKING:
    from knowme.core.context.policy import ContextPolicy


@dataclass(frozen=True)
class AgentSpec:
    name: str = "default"
    # None → SOUL.md from the home dir (load_soul), which is what the default
    # agent has always used. A string here is the agent's own persona.
    system_prompt: str | None = None
    # None → every registered tool. A set → only those names (Phase 1: the
    # runtime does not apply it yet; ToolRegistry.subset is the mechanism).
    tools: frozenset[str] | None = None
    # Which memory this agent reads and writes. "shared" is today's single
    # store; Phase 1 gives facts/episodes/chat_log an agent dimension.
    memory_scope: str = "shared"
    # How the request is fitted to the window. None → default_policy(), the
    # tool_budget → micro_compact → state_summary chain.
    context_policy: ContextPolicy | None = None
    model: str | None = None
    small_model: str | None = None
    max_iterations: int | None = None
    max_tokens: int | None = None


DEFAULT_SPEC = AgentSpec()


@dataclass(frozen=True)
class ResolvedSpec:
    """A spec with every None filled from Settings — what the runtime reads.

    Kept separate from AgentSpec so a spec stays a declaration ("no opinion on
    the model") and a resolved spec is a fact ("deepseek-v4-pro"). Resolving is
    cheap and done per turn, so a Settings change is live on the next turn the
    same way it always was.
    """

    name: str
    system_prompt: str | None
    tools: frozenset[str] | None
    memory_scope: str
    context_policy: ContextPolicy
    model: str
    small_model: str
    max_iterations: int
    max_tokens: int


def resolve(spec: AgentSpec, settings: Settings) -> ResolvedSpec:
    from knowme.core.context.policy import default_policy

    return ResolvedSpec(
        name=spec.name,
        system_prompt=spec.system_prompt,
        tools=spec.tools,
        memory_scope=spec.memory_scope,
        context_policy=spec.context_policy or default_policy(),
        model=spec.model or settings.model,
        small_model=spec.small_model or settings.small_model,
        max_iterations=spec.max_iterations or settings.max_iterations,
        max_tokens=spec.max_tokens or settings.max_tokens,
    )

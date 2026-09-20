"""The small, readable catalog of agents shipped with the workspace.

An agent is configuration, not a second runtime implementation.  Every profile
below becomes an ``AgentSpec`` and is executed by ``AgentRuntime``.
"""

from __future__ import annotations

from dataclasses import dataclass

from knowme.core.spec import AgentSpec


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    icon: str
    description: str
    spec: AgentSpec

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "description": self.description,
            "tools": sorted(self.spec.tools) if self.spec.tools is not None else None,
            "model": self.spec.model,
        }


_READER_TOOLS = frozenset({
    "get_document", "get_selection", "add_note", "highlight",
    "search_web", "save_note", "read_tool_result", "skill",
    "create_skill", "manage_memory",
})


PROFILES: tuple[AgentProfile, ...] = (
    AgentProfile(
        id="default",
        name="General",
        icon="✦",
        description="日常助理；可以使用当前已连接的全部工具。",
        spec=AgentSpec(name="default"),
    ),
    AgentProfile(
        id="coding",
        name="Coding",
        icon="⌘",
        description="理解项目、检索资料，并把编码任务交给受控工作区。",
        spec=AgentSpec(
            name="coding",
            system_prompt=(
                "You are the Coding Agent in a local personal-agent workspace. "
                "Explain code plainly, inspect before changing, preserve existing work, "
                "and make small verifiable changes. Use tools only when they help."
            ),
            tools=frozenset({
                "get_document", "search_web", "read_tool_result", "skill",
                "create_skill", "save_note", "manage_memory", "delegate_task",
                "github_read",
            }),
        ),
    ),
    AgentProfile(
        id="learning",
        name="Learning",
        icon="◈",
        description="围绕正在阅读的材料解释、提问、做笔记并形成知识。",
        spec=AgentSpec(
            name="learning",
            system_prompt=(
                "You are the Learning Agent. Work from the open reading material and "
                "the user's selection. Explain difficult ideas step by step, ask useful "
                "questions, and turn important insights into concise notes when asked."
            ),
            tools=_READER_TOOLS,
        ),
    ),
    AgentProfile(
        id="research",
        name="Research",
        icon="◎",
        description="检索、比较来源，并把结论沉淀为可追溯的研究笔记。",
        spec=AgentSpec(
            name="research",
            system_prompt=(
                "You are the Research Agent. Separate evidence from inference, compare "
                "sources, state uncertainty, and keep a clear trail from claims to source "
                "material. Prefer primary sources when available."
            ),
            tools=frozenset(set(_READER_TOOLS) | {"github_read"}),
        ),
    ),
)

_BY_ID = {profile.id: profile for profile in PROFILES}


def list_profiles() -> tuple[AgentProfile, ...]:
    return PROFILES


def get_profile(agent_id: str) -> AgentProfile:
    """Return a known profile; never turn arbitrary input into configuration."""
    try:
        return _BY_ID[agent_id]
    except KeyError as exc:
        raise ValueError(f"unknown agent: {agent_id}") from exc

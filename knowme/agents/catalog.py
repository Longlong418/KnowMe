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
    "create_skill", "manage_memory", "get_note", "create_note",
    "update_note", "delete_note", "list_notes", "search_notes",
    "list_folders", "get_linked_notes", "parse_links",
})

# The document library (tools/documents.py): list / search / read a window /
# import a URL. These are what make the Reader's own agent able to work from
# the material rather than from whatever it can recall.
_DOCUMENT_TOOLS = frozenset({
    "list_documents", "search_documents", "open_document", "fetch_document",
})

# The Coding Agent's own hands (tools/coding.py): look at the project, change
# it, run it. Confined to the project root; write_file/edit_file/run_command
# additionally need the Coding page's allow_write switch, which is off by
# default. delegate_task stays in the list on purpose — a big refactor is still
# better handed to a local CLI, and now the agent can check what it did.
_CODING_TOOLS = frozenset({
    "list_files", "read_file", "search_files", "git_status", "git_diff",
    "write_file", "edit_file", "run_command", "delegate_task",
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
        description="读项目、改项目、跑测试；需要时把大活交给本机 CLI，并核对它到底改了什么。",
        spec=AgentSpec(
            name="coding",
            system_prompt=(
                "You are the Coding Agent in a local personal-agent workspace. "
                "Explain code plainly, inspect before changing, preserve existing work, "
                "and make small verifiable changes. Use tools only when they help. "
                "You have your own tools (read_file, search_files, git_status, and — when "
                "the user has switched them on — write_file, edit_file, run_command) and "
                "they only work inside the project root; read before you write, and say "
                "which files you changed and what you ran. If a write is refused, the "
                "user has not enabled it yet: ask them to, instead of working around it."
            ),
            tools=frozenset({
                "get_document", "search_web", "read_tool_result", "skill",
                "create_skill", "save_note", "manage_memory", "github_read",
                *_CODING_TOOLS,
            }),
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
    # The Reader's own agent, available inside the reading pane so you can ask
    # about what you are reading without leaving it. Deliberately does NOT have
    # search_web: its job is the material in front of you, and an agent that can
    # wander off to the open web is one that answers from somewhere other than
    # your document. The Research agent is next door for that.
    AgentProfile(
        id="reader",
        name="Reader",
        icon="❖",
        description="跟着你正在读的材料：查文档库、定位原文、解释清楚、顺手做笔记。",
        spec=AgentSpec(
            name="reader",
            system_prompt=(
                "You are the Reading Agent, embedded in the user's reading pane. "
                "Work from the document itself: the open document arrives as "
                "[application context] with its id. Quote and paraphrase the actual "
                "text, and say where in the document you are. When you need more "
                "than the context holds, call open_document with that id and an "
                "offset — read in windows rather than assuming what the rest says. "
                "If the library search finds nothing, say so instead of answering "
                "from memory. Never invent quotations."
            ),
            tools=frozenset(set(_DOCUMENT_TOOLS) | {
                "get_document", "get_selection", "save_note", "manage_memory",
                "read_tool_result", "skill", "create_skill",
                "create_note", "get_note", "update_note", "list_notes", "search_notes",
            }),
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

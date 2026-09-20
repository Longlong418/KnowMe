"""Ephemeral Agent Run — assembles working memory for each turn.

The inner box on the whiteboard: everything here is rebuilt per run and thrown
away. What persists lives in knowme/memory. Working memory =

    system prompt (SOUL.md)            ← who KnowMe is
  + durable facts & episodes           ← what KnowMe remembers (gated!)
  + current chat history               ← this conversation
  + the user's new message
"""

from __future__ import annotations

from datetime import datetime

from knowme.config import Settings
from knowme.runtime import snip_compact
from knowme.runtime import tool_entries as te

DEFAULT_SOUL = """\
You are KnowMe, a personal assistant running locally on your user's laptop.
You are concise, warm, and proactive. You remember what your user tells you.

Rules:
- When the user wants to schedule something, use create_event. Resolve relative
  dates and times ("next Tuesday", "in 30 minutes") to ISO timestamps yourself.
- When the user asks what's on their calendar (a day, a week, "yesterday"), use
  list_events — you CAN read the calendar, not just write to it.
- When the user shares something durable about a person, project, or preference,
  use save_note to remember it.
- When asked to message someone, use send_message (it drafts to a local outbox).
- If memory context is provided with the message, trust it — it came from your
  own store.
- Call each tool at most once per request. Your history shows [tools used]
  blocks listing what past turns called — if a tool already ran, do NOT run it
  again; answer from that record instead. A tool result shown as
  `[full: <path> — read_tool_result]` was too long to keep: call read_tool_result
  with that filename to see it, rather than guessing at what it said.
- Be honest about where things live. Every tool's output states exactly where
  its artifact landed (local calendar file, Apple Calendar, memory database at
  .knowme/state.db) — relay that truthfully, and never claim something synced
  anywhere the tool output doesn't say.
- You have SKILLS — instructions you've been taught for doing particular tasks
  a particular way. The `skill` tool lists them; call it with no arguments
  before starting anything structured or repeatable (a weekly review, a report
  in a set format, a recurring lookup), then load the one you need by name.
  Don't improvise a workflow you may already have written down.
- You can manage your own memory: use manage_memory to correct or forget facts,
  update_soul to save a standing preference the user gives you, and create_skill
  to save a repeatable workflow the user teaches you (only after they say yes).
"""


def load_soul(settings: Settings) -> str:
    """SOUL.md is the editable persona file, created on first run. Changing it
    changes who your KnowMe is — that's procedural memory at its simplest."""
    soul_path = settings.home / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(DEFAULT_SOUL, encoding="utf-8")
    return soul_path.read_text(encoding="utf-8")


class Session:
    """Holds one conversation: the chat history plus the recipe for the
    system prompt. One Session per gateway connection."""

    def __init__(self, settings: Settings, memory=None, session_id: str = "default",
                 conn=None):
        self.settings = settings
        self.memory = memory  # knowme.memory.Memory (None until Phase-2 wiring)
        self.session_id = session_id
        # Only snip_compact's watermark needs this; when a caller passes a
        # memory it already carries the same connection, so derive it rather
        # than making every existing construction site learn a new argument.
        self.conn = conn if conn is not None else getattr(memory, "conn", None)
        self.history: list[dict] = []

    def build_system(self) -> str:
        """The STABLE half of the prompt — byte-identical for every turn.

        Nothing per-turn belongs here. Prompt caching is a PREFIX match: a
        clock interpolated into this string changes every minute, and every
        byte after it is re-billed at full price on every single turn. It fails
        silently, too — the request still succeeds, the bill is just larger.

        So this holds only what a session cannot change underneath you: the
        persona, and the model's own identity. The model id can change, but only
        by switching model — which invalidates the cache anyway, because caches
        are model-scoped. Stating it here therefore costs nothing.

        Everything that moves turn to turn lives in build_turn_context().
        """
        return "\n".join([
            load_soul(self.settings),
            # the agent should know its own brain — "what model are you?"
            # is the first question every curious user asks
            (f"\nYour model: you are running on '{self.settings.model}' via the "
             f"'{self.settings.provider}' provider, inside KnowMe, a local-first "
             "open-source agent harness."),
        ])

    def build_turn_context(self, user_message: str, notify=None) -> str:
        """The VOLATILE half — everything that changes from turn to turn: the
        clock, the gated retrieval, the matched skills.

        Returned as text for the caller to prepend to the user's message rather
        than append to the system prompt. Where it sits decides what it breaks:
        a message at turn 5 invalidates nothing before turn 5, while a system
        prompt that changes every turn invalidates everything.

        The agent runs on your laptop, so it should know your laptop's clock —
        local time WITH the timezone name, enough to resolve "in 30 minutes".
        """
        now = datetime.now().astimezone()
        parts = [f"Right now it is {now:%A, %Y-%m-%d %H:%M} ({now:%Z}, UTC{now:%z})."]

        if self.memory is not None:
            # Hero moment #1: a cheap judge decides IF we retrieve at all —
            # default-on retrieval is slow and biases answers (see
            # memory/retrieval_gate.py for the why).
            retrieved = self.memory.gated_retrieve(user_message, notify=notify)
            if retrieved:
                parts.append("\nRelevant memory:\n" + retrieved)
            # Skills are NOT injected here any more. The model pulls what it
            # needs through the `skill` tool; the system prompt only tells it
            # that skills exist. Pushing skill bodies in on every turn meant
            # paying for a skill the model never used — and guessing which one
            # matters from word overlap is a judgment the model does better.

        return "[context]\n" + "\n".join(parts)

    def add_exchange(self, user_message: str, reply: str, tool_calls: list | None = None,
                     source: str = "cli", meta: dict | None = None) -> None:
        """Record the turn in history (working memory) and, if memory is wired,
        in the chat log (so consolidation can distill it later).

        Tool activity is folded into the assistant's history entry as a [tools
        used] block, one entry per line. Without it the model forgets it already
        acted and happily re-runs the same tool next turn (the triple-booked-
        meeting bug from the first live test).

        THE RECORD STORED HERE IS THE COMPLETE ONE. Nothing is compressed on the
        way to disk: this string is both the model's working memory and the
        conversation the USER reads back in the dashboard, and those two want
        opposite things. Compression belongs to prompt assembly — see
        app._run_full_turn, which rewrites a copy on the way out. Storing the
        compressed form here is how the dashboard ended up showing an excerpt and
        a file path where the user had a right to see the actual tool output."""
        record = reply
        if tool_calls:
            record = te.render(reply, [
                te.entry(c["tool"], c["args"], c["output"])
                for c in tool_calls])
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": record})
        # The conversation-level bound. Runs here because this is the only place
        # history grows, and it no-ops until the conversation is actually long
        # (runtime/snip_compact.py). Replaces the old history[-N:] slice at the
        # call site, which dropped the middle without saying so.
        self.history = snip_compact.snip(
            self.history, self.settings.home, self.conn, self.session_id,
            self.settings.snip_head, self.settings.snip_tail)
        if self.memory is not None:
            self.memory.log_chat(user_message, record, session_id=self.session_id,
                                 source=source, meta=meta)

    # ---- session lifecycle (the "New chat" / history feature)
    # A session is just a tag on chat_log rows. Starting a new one clears working
    # memory; switching reloads a past conversation's history so replies have
    # context. Consolidation still reads ALL unconsolidated rows regardless.
    def start_new(self, session_id: str) -> None:
        self.session_id = session_id
        self.history = []

    def switch(self, session_id: str) -> None:
        self.session_id = session_id
        self.history = []
        if self.memory is None:
            return
        # Reload the WHOLE thread, then reproduce the snip shape. Slicing to the
        # recent tail here (as this used to) would drop the opening the snip
        # deliberately protects — and would do it silently, which is the failure
        # mode snip_compact exists to remove.
        flat = [{"role": role, "content": text}
                for user_msg, reply in self.memory.session_history(session_id)
                for role, text in (("user", user_msg), ("assistant", reply))]
        self.history = snip_compact.rebuild(
            flat, self.settings.home, self.conn, session_id,
            self.settings.snip_head, self.settings.snip_tail)

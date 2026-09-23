"""Memory facade — the three pillars behind one small interface.

    procedural  SKILL.md files      how to act
    semantic    facts table (FTS5)  what is durably true
    episodic    episodes table      what happened, when

Plus the two agents that manage them:
    retrieval_gate   decides IF a turn needs memory   (hero moment #1)
    consolidation    distills chats into facts, every N exchanges
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import anthropic

from knowme.config import Settings
from knowme.memory import consolidation, retrieval_gate
from knowme.memory.episodic.store import SqliteEpisodeStore
from knowme.memory.procedural.loader import SkillLoader
from knowme.memory.semantic.store import SqliteFactStore


def bundled_skill_dirs() -> list[Path]:
    """Where the skills that SHIP with KnowMe live — and why there are two answers.

    Skills live under `skills/` at the repo root so a checkout can edit them. But
    the wheel only packages the `knowme/` directory, so a `pip install knowme-agent`
    would have found nothing there and silently started with zero skills —
    procedural memory, one of the four pillars, quietly missing. (It did, until
    2026-07-31.) pyproject force-includes the same folder into the wheel at
    `knowme/skills`, so an installed KnowMe finds it next to the code.

    Exactly one of these exists at a time — the package copy only in a built
    wheel, the repo copy only in a checkout — so returning both is not a
    double-load, it is "wherever you installed from, the skills came too".
    """
    here = Path(__file__).resolve()
    return [p for p in (here.parents[1] / "skills", here.parents[2] / "skills") if p.is_dir()]


class Memory:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, client: anthropic.Anthropic,
                 episode_store=None, agent_id: str = "default"):
        # episode_store: inject an already-built store (the dashboard caches ONE
        # NotionEpisodeStore process-wide — its constructor hits the network,
        # so building one per Memory would re-query Notion on every poll).
        # agent_id: "who wrote this down", not "what this agent may see". Facts,
        # episodes and notes are ONE shared pool; this id is only the stamp that
        # new rows carry so the dashboard can say which Agent recorded them.
        # Conversations DO stay separate (chat_log / sessions / the Context
        # Bridge below are still scoped) — memory is shared, threads are not.
        self.conn = conn
        self.settings = settings
        self.client = client
        self.agent_id = agent_id
        self.facts = self._make_fact_store(conn, settings, agent_id)
        self.episodes = episode_store if episode_store is not None else self._make_episode_store(conn, settings, agent_id)
        self.skills = SkillLoader([*bundled_skill_dirs(), settings.home / "skills"])

    @staticmethod
    def _make_fact_store(conn, settings, agent_id="default"):
        # Every branch here returns something that satisfies FactStore
        # (semantic/base.py) and is held to it by the conformance suite — which
        # is the whole reason a hosted service can stand in for local SQLite
        # without anything upstream noticing.
        if settings.semantic_store == "supabase":
            from knowme.memory.semantic.supabase_store import SupabaseFactStore

            return SupabaseFactStore(settings, agent_id)
        if settings.semantic_store == "mem0":
            from knowme.memory.semantic.mem0_store import Mem0FactStore

            return Mem0FactStore(settings, agent_id)
        if settings.semantic_store == "zep":
            from knowme.memory.semantic.zep_store import ZepFactStore

            return ZepFactStore(settings, agent_id)
        if settings.semantic_store == "langmem":
            from knowme.memory.semantic.langmem_store import LangMemFactStore

            return LangMemFactStore(settings, agent_id)
        return SqliteFactStore(conn, agent_id)

    @staticmethod
    def _make_episode_store(conn, settings, agent_id="default"):
        if settings.episodic_store == "notion":
            from knowme.memory.episodic.notion_store import NotionEpisodeStore

            return NotionEpisodeStore(agent_id)
        return SqliteEpisodeStore(conn, agent_id)

    # ---- retrieval (gated — see retrieval_gate.py for why)
    def gated_retrieve(self, message: str, notify=None) -> str:
        retrieve, query, reason = retrieval_gate.should_retrieve(
            self.client, self.settings.small_model, message
        )
        if notify:
            notify("gate", {"decision": "retrieve" if retrieve else "skip", "reason": reason})
        if not retrieve:
            return ""
        found = self.facts.search(query, self.settings.retrieval_top_k)
        found += self.episodes.search(query, top_k=3)
        return "\n".join(found)

    # ---- procedural
    # No matching_skills() any more. The harness used to push the "top 2"
    # keyword-overlapping skill BODIES into every turn; the model now pulls what
    # it wants through the `skill` tool instead (see tools/memory_admin.py).
    # Skills are reached via memory.skills.catalog() / .find(name).

    # ---- write paths
    def log_chat(self, user_message: str, reply: str, session_id: str = "default",
                 source: str = "cli", meta: dict | None = None,
                 user_meta: dict | None = None) -> None:
        import json as _json
        # user_meta carries the attached images' REFERENCES (name/url/bytes), so
        # a reopened conversation can draw the picture again. The pixels stay on
        # disk in <home>/uploads/ — the row points at them.
        self.conn.execute(
            "INSERT INTO chat_log (role, content, session_id, source, agent_id, meta) VALUES ('user', ?, ?, ?, ?, ?)",
            (user_message, session_id, source, self.agent_id,
             _json.dumps(user_meta) if user_meta else None),
        )
        # meta (gate/latency/iterations/tools) rides on the assistant row so a
        # reopened thread can render the full turn card, not just the text.
        self.conn.execute(
            "INSERT INTO chat_log (role, content, session_id, source, agent_id, meta) VALUES ('assistant', ?, ?, ?, ?, ?)",
            (reply, session_id, source, self.agent_id, _json.dumps(meta) if meta else None),
        )
        self.conn.commit()

    # ---- sessions (for the dashboard's chat history + "New chat")
    def session_history(self, session_id: str) -> list[tuple[str, str]]:
        """The (user, assistant) exchanges of one past session, in order — used
        to reload working memory when the user switches back to a conversation."""
        rows = self.conn.execute(
            "SELECT role, content FROM chat_log WHERE session_id = ? AND agent_id = ? ORDER BY id",
            (session_id, self.agent_id),
        ).fetchall()
        pairs, pending = [], None
        for r in rows:
            if r["role"] == "user":
                pending = r["content"]
            elif pending is not None:
                pairs.append((pending, r["content"]))
                pending = None
        return pairs

    def list_sessions(self) -> list[dict]:
        """One row per conversation: id, first user message (the title), message
        count, and when it started — newest first. Scoped to this agent's turns."""
        rows = self.conn.execute(
            """SELECT session_id,
                      COUNT(*) AS messages,
                      MIN(created_at) AS started_at,
                      MAX(created_at) AS last_at
               FROM chat_log WHERE agent_id = ? GROUP BY session_id ORDER BY last_at DESC""",
            (self.agent_id,),
        ).fetchall()
        out = []
        for r in rows:
            first = self.conn.execute(
                "SELECT content FROM chat_log WHERE session_id = ? AND role = 'user' AND agent_id = ? ORDER BY id LIMIT 1",
                (r["session_id"], self.agent_id),
            ).fetchone()
            out.append({
                "id": r["session_id"],
                "title": (first["content"][:60] if first else "(empty)"),
                "messages": r["messages"],
                "started_at": r["started_at"],
                "last_at": r["last_at"],
            })
        return out

    def export_markdown(self) -> None:
        """Mirror memory to a human-readable MEMORY.md next to state.db — so the
        whiteboard's `~/.knowme/MEMORY.md` box is literally real, and "your memory
        is a file you can open" is true. state.db stays the queryable source of
        truth; this file is a generated view, refreshed after each turn.

        Exports the whole shared pool, so any agent's turn rewrites the same
        complete file. Scoping this by agent_id was worse than it looked: the
        home is shared, MEMORY.md is one file, so whichever agent happened to
        run last overwrote the others' memories with its own — the file got
        smaller the more you used KnowMe."""
        facts = self.conn.execute(
            "SELECT subject, content FROM facts ORDER BY subject, id"
        ).fetchall()
        eps = self.conn.execute(
            "SELECT happened_at, summary FROM episodes ORDER BY happened_at DESC, id DESC"
        ).fetchall()
        lines = [
            "# KnowMe memory",
            "",
            ("_A human-readable mirror of what KnowMe remembers. The source of truth is "
            "`state.db` (the `facts` and `episodes` tables, keyword-searchable via FTS5); "
            "this file is regenerated after every turn._"),
            "",
            f"## Facts — semantic memory ({len(facts)})",
            "",
        ]
        lines += [f"- **{f['subject']}** — {f['content']}" for f in facts] or ["_none yet_"]
        lines += ["", f"## Episodes — episodic memory ({len(eps)})", ""]
        lines += [f"- **{e['happened_at']}** — {e['summary']}" for e in eps] or ["_none yet_"]
        (self.settings.home / "MEMORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def maybe_consolidate(self, notify=None) -> None:
        new_facts = consolidation.consolidate_if_due(
            self.conn,
            self.client,
            self.settings.small_model,
            self.settings.consolidate_every,
            self.facts,
            self.episodes,
            agent_id=self.agent_id,
        )
        if new_facts and notify:
            notify("consolidation", {"new_facts": new_facts})

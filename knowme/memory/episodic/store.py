"""Episodic memory — dated events: what happened, and when.

Semantic memory answers "what do I know?"; episodic answers "what happened
last Tuesday?". Same SQLite file, but every row carries a date and retrieval
blends relevance (FTS rank) with recency — the whiteboard's "RAG for
relevance + SQL for recency".
"""

from __future__ import annotations

import sqlite3

from knowme.memory.semantic.store import _fts_query, _searchable, _substring_terms


class SqliteEpisodeStore:
    def __init__(self, conn: sqlite3.Connection, agent_id: str = "default"):
        self.conn = conn
        self.agent_id = agent_id

    def add(self, summary: str, happened_at: str) -> None:
        self.conn.execute(
            "INSERT INTO episodes (happened_at, summary, agent_id) VALUES (?,?,?)",
            (happened_at, summary, self.agent_id),
        )
        self.conn.commit()

    def search(self, query: str, top_k: int = 3) -> list[str]:
        """Relevance first (FTS), most recent first among matches — and, for the
        scripts the index cannot segment, the substring pass described in
        semantic/store._substring_terms (an episode stored as 「今天定了去大连海事大学」
        is one FTS term, so only its opening characters were reachable)."""
        if not _searchable(query):
            return self.recent(top_k)
        rows: list[sqlite3.Row] = []
        fts = _fts_query(query)
        if fts:
            rows = self.conn.execute(
                "SELECT e.id, e.happened_at, e.summary FROM episodes_fts JOIN episodes e "
                "ON e.id = episodes_fts.rowid WHERE episodes_fts MATCH ? AND e.agent_id = ? "
                "ORDER BY rank, e.happened_at DESC LIMIT ?",
                (fts, self.agent_id, top_k),
            ).fetchall()
        seen = {r["id"] for r in rows}
        for term in _substring_terms(query):
            if len(rows) >= top_k:
                break
            for r in self.conn.execute(
                "SELECT id, happened_at, summary FROM episodes "
                "WHERE agent_id = ? AND summary LIKE ? "
                "ORDER BY happened_at DESC LIMIT ?",
                (self.agent_id, f"%{term}%", top_k),
            ):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    rows.append(r)
        return [f"({r['happened_at']}) {r['summary']}" for r in rows[:top_k]]

    def recent(self, top_k: int = 3) -> list[str]:
        rows = self.conn.execute(
            "SELECT happened_at, summary FROM episodes WHERE agent_id = ? "
            "ORDER BY happened_at DESC LIMIT ?",
            (self.agent_id, top_k),
        ).fetchall()
        return [f"({r['happened_at']}) {r['summary']}" for r in rows]

    def list(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, happened_at, summary, created_at FROM episodes "
            "WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
            (self.agent_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, episode_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM episodes WHERE id=? AND agent_id = ?", (episode_id, self.agent_id))
        self.conn.commit()
        return cur.rowcount > 0

"""Semantic memory — durable facts, keyword-searched with SQLite FTS5.

The Hermes insight from the whiteboard: "keyword top-k, no embedding". For a
single user's facts, ranked keyword search (BM25) is fast, fully local, and —
crucially for teaching — you can read the whole index with sqlite3.
Want vectors? Set KNOWME_SEMANTIC_STORE=supabase (see supabase_store.py).
"""

from __future__ import annotations

import re
import sqlite3

# The scripts unicode61 does not segment. It counts these as alphanumeric but
# puts no word boundary between them, so a whole run — 「阿历克斯喜欢游泳」 — is
# indexed as ONE term. An exact match on a name inside that run can therefore
# never hit, which is why these tokens, and only these, are searched as
# prefixes. Turning *every* token into `token*` would quietly make "car" match
# "carpet": a worse failure than this one, because it still looks like it works.
_UNSEGMENTED_SCRIPTS = (
    "\u3040-\u309f"  # Hiragana
    "\u30a0-\u30ff"  # Katakana
    "\u3400-\u4dbf"  # CJK Unified Ideographs Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
)
_UNSEGMENTED = re.compile(f"[{_UNSEGMENTED_SCRIPTS}]")
# \u2026and a RUN of them: the thing unicode61 fuses into one index term.
_UNSEGMENTED_RUN = re.compile(f"[{_UNSEGMENTED_SCRIPTS}]{{2,}}")


def _fts_query(text: str) -> str:
    r"""User text isn't a valid FTS5 query (quotes/punctuation break MATCH).
    Reduce it to `word OR word OR ...` over alphanumeric tokens.

    "Alphanumeric" has to mean what the *index* means by it. facts_fts and
    episodes_fts declare no tokenizer, so they get FTS5's default — unicode61,
    which keeps every Unicode alphanumeric and folds diacritics ("München" is
    stored as `munchen`). An ASCII-only `[a-zA-Z0-9]` disagreed with that
    index, and the index was not the side that was wrong:

      * accented Latin was truncated to a fragment — "Müller" became `ller`,
        which matches nothing, so German, French, Spanish, Portuguese, Turkish
        and Vietnamese users got silently wrong answers;
      * every non-Latin script reduced to "" — and an empty query is not a
        no-op. SqliteEpisodeStore.search() reads it as "just give me the recent
        ones", so a user asking about Сергей got an unrelated English episode
        handed to the model under the heading "Relevant memory". KnowMe wasn't
        skipping memory for those users, it was confidently supplying someone
        else's.

    `[^\W_]` is the same set unicode61 keeps. Underscore is excluded because
    unicode61 treats it as a separator, so `knowme_agent` is two terms in the
    index and has to be two terms here too, or it matches nothing.

    Everything stays lowercased, and that is load-bearing beyond tidiness:
    FTS5's AND/OR/NOT/NEAR are operators only in UPPERCASE, so lowercasing is
    what stops a user's "Alex and Bob" from parsing as a boolean expression.
    """
    words = re.findall(r"[^\W_]{2,}", text.lower())
    if not words:
        return ""
    return " OR ".join(
        f"{w}*" if _UNSEGMENTED.search(w) else w for w in dict.fromkeys(words)
    )


def _substring_terms(text: str) -> list[str]:
    r"""The CJK runs in a query, in the order they should be tried.

    The `*` above is a PREFIX match, and a prefix match can only reach the START
    of an index term. unicode61 fuses a whole CJK run into one term, so
    「大连海事大学」 sits inside the term 「在大连海事大学」 and `大连海事大学*` matches
    nothing — the query has to name the first characters of the sentence to find
    it. Every real question is about something in the MIDDLE. (Live report:
    问「我在哪读书」→ 记忆里明明有「在大连海事大学读硕士研究生」，agent 说不知道。
    The search it tried, `manage_memory search "上学 学校 大学"`, returned
    "no matching facts".)

    So these, and only these, are searched as SUBSTRINGS instead — the same
    trick the knowledge base already uses (`tools/knowledge.py`: title/content
    LIKE). Latin never comes here: "%car%" matching "carpet" is a worse failure
    than the one being fixed, because it still looks like it works.

    A run longer than a word is a phrase, and the fact it should reach is
    usually phrased differently, so it also yields its two-character fragments
    (「大连海事大学读硕士」 -> 大学, 硕士). Whole runs come first, because they are
    the more precise match, and the caller keeps that order.
    """
    terms: list[str] = []
    for run in _UNSEGMENTED_RUN.findall(text.lower()):
        terms.append(run)
        terms.extend(run[i : i + 2] for i in range(len(run) - 1))
    # A search query is a few words. A pasted paragraph must not become two
    # hundred LIKE scans, and the tail of one is not worth reading anyway.
    return list(dict.fromkeys(terms))[:12]


def _searchable(text: str) -> bool:
    """Is there anything in this query to search WITH at all?

    The two callers treat the empty query differently and must keep doing so —
    `search` fails closed ("no facts"), `search_with_ids` opens with the newest
    ones — and both answers are about whether a query EXISTS, not about whether
    it matched. So the check is on the terms, not on the results."""
    return bool(_fts_query(text) or _substring_terms(text))


class SqliteFactStore:
    def __init__(self, conn: sqlite3.Connection, agent_id: str = "default"):
        self.conn = conn
        self.agent_id = agent_id

    def add(self, subject: str, content: str, source: str = "user") -> None:
        self.conn.execute(
            "INSERT INTO facts (subject, content, source, agent_id) VALUES (?,?,?,?)",
            (subject.lower().strip(), content, source, self.agent_id),
        )
        self.conn.commit()

    def _search_rows(self, query: str, top_k: int) -> list[sqlite3.Row]:
        """Relevance first (FTS5/BM25), then the CJK substring pass for what the
        index cannot segment. One query, two callers: the memory a turn is
        handed (search) and the memory the manage_memory tool shows (search_with_ids)
        — they used to carry two copies of this SQL, which is how a fix lands in
        one of them and not the other."""
        rows: list[sqlite3.Row] = []
        fts = _fts_query(query)
        if fts:
            rows = self.conn.execute(
                "SELECT f.id, f.subject, f.content FROM facts_fts JOIN facts f "
                "ON f.id = facts_fts.rowid "
                "WHERE facts_fts MATCH ? AND f.agent_id = ? ORDER BY rank LIMIT ?",
                (fts, self.agent_id, top_k),
            ).fetchall()
        seen = {r["id"] for r in rows}
        # The substring pass fills only the slots FTS left over. An all-CJK query
        # arrives here with every slot free — its `fts` expression is the prefix
        # form, and that is precisely the form that cannot reach the middle of a
        # stored sentence.
        for term in _substring_terms(query):
            if len(rows) >= top_k:
                break
            for r in self.conn.execute(
                "SELECT id, subject, content FROM facts "
                "WHERE agent_id = ? AND (content LIKE ? OR subject LIKE ?) "
                "ORDER BY id DESC LIMIT ?",
                (self.agent_id, f"%{term}%", f"%{term}%", top_k),
            ):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    rows.append(r)
        return rows[:top_k]

    def search(self, query: str, top_k: int = 4) -> list[str]:
        if not _searchable(query):
            return []
        return [f"[{r['subject']}] {r['content']}" for r in self._search_rows(query, top_k)]

    # --- CRUD: humans (dashboard) and the agent (manage_memory tool) edit memory.
    # The facts_au / facts_ad triggers keep the FTS index in sync automatically.
    def list(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, subject, content, source, created_at FROM facts "
            "WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
            (self.agent_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_with_ids(self, query: str, top_k: int = 8) -> list[dict]:
        if not _searchable(query):
            return self.list(top_k)
        return [dict(r) for r in self._search_rows(query, top_k)]

    def update(self, fact_id: int, content: str, subject: str | None = None) -> bool:
        if subject is None:
            cur = self.conn.execute(
                "UPDATE facts SET content=? WHERE id=? AND agent_id=?",
                (content, fact_id, self.agent_id),
            )
        else:
            cur = self.conn.execute(
                "UPDATE facts SET content=?, subject=? WHERE id=? AND agent_id=?",
                (content, subject.lower().strip(), fact_id, self.agent_id),
            )
        self.conn.commit()
        return cur.rowcount > 0

    def delete(self, fact_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM facts WHERE id=? AND agent_id=?", (fact_id, self.agent_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def merge(self, source_id: int, target_id: int) -> bool:
        """Append one fact to another, then remove the source atomically."""
        if source_id == target_id:
            return False
        rows = self.conn.execute(
            "SELECT id, subject, content FROM facts "
            "WHERE id IN (?, ?) AND agent_id = ?",
            (source_id, target_id, self.agent_id),
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        source, target = by_id.get(source_id), by_id.get(target_id)
        if source is None or target is None:
            return False
        addition = source["content"]
        if source["subject"] != target["subject"]:
            addition = f"{source['subject']}: {addition}"
        merged = target["content"]
        if addition and addition not in merged:
            merged = f"{merged}\n{addition}" if merged else addition
        try:
            self.conn.execute(
                "UPDATE facts SET content=? WHERE id=? AND agent_id=?",
                (merged, target_id, self.agent_id),
            )
            self.conn.execute(
                "DELETE FROM facts WHERE id=? AND agent_id=?",
                (source_id, self.agent_id),
            )
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            raise
        return True

    def settle(self, timeout: float = 120.0) -> bool:
        """Already settled. The row and its FTS5 index land in one transaction,
        so a fact is searchable the instant add() returns. The hosted backends
        have to work for this."""
        return True

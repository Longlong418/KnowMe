"""Document-library tools — the reader agent's access to your documents.

These read the LIBRARY (``applications/library.py``), not the filesystem: a
document is looked up by its id, and only documents you actually added are
reachable. ``tools/reader.py``'s ``get_document(path)`` still exists for "read
this file on my disk" and is unchanged.

WHY EVERY READ IS A WINDOW
    A book is 500,000 characters and the context window is not. So
    ``open_document`` returns a bounded slice and tells the caller where it
    stopped, and continuing is one more call with ``offset``. The bound is
    ``settings.tool_result_budget``, which is exactly the right number: that
    setting decides when a tool result is rewritten on LATER turns (compressed
    to a pointer plus an excerpt), and within the turn the model reasons over
    the result in full. Returning more than the budget would mean paying for
    characters the model cannot use and the next turn will compress anyway.

WHY THERE IS NO get_current_document TOOL
    The Application Context Bridge already puts the open document into the turn
    (``[application context]`` … ``Document content:`` … ``Application state:
    doc_id=…``), capped at ``max_content_chars``. So the agent already HAS the
    document's text and its id — it can call ``open_document`` with that id to
    read past the point the bridge stopped. A second tool for the same thing
    would be one more name for the model to choose between.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from knowme.applications import library
from knowme.core.tools import Tool
from knowme.applications.reader import ReaderError


def _format_listing(rows: list[dict]) -> str:
    if not rows:
        return "文档库是空的。让用户在阅读器里添加一份文件，或粘贴一个 URL。"
    lines = [f"文档库里有 {len(rows)} 份文档："]
    for d in rows:
        # A zero-char document is flagged in the listing: it is readable by the
        # user but invisible to search, and finding that out mid-answer is worse
        # than knowing it up front.
        note = "  ← 没有文字层（扫描件），不能检索或引用" if not d["chars"] else ""
        lines.append(f"- {d['title']}  [id={d['id']}]  {d['kind']} · {d['chars']} 字"
                     f" · 加入于 {d['created_at'][:10]}{note}")
    return "\n".join(lines)


NO_TEXT = ("《{title}》 [id={id}] 没有可提取的文字（{chars} 字）——它多半是扫描件，"
           "里面是文字的图像而不是文字本身。用户可以正常阅读它，但你不能引用或检索它的内容。"
           "如果需要它的内容，请说明这一点，让用户提供带文字层的版本，或由用户选中文字发给你。")


def _format_window(row: sqlite3.Row, window: dict) -> str:
    """One window of a document, with the coordinates to ask for the next one."""
    if not window["chars"]:
        # Say WHY it is empty instead of returning an empty string, which the
        # model would have to guess about (and would probably guess wrong).
        return NO_TEXT.format(title=row["title"], id=row["id"], chars=0)
    end = window["offset"] + window["limit"]
    head = (f"《{row['title']}》 [id={row['id']}] {row['kind']} · 全文 {window['chars']} 字"
            f" · 本次返回第 {window['offset']}–{end} 字")
    if window["has_more"]:
        tail = (f"\n\n[还有 {window['chars'] - end} 字。"
                f"继续读：open_document(doc_id=\"{row['id']}\", offset={end})]")
    else:
        tail = "\n\n[全文结束]"
    return f"{head}\n\n{window['text']}{tail}"


def make_document_tools(conn: sqlite3.Connection, home: Path,
                        budget: int = 4000) -> dict[str, Tool]:
    """Build the library tools. `budget` is the per-read character window."""

    def _list() -> str:
        return _format_listing(library.list_documents(conn))

    def _search(query: str) -> str:
        hits = library.search_documents(conn, query)
        if not hits:
            return f"没有找到包含「{query}」的文档。可以先用 list_documents 看有哪些文档。"
        return "匹配的段落：\n" + "\n".join(
            f"- 《{h['title']}》 [id={h['id']}] … {h['snippet']} …" for h in hits)

    def _open(doc_id: str, offset: int = 0) -> str:
        row = library.get_document(conn, doc_id)
        if row is None:
            return (f"没有 id 为 {doc_id} 的文档。"
                    "先用 list_documents 或 search_documents 找到正确的 id。")
        return _format_window(row, library.text_window(row["content"], offset, budget))

    def _fetch(url: str) -> str:
        """Download a URL into the library and return its first window."""
        try:
            fetched = library.fetch_url(url)
            name = fetched["name"]
            if not Path(name).suffix:
                name += ".txt"
            doc = library.parse_and_save(
                conn, home, name=name, raw=fetched["raw"],
                content_type=fetched["content_type"], source=fetched["source"])
        except ReaderError as exc:
            return f"抓取失败：{exc}"
        row = library.get_document(conn, doc["id"])
        return _format_window(row, library.text_window(row["content"], 0, budget))

    return {
        "list_documents": Tool(
            name="list_documents",
            description="列出文档库里所有文档（标题、id、类型、字数）。"
                        "不知道有哪些文档时先调它——读文档需要一个 id。",
            input_schema={"type": "object", "properties": {}},
            fn=_list,
        ),
        "search_documents": Tool(
            name="search_documents",
            description="在文档库的正文里全文检索，返回命中的片段和文档 id。"
                        "想找某个说法出现在哪份文档时用它，不要挨个 open_document 翻。",
            input_schema={"type": "object", "properties": {
                "query": {"type": "string", "description": "检索词，多个词用空格分隔（全部命中才算匹配）"},
            }, "required": ["query"]},
            fn=_search,
        ),
        "open_document": Tool(
            name="open_document",
            description=f"读取某份文档的一段正文（每次最多约 {budget} 字）并给出下一段的 offset。"
                        "返回里带 [还有 N 字] 时，用同样的 doc_id 和新的 offset 继续读。"
                        "不要一次读完长文档——先读开头，需要细节再继续。",
            input_schema={"type": "object", "properties": {
                "doc_id": {"type": "string", "description": "文档 id（list_documents 或 search_documents 给出）"},
                "offset": {"type": "integer", "description": "从第几个字符开始读，默认 0", "default": 0},
            }, "required": ["doc_id"]},
            fn=_open,
        ),
        "fetch_document": Tool(
            name="fetch_document",
            description="抓取一个公开 URL，存进文档库，并返回开头一段。"
                        "用户让你读一篇网页/在线文档时用它。只支持 http(s)。",
            input_schema={"type": "object", "properties": {
                "url": {"type": "string", "description": "要抓取的 http(s) 链接"},
            }, "required": ["url"]},
            fn=_fetch,
        ),
    }

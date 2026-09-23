"""The document tools the reader agent works through.

The invariant worth locking is the WINDOW: a whole book must never reach the
model in one result, and the result must say how to get the next piece. Without
that a long document is either unreadable (truncated with no way forward) or
unaffordable (the whole thing in context every turn).
"""

import re

import pytest

from knowme.applications.library import list_documents, save_document
from knowme.applications.reader import ReaderError
from knowme.db import connect
from knowme.tools import documents
from knowme.tools.documents import make_document_tools


def _tools(tmp_path, budget=200):
    conn = connect(tmp_path)
    return conn, make_document_tools(conn, tmp_path, budget=budget)


def test_every_read_is_a_window_with_a_way_forward(tmp_path):
    conn, tools = _tools(tmp_path, budget=200)
    long_text = "第一段。" * 500                       # 2000 chars
    doc = save_document(conn, tmp_path, name="book.md", raw=b"x", text=long_text)

    first = tools["open_document"].fn(doc["id"])
    assert "第 0–200 字" in first
    assert "还有 1800 字" in first
    # the tool must hand back the coordinates for the next call, or the model
    # has to guess an offset
    nxt = int(re.search(r"offset=(\d+)", first).group(1))
    assert nxt == 200

    second = tools["open_document"].fn(doc["id"], nxt)
    assert "第 200–400 字" in second
    assert second != first

    # the last window says so instead of offering more
    last = tools["open_document"].fn(doc["id"], 1800)
    assert "全文结束" in last
    assert "offset=" not in last


def test_no_window_is_longer_than_the_budget(tmp_path):
    conn, tools = _tools(tmp_path, budget=300)
    doc = save_document(conn, tmp_path, name="big.md", raw=b"x", text="字" * 10_000)
    for offset in (0, 300, 9000):
        body = tools["open_document"].fn(doc["id"], offset)
        # the body minus the header/footer is the window itself
        window = body.split("\n\n", 1)[1].rsplit("\n\n[", 1)[0]
        assert len(window) <= 300, f"window at {offset} was {len(window)} chars"


def test_a_bad_id_points_at_the_tools_that_would_find_one(tmp_path):
    conn, tools = _tools(tmp_path)
    msg = tools["open_document"].fn("not-an-id")
    assert "not-an-id" in msg
    assert "list_documents" in msg          # tells the model how to recover
    assert "search_documents" in msg


def test_search_and_list_are_usable_without_opening_anything(tmp_path):
    conn, tools = _tools(tmp_path)
    save_document(conn, tmp_path, name="论文.md", raw=b"x", text="检索门控会跳过记忆。")
    save_document(conn, tmp_path, name="other.md", raw=b"y", text="完全无关。")

    listing = tools["list_documents"].fn()
    assert "论文" in listing and "other" in listing
    assert "检索门控会跳过记忆" not in listing      # a listing is not the text

    # two-character Chinese words are the case FTS5 silently lost
    hits = tools["search_documents"].fn("门控")
    assert "论文" in hits and "other" not in hits
    assert "门控" in hits                          # the snippet shows the match

    assert "没有找到" in tools["search_documents"].fn("不存在的东西")


def test_an_empty_library_says_so_rather_than_failing(tmp_path):
    conn, tools = _tools(tmp_path)
    assert "空的" in tools["list_documents"].fn()
    assert "没有找到" in tools["search_documents"].fn("anything")


def test_fetch_document_downloads_parses_and_stores(tmp_path, monkeypatch):
    """``fetch_document`` shipped broken and no test noticed, because the fetch
    was the one path the three tests above never walked.

    It called ``library.fetch_url`` — which does not exist. The downloader lives
    in ``applications/reader.py`` (it owns the scheme check and the 4 MB cap);
    the library only ever receives bytes that already arrived. So every call
    raised ``AttributeError``, the surrounding ``except ReaderError`` did not
    catch it, and a model asking this tool to read a web page got a crash
    instead of a document.

    Monkeypatched rather than live: this pins the WIRING (download → parse →
    store → hand back a window). A test that needs the network would fail for
    reasons that have nothing to do with this file.
    """
    conn, tools = _tools(tmp_path, budget=200)
    monkeypatch.setattr(documents, "fetch_url", lambda url: {
        "name": "page.html", "raw": b"<p>hello from the web</p>",
        "content_type": "text/html", "source": url})

    out = tools["fetch_document"].fn("https://example.com/a")
    assert "hello from the web" in out
    # It really landed in the library, not just in the reply — otherwise the
    # next turn's search_documents would not see it.
    assert [d["title"] for d in list_documents(conn)] == ["page"]
    # And it is a WINDOW like every other read, not the raw body.
    assert "第 0–" in out


def test_a_failed_fetch_is_a_message_not_an_exception(tmp_path, monkeypatch):
    def boom(url):
        raise ReaderError("远程文件太大（最多 4 MB）")

    conn, tools = _tools(tmp_path)
    monkeypatch.setattr(documents, "fetch_url", boom)
    out = tools["fetch_document"].fn("https://example.com/huge")
    # The Reader's own words, so the model can tell the user something true.
    assert "4 MB" in out
    assert list_documents(conn) == []

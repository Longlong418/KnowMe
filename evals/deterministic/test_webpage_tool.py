"""``read_webpage`` — the tool that turns a search hit into readable text.

Two things are worth locking here, and neither is "it can fetch a page":

1. IT IS A WINDOW, AND IT SAYS SO. The usual caller is a graph node running
   ``run_loop``, which resends the whole message list every iteration — the
   context-budget machinery lives in AgentRuntime, not in the loop. So an
   unbounded page is not a slow answer, it is minutes and real money, and a
   page truncated without a marker is worse still: the model cannot know to ask
   for the rest. Hence the cap AND the "[还有 N 字]" footer with an offset.

2. FETCH FAILURES COME BACK AS TEXT. Same posture as ``search_web``: a tool the
   model called because it did not know the answer must not answer with an
   exception, or the node it runs inside dies with nothing to show for it.

The fetch itself is monkeypatched — the download and the HTML→text extraction
belong to ``applications/reader.py`` and are tested there. What is tested here is
what this module adds on top: the window, the marker, the error text.
"""

import re

from knowme.applications.reader import ReaderError
from knowme.tools import webpage

PAGE = {"name": "article.html", "source": "https://example.com/a",
        "content": "正文第一段。" * 1200}          # 7200 chars


def _tool(monkeypatch, page=PAGE):
    monkeypatch.setattr(webpage, "load_url", lambda url: dict(page, source=url))
    return webpage.make_tool()


def test_a_short_page_comes_back_whole_and_says_so(monkeypatch):
    tool = _tool(monkeypatch, {"name": "p", "source": "x", "content": "只有一句话。"})
    out = tool.fn("https://example.com/short")
    assert "只有一句话。" in out
    assert "全文结束" in out
    assert "offset=" not in out          # nothing more to ask for


def test_a_long_page_is_cut_to_the_window_with_a_way_forward(monkeypatch):
    tool = _tool(monkeypatch)
    out = tool.fn("https://example.com/long")

    body = out.split("\n\n", 1)[1].rsplit("\n\n[", 1)[0]
    assert len(body) <= webpage.WINDOW, f"window was {len(body)} chars"
    assert "article.html" in out and "7200 字" in out

    # The model must not have to guess an offset. Same contract as
    # open_document (test_document_tools.py).
    nxt = int(re.search(r"offset=(\d+)", out).group(1))
    assert nxt == webpage.WINDOW

    second = tool.fn("https://example.com/long", nxt)
    assert second != out
    assert f"第 {nxt} 字开始" in second


def test_the_last_window_offers_no_more(monkeypatch):
    tool = _tool(monkeypatch)
    out = tool.fn("https://example.com/long", 7199)
    assert "全文结束" in out
    assert "offset=" not in out


def test_a_page_with_no_text_explains_instead_of_returning_nothing(monkeypatch):
    """An empty string is unreadable as an answer: the model has to guess whether
    the page was empty, blocked, or a bug. Say which."""
    tool = _tool(monkeypatch, {"name": "app", "source": "x", "content": "   \n  "})
    out = tool.fn("https://example.com/spa")
    assert "没有可读的文字" in out
    assert "app" in out


def test_a_fetch_failure_is_a_message_not_an_exception(monkeypatch):
    def boom(url):
        raise ReaderError("只支持 http(s) URL")

    monkeypatch.setattr(webpage, "load_url", boom)
    tool = webpage.make_tool()
    assert "只支持 http(s) URL" in tool.fn("ftp://example.com/x")

"""``read_webpage`` — one page's readable TEXT, not just a snippet.

``search_web`` returns a title, a ~400-character snippet and a URL. That is
enough to decide what is worth looking at and nowhere near enough to write
anything from, which is fine for "what's on this weekend" and useless for
research. This is the second half of that pair: give it a URL, get the words.

WHY THE WINDOW IS SMALL AND LOUD
    The fetch and the HTML→text extraction are the Reader's
    (``applications/reader.py``) — same scheme check, same 4 MB cap, same
    extractor the reading pane uses, so a page cannot read differently here than
    it does there. What this adds is a bound, because the caller is usually a
    graph node running ``run_loop``, and ``run_loop`` resends the whole message
    list on every iteration: the context-budget machinery (tool_budget,
    micro_compact) lives in AgentRuntime, not in the loop. A 4 MB page resent ten
    times is minutes and real money, and no unit test would ever notice. So the
    window is a few thousand characters, ``offset`` gets you the next one, and
    the reply always says where it stopped — a model handed half a page without
    being told does not know to ask for the rest.
"""

from __future__ import annotations

from knowme.applications.reader import ReaderError, load_url
from knowme.core.tools import Tool

# Roughly the same size as the library's per-read window (tools/documents.py),
# for the same reason: it is what the model can use within a turn, and anything
# longer is paid for on every following iteration of the loop.
WINDOW = 4000


def make_tool() -> Tool:
    def read_webpage(url: str, offset: int = 0) -> str:
        try:
            page = load_url(url)
        except ReaderError as exc:
            # The Reader's errors are already written for a reader ("只支持 http(s)
            # URL", "远程文件太大（最多 4 MB）"), so pass them through rather than
            # inventing a second vocabulary for the same failure.
            return f"读取失败：{exc}"
        text = page["content"].strip()
        if not text:
            return (f"{page['name']}（{url}）取回了页面，但里面没有可读的文字。"
                    "常见原因是这页靠脚本渲染，或者正文是图片。换一个信源，"
                    "或者用别的关键词再搜一次。")
        start = max(0, offset)
        chunk = text[start:start + WINDOW]
        head = f"《{page['name']}》 {url} · 全文 {len(text)} 字"
        if start:
            head += f" · 本次从第 {start} 字开始"
        end = start + len(chunk)
        if end < len(text):
            tail = (f"\n\n[还有 {len(text) - end} 字没读。"
                    f"确实需要就继续：read_webpage(url=\"{url}\", offset={end})]")
        else:
            tail = "\n\n[全文结束]"
        return f"{head}\n\n{chunk}{tail}"

    return Tool(
        name="read_webpage",
        description=(
            "Fetch one public web page and return its readable text (not the raw HTML, "
            "not a snippet). Use it after search_web when the snippet is not enough to "
            "answer from — it returns the first few thousand characters and an offset to "
            "continue. Prefer several focused pages over reading one page end to end."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) 链接（search_web 给出的那个）"},
                "offset": {"type": "integer",
                           "description": "从第几个字符开始读，默认 0；返回里带 [还有 N 字] 时用它继续",
                           "default": 0},
            },
            "required": ["url"],
        },
        fn=read_webpage,
    )

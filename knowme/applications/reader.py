"""Small, dependency-free document loader used by the dashboard Reader.

The browser should never guess whether a response is text or binary.  This
module keeps that decision in one place and returns a plain text document that
can safely be sent through the Context Bridge.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_BYTES = 4 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".csv", ".html", ".htm", ".xml"}


class ReaderError(ValueError):
    """An input cannot be loaded as a readable document."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._hidden += 1
        elif not self._hidden and tag.lower() in {"p", "div", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._hidden = max(0, self._hidden - 1)
        elif not self._hidden and tag.lower() in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def _html_to_text(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts))
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def _decode(raw: bytes, content_type: str = "") -> str:
    charset = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.IGNORECASE)
    candidates = [charset.group(1)] if charset else []
    candidates += ["utf-8", "utf-8-sig", "gb18030", "latin-1"]
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_bytes(name: str, raw: bytes, content_type: str = "") -> str:
    if len(raw) > MAX_BYTES:
        raise ReaderError(f"文件太大（最多 {MAX_BYTES // 1024 // 1024} MB）")
    suffix = PurePosixPath(name.split("?", 1)[0].lower()).suffix
    if raw.startswith(b"%PDF") or suffix == ".pdf" or "application/pdf" in content_type.lower():
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ReaderError("当前环境未安装 PDF 解析器，请先安装 pypdf") from exc
        try:
            pages = PdfReader(io.BytesIO(raw)).pages
            return "\n\n".join(page.extract_text() or "" for page in pages).strip()
        except Exception as exc:  # pragma: no cover - parser-specific failures
            raise ReaderError(f"PDF 无法解析：{exc}") from exc
    text = _decode(raw, content_type)
    if suffix in {".html", ".htm"} or "text/html" in content_type.lower():
        return _html_to_text(text)
    return text


def load_upload(name: str, data_url: str) -> dict[str, str]:
    """Decode a browser data URL and return the safe Reader payload."""
    if not name or not data_url.startswith("data:") or "," not in data_url:
        raise ReaderError("上传数据格式无效")
    header, encoded = data_url.split(",", 1)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ReaderError("上传数据不是有效的 base64 文件") from exc
    return {"name": name, "content": parse_bytes(name, raw, header)}


def load_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReaderError("只支持 http(s) URL")
    request = Request(url, headers={"User-Agent": "KnowMe Reader/1.0"})
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - scheme checked above
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(MAX_BYTES + 1)
    except Exception as exc:  # surface a readable error to the UI
        raise ReaderError(f"URL 加载失败：{exc}") from exc
    if len(raw) > MAX_BYTES:
        raise ReaderError("远程文件太大（最多 4 MB）")
    name = PurePosixPath(parsed.path).name or parsed.netloc
    return {"name": name, "content": parse_bytes(name, raw, content_type), "source": url}

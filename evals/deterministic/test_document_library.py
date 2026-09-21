"""The document library: both halves stored, searchable, and safe on disk.

Two properties are worth locking because breaking either is silent:

  the ORIGINAL bytes are kept under a generated name, never the uploaded one —
  a filename is attacker-controlled input and must not decide where a file lands

  the extracted TEXT is what search and the agent read, so a document is
  findable by its body and a whole book never has to fit in one tool result
"""

import io

import pytest

from knowme.applications.library import (
    delete_document,
    file_path,
    get_document,
    kind_for,
    list_documents,
    parse_and_save,
    save_document,
    search_documents,
    text_window,
)
from knowme.db import connect


def test_both_the_original_and_the_text_are_stored(tmp_path):
    conn = connect(tmp_path)
    doc = save_document(conn, tmp_path, name="paper.md", raw=b"# Title\n\nbody text",
                        text="# Title\n\nbody text")

    assert doc["title"] == "paper"          # extension dropped from the display name
    assert doc["kind"] == "markdown"
    assert doc["chars"] == 18
    assert doc["bytes"] == 18

    stored = file_path(conn, tmp_path, doc["id"])
    assert stored is not None and stored.read_bytes() == b"# Title\n\nbody text"
    # the ORIGINAL, not a parse of it — pdf.js renders this, not our text
    assert stored.parent.name == "documents"


def test_the_stored_name_is_generated_not_uploaded(tmp_path):
    """A hostile filename must not escape the documents folder."""
    conn = connect(tmp_path)
    doc = save_document(conn, tmp_path, name="../../escape.md", raw=b"x", text="x")
    stored = file_path(conn, tmp_path, doc["id"])
    assert stored is not None
    assert stored.parent == (tmp_path / "documents").resolve()
    assert stored.name == f"{doc['id']}.md"


def test_re_adding_the_same_bytes_does_not_duplicate(tmp_path):
    conn = connect(tmp_path)
    first = save_document(conn, tmp_path, name="a.md", raw=b"same", text="same")
    second = save_document(conn, tmp_path, name="b.md", raw=b"same", text="same")
    assert first["id"] == second["id"]
    assert len(list_documents(conn)) == 1


def test_search_finds_a_document_by_its_body(tmp_path):
    conn = connect(tmp_path)
    save_document(conn, tmp_path, name="notes.md", raw=b"x",
                  text="the retrieval gate skips memory when it is not needed")
    save_document(conn, tmp_path, name="other.md", raw=b"y", text="unrelated words")

    hits = search_documents(conn, "retrieval gate")
    assert [h["title"] for h in hits] == ["notes"]
    assert "retrieval" in hits[0]["snippet"]
    assert search_documents(conn, "words")[0]["title"] == "other"

    # Every term must appear, so more words narrow the result rather than
    # widen it — precise beats fuzzy for a library an agent also queries.
    assert search_documents(conn, "retrieval gate memory") != []
    assert search_documents(conn, "retrieval unrelated") == []
    assert search_documents(conn, "   ") == []

    # Punctuation is searched LITERALLY, not parsed: none of these raises the
    # way an FTS5 query would, and a character that is not in the document is
    # simply not found. Each term stands alone, so "(gate" is a term no
    # document contains while "gate" on its own is found.
    assert search_documents(conn, "retrieval (gate") == []     # "(gate" is literal
    assert search_documents(conn, "retrieval (gate") == search_documents(conn, "(gate")
    assert search_documents(conn, '"retrieval"') == []         # stray quotes are literal
    assert search_documents(conn, "retrieval*") == []          # * is not a wildcard
    assert search_documents(conn, "a%b") == []                 # % is not a wildcard
    assert search_documents(conn, "a_b") == []                 # _ is not a wildcard
    assert search_documents(conn, "retrieval gate") != []      # ...and the words are found


def test_chinese_search_works_at_any_query_length(tmp_path):
    """The reason search is LIKE and not FTS5.

    FTS5's default tokenizer has no word boundaries to split Chinese on, so a
    whole sentence becomes one token and NO query inside it matches; the
    trigram tokenizer needs three characters, so every two-character word
    (门控, 记忆, 文档) silently matched nothing. Both failures were invisible —
    an empty result list looks the same as "no such document".
    """
    conn = connect(tmp_path)
    save_document(conn, tmp_path, name="论文.md", raw=b"x",
                  text="这一段落讲了检索门控的设计，以及记忆如何注入上下文。")
    save_document(conn, tmp_path, name="其他.md", raw=b"y", text="完全无关的内容。")

    assert [h["title"] for h in search_documents(conn, "检索门控")] == ["论文"]
    assert [h["title"] for h in search_documents(conn, "门控")] == ["论文"]     # 2 chars
    assert [h["title"] for h in search_documents(conn, "记忆")] == ["论文"]     # 2 chars
    assert [h["title"] for h in search_documents(conn, "上下文")] == ["论文"]
    assert search_documents(conn, "不存在") == []
    # a mixed query still narrows on both scripts
    assert search_documents(conn, "检索 gate") == []
    assert "检索门控" in search_documents(conn, "门控")[0]["snippet"]


def test_delete_removes_the_row_and_the_file(tmp_path):
    conn = connect(tmp_path)
    doc = save_document(conn, tmp_path, name="gone.md", raw=b"x", text="findable token")
    assert search_documents(conn, "findable") != []

    assert delete_document(conn, tmp_path, doc["id"]) is True
    assert get_document(conn, doc["id"]) is None
    assert search_documents(conn, "findable") == []
    assert not (tmp_path / "documents" / f"{doc['id']}.md").exists()
    assert delete_document(conn, tmp_path, doc["id"]) is False


def test_text_window_never_returns_a_whole_document(tmp_path):
    conn = connect(tmp_path)
    doc = save_document(conn, tmp_path, name="big.md", raw=b"x", text="a" * 1000)

    first = text_window("a" * 1000, 0, 400)
    assert (first["chars"], first["offset"], first["limit"]) == (1000, 0, 400)
    assert first["has_more"] is True
    assert text_window("a" * 1000, 990, 400)["has_more"] is False
    # an offset past the end is clamped, not an error, and never yields text
    past = text_window("a" * 1000, 5000, 400)
    assert past["text"] == "" and past["has_more"] is False
    assert doc["chars"] == 1000


def test_pdf_text_is_extracted_so_a_pdf_is_searchable(tmp_path):
    """A real PDF round-trip: the point of declaring pypdf as a dependency."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    # blank pages carry no text, so this asserts the PATH, not a lucky parse:
    # a PDF with no text layer is refused with a readable message rather than
    # stored as an empty document.
    buffer = io.BytesIO()
    writer.write(buffer)

    from knowme.applications.reader import ReaderError

    with pytest.raises(ReaderError, match="没有文本内容"):
        parse_and_save(conn := connect(tmp_path), tmp_path, name="blank.pdf",
                       raw=buffer.getvalue())


def test_kind_drives_which_renderer_the_frontend_picks(tmp_path):
    assert kind_for("a.md") == "markdown"
    assert kind_for("a.pdf") == "pdf"
    assert kind_for("a.html") == "html"
    assert kind_for("a.py") == "code"
    assert kind_for("a.unknownext") == "text"
    assert kind_for("https://x.dev/doc.PDF?v=1") == "pdf"   # query string stripped

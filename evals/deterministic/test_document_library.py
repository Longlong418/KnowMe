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


def test_a_real_pdf_round_trips_through_the_stored_bytes(tmp_path):
    """The point of declaring pypdf as a dependency: pdf.js renders the file we
    kept, and pypdf reads the text out of the same bytes."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_blank_page(width=300, height=300)   # a second page, on purpose
    buffer = io.BytesIO()
    writer.write(buffer)

    conn = connect(tmp_path)
    doc = parse_and_save(conn, tmp_path, name="blank.pdf", raw=buffer.getvalue())
    assert doc["kind"] == "pdf"
    assert doc["bytes"] == len(buffer.getvalue())

    stored = file_path(conn, tmp_path, doc["id"])
    assert stored is not None and stored.read_bytes() == buffer.getvalue()
    assert pypdf.PdfReader(io.BytesIO(stored.read_bytes())).get_num_pages() == 2


def test_a_scan_with_no_text_layer_is_kept_but_flagged(tmp_path):
    """A scanned PDF has no characters in it, only an image of them. It is still
    worth storing — the user can read it — but search and quoting cannot see
    inside it, and pretending otherwise would fail silently later."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=300, height=300)      # an image-only page
    buffer = io.BytesIO()
    writer.write(buffer)

    conn = connect(tmp_path)
    doc = parse_and_save(conn, tmp_path, name="scan.pdf", raw=buffer.getvalue())
    assert doc["chars"] == 0                          # stored, not refused
    assert file_path(conn, tmp_path, doc["id"]) is not None  # the page is readable
    assert search_documents(conn, "扫描") == []        # and honestly unfindable


def test_kind_drives_which_renderer_the_frontend_picks(tmp_path):
    assert kind_for("a.md") == "markdown"
    assert kind_for("a.pdf") == "pdf"
    assert kind_for("a.html") == "html"
    assert kind_for("a.py") == "code"
    assert kind_for("a.unknownext") == "text"
    assert kind_for("https://x.dev/doc.PDF?v=1") == "pdf"   # query string stripped


def test_the_bytes_decide_when_the_name_lies(tmp_path):
    """An arXiv URL ends in /pdf/1706.03762 — a name, not an extension.

    Reading ".03762" as a suffix made a 15-page paper a text/plain document,
    and the reader picks its renderer from the kind, so it painted the whole
    extraction as one paragraph. A PDF says what it is in its first four bytes.
    """
    from knowme.applications.library import kind_and_suffix

    assert kind_and_suffix("https://arxiv.org/pdf/1706.03762", b"%PDF-1.7\n...") == ("pdf", ".pdf")
    assert kind_and_suffix("paper.pdf", b"%PDF-1.4\n...") == ("pdf", ".pdf")
    # A name we do understand still wins for everything that is not a PDF.
    assert kind_and_suffix("notes.md", b"# hi") == ("markdown", ".md")
    assert kind_and_suffix("data.csv", b"a,b") == ("csv", ".csv")
    # No usable extension at all: text, and a suffix that is at least a suffix.
    assert kind_and_suffix("mystery", b"hello") == ("text", ".txt")


def test_saving_a_url_named_pdf_stores_it_as_a_pdf(tmp_path):
    conn = connect(tmp_path)
    doc = save_document(conn, tmp_path, name="https://arxiv.org/pdf/1706.03762",
                        raw=b"%PDF-1.7\n" + b"x" * 100, text="extracted body")
    assert doc["kind"] == "pdf"
    assert doc["suffix"] == ".pdf"
    assert file_path(conn, tmp_path, doc["id"]).read_bytes().startswith(b"%PDF")


def test_a_wrongly_kinded_row_is_repaired_in_place(tmp_path):
    """Old rows are corrected without touching the file or the text.

    `suffix` and `path` are separate columns, so fixing the row is enough: the
    bytes on disk were never wrong. Written the way the old code wrote it, so
    this fails if the repair pass ever stops running.
    """
    conn = connect(tmp_path)
    save_document(conn, tmp_path, name="keep.md", raw=b"# keep", text="keep me")
    conn.execute(
        "INSERT INTO documents (id, title, kind, suffix, path, source, content,"
        " chars, bytes, sha256, added_by, created_at)"
        " VALUES ('legacy','1706','text','.03762','documents/legacy.03762','',"
        " 'arxiv body', 10, 8, 'deadbeef', 'default', '2026-01-01T00:00:00+00:00')")
    folder = tmp_path / "documents"
    folder.mkdir(exist_ok=True)
    (folder / "legacy.03762").write_bytes(b"%PDF-1.7\n" + b"y" * 40)
    conn.commit()

    docs = list_documents(conn, home=tmp_path)
    fixed = next(d for d in docs if d["id"] == "legacy")
    assert (fixed["kind"], fixed["suffix"]) == ("pdf", ".pdf")
    # The file did not move and the extracted text is untouched.
    assert (folder / "legacy.03762").is_file()
    assert "arxiv body" in get_document(conn, "legacy")["content"]
    assert search_documents(conn, "arxiv body")[0]["id"] == "legacy"


def test_the_repair_only_touches_real_pdfs(tmp_path):
    """A row with an odd suffix but text bytes must keep saying text.

    Otherwise the second time this ran it would relabel every extensionless
    document in the library as a PDF and the reader would refuse to open them.
    """
    conn = connect(tmp_path)
    save_document(conn, tmp_path, name="keep.md", raw=b"# keep", text="keep me")
    conn.execute(
        "INSERT INTO documents (id, title, kind, suffix, path, source, content,"
        " chars, bytes, sha256, added_by, created_at)"
        " VALUES ('plain','README','text','.README','documents/plain.README','',"
        " 'just words', 10, 10, 'cafe', 'default', '2026-01-01T00:00:00+00:00')")
    folder = tmp_path / "documents"
    folder.mkdir(exist_ok=True)
    (folder / "plain.README").write_bytes(b"just words, no magic here")
    conn.commit()

    docs = list_documents(conn, home=tmp_path)
    assert next(d for d in docs if d["id"] == "plain")["kind"] == "text"
    # Second pass over an already-correct row is a no-op, not a flip.
    assert next(d for d in list_documents(conn, home=tmp_path)
                if d["id"] == "plain")["kind"] == "text"


def test_a_missing_file_does_not_break_the_listing(tmp_path):
    """A row whose file was deleted by hand still lists; it just cannot be read."""
    conn = connect(tmp_path)
    save_document(conn, tmp_path, name="keep.md", raw=b"# keep", text="keep me")
    conn.execute(
        "INSERT INTO documents (id, title, kind, suffix, path, source, content,"
        " chars, bytes, sha256, added_by, created_at)"
        " VALUES ('ghost','gone','text','.weird','documents/ghost.weird','',"
        " '', 0, 0, '', 'default', '2026-01-01T00:00:00+00:00')")
    conn.commit()
    assert any(d["id"] == "ghost" for d in list_documents(conn, home=tmp_path))

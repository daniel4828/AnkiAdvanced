"""Tests for issue #836: the book reader.

An uploaded German/English EPUB/PDF is cut into fixed-size pages once, then
each page is translated and annotated on demand by the same pipeline the
knowledge base uses (knowledge/rendition.render_html) and cached.

What is worth pinning down here:

  1. Pagination cuts only at paragraph boundaries and labels a page with the
     book marker it *starts* in.
  2. EPUB extraction walks the spine with the stdlib only, and a file with no
     prose fails loudly rather than becoming an empty book.
  3. A rendered page is cached — paging back must not re-translate.
  4. A translation failure is reported and writes nothing (a page of German
     served as Chinese would only be noticed halfway down it).
  5. Reading progress is per (book, language).

Each test gets an isolated temp DB by patching database.core.DB_PATH — never
database.DB_PATH, which is only a wildcard-import copy (#615).
"""
import os
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import books
import database
import knowledge.rendition
import main
from books.epub import BookExtractionError
from books.paginate import paginate

client = TestClient(main.app)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()


@pytest.fixture
def fake_render(monkeypatch):
    """Stand in for translate-then-annotate, counting how often it ran.

    Patched on routes.books, which is where the page endpoint looks it up —
    the module-level import there means patching knowledge.rendition alone
    would silently miss (#615's lesson about patching the name the caller
    actually reads).
    """
    import routes.books as book_routes

    calls = []

    def _render(html, lang, source="de"):
        calls.append((html, lang, source))
        return f"[{lang}]{html}", [{"word": "生态", "definition_de": "Ökologie"}]

    monkeypatch.setattr(book_routes, "render_html", _render)
    return calls


# --- pagination -------------------------------------------------------------

def test_paginate_never_splits_a_paragraph():
    blocks = [{"text": "A" * 400, "ref_label": "c1"},
              {"text": "B" * 400, "ref_label": "c1"},
              {"text": "C" * 400, "ref_label": "c2"}]
    pages = paginate(blocks, char_budget=500)
    assert len(pages) == 3
    for page, letter in zip(pages, "ABC"):
        assert page["source_text"] == f"<p>{letter * 400}</p>"


def test_paginate_labels_a_page_with_the_marker_it_starts_in():
    blocks = [{"text": "A" * 300, "ref_label": "PDF p. 7"},
              {"text": "B" * 300, "ref_label": "PDF p. 8"}]
    pages = paginate(blocks, char_budget=400)
    assert [p["ref_label"] for p in pages] == ["PDF p. 7", "PDF p. 8"]


def test_paginate_escapes_html_in_the_source_text():
    """Source text goes into <p> markup, so a stray < in the book must not
    turn into a tag the translator would then move around."""
    pages = paginate([{"text": "5 < 6 & <b>bold</b>", "ref_label": None}], 500)
    assert "&lt;" in pages[0]["source_text"]
    assert "<b>" not in pages[0]["source_text"]


def test_paginate_splits_an_over_long_paragraph_at_sentence_ends():
    text = "Das ist ein Satz. " * 100          # ~1800 chars, one paragraph
    pages = paginate([{"text": text, "ref_label": None}], char_budget=300)
    assert len(pages) > 1
    assert all(p["source_text"].endswith("</p>") for p in pages)


# --- EPUB extraction --------------------------------------------------------

def _write_epub(path, chapters, title="Testbuch", author="Autorin"):
    """A minimal but structurally real EPUB: container.xml → OPF → spine."""
    manifest = "".join(
        f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(len(chapters)))
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>
  </metadata>
  <manifest>{manifest}</manifest>
  <spine>{spine}</spine>
</package>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
        zf.writestr("content.opf", opf)
        for i, body in enumerate(chapters):
            zf.writestr(f"c{i}.xhtml",
                        f"<html><head><title>x</title><style>p{{}}</style></head>"
                        f"<body>{body}</body></html>")


def test_epub_extract_reads_metadata_and_spine_order(tmp_path):
    path = tmp_path / "b.epub"
    _write_epub(path, ["<h1>Erstes Kapitel</h1><p>Ein Satz.</p>",
                       "<h1>Zweites Kapitel</h1><p>Noch einer.</p>"])
    out = books.epub.extract(str(path))
    assert out["title"] == "Testbuch"
    assert out["author"] == "Autorin"
    texts = [b["text"] for b in out["blocks"]]
    assert texts == ["Erstes Kapitel", "Ein Satz.", "Zweites Kapitel", "Noch einer."]
    # The heading becomes the label for everything under it.
    assert out["blocks"][1]["ref_label"] == "Erstes Kapitel"
    assert out["blocks"][3]["ref_label"] == "Zweites Kapitel"


def test_epub_extract_drops_style_and_script_content(tmp_path):
    path = tmp_path / "b.epub"
    _write_epub(path, ["<p>Prosa.</p><script>var x = 1;</script>"])
    texts = [b["text"] for b in books.epub.extract(str(path))["blocks"]]
    assert texts == ["Prosa."]


def test_epub_with_no_prose_fails_instead_of_becoming_an_empty_book(tmp_path):
    path = tmp_path / "b.epub"
    _write_epub(path, ["<p></p>"])
    with pytest.raises(BookExtractionError):
        books.epub.extract(str(path))


def test_non_epub_file_is_rejected(tmp_path):
    path = tmp_path / "notabook.epub"
    path.write_bytes(b"this is not a zip")
    with pytest.raises(BookExtractionError):
        books.epub.extract(str(path))


def test_unsupported_extension_is_rejected():
    with pytest.raises(BookExtractionError):
        books.format_from_filename("roman.mobi")


# --- ingest -----------------------------------------------------------------

def test_ingest_stores_pages_and_detects_german(tmp_db, tmp_path):
    path = tmp_path / "b.epub"
    _write_epub(path, ["<h1>Kapitel</h1>" + "".join(
        f"<p>Das ist der Absatz nummer {i} und er ist nicht sehr lang.</p>"
        for i in range(40))])
    result = books.ingest_file(str(path), "b.epub", char_budget=300)

    assert result["source_lang"] == "de"
    assert result["page_count"] > 1
    book = database.get_book(result["book_id"])
    assert book["page_count"] == result["page_count"]
    assert book["title"] == "Testbuch"
    # Page numbering is 1-based and contiguous.
    assert database.get_page(result["book_id"], 1)["source_text"].startswith("<p>")
    assert database.get_page(result["book_id"], result["page_count"]) is not None
    assert database.get_page(result["book_id"], result["page_count"] + 1) is None


def test_ingest_honours_an_explicit_source_lang(tmp_db, tmp_path):
    """Detection is only a default: reading a German book with source_lang=en
    would translate every page from the wrong language."""
    path = tmp_path / "b.epub"
    _write_epub(path, ["<p>Und das ist nicht auf Englisch geschrieben.</p>"])
    result = books.ingest_file(str(path), "b.epub", source_lang="en")
    assert database.get_book(result["book_id"])["source_lang"] == "en"


# --- reading API ------------------------------------------------------------

def _make_book(tmp_path, paragraphs=("Ein Satz.", "Noch ein Satz.")):
    path = tmp_path / "b.epub"
    _write_epub(path, ["".join(f"<p>{p}</p>" for p in paragraphs)])
    return books.ingest_file(str(path), "b.epub", char_budget=20)


def test_page_is_rendered_then_cached(tmp_db, tmp_path, fake_render):
    book = _make_book(tmp_path)
    first = client.get(f"/api/books/{book['book_id']}/page/1?lang=zh")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["cached"] is False
    assert body["text"].startswith("[zh]")
    assert body["new_words"][0]["word"] == "生态"
    assert body["page_count"] == book["page_count"]

    second = client.get(f"/api/books/{book['book_id']}/page/1?lang=zh")
    assert second.json()["cached"] is True
    assert second.json()["text"] == body["text"]
    assert len(fake_render) == 1, "a cached page must not be re-translated"


def test_each_language_gets_its_own_rendition(tmp_db, tmp_path, fake_render):
    book = _make_book(tmp_path)
    assert client.get(f"/api/books/{book['book_id']}/page/1?lang=zh").json()["text"].startswith("[zh]")
    assert client.get(f"/api/books/{book['book_id']}/page/1?lang=fr").json()["text"].startswith("[fr]")
    assert len(fake_render) == 2


def test_source_language_is_passed_to_the_renderer(tmp_db, tmp_path, fake_render):
    path = tmp_path / "b.epub"
    _write_epub(path, ["<p>This is an English sentence.</p>"])
    book = books.ingest_file(str(path), "b.epub", source_lang="en")
    client.get(f"/api/books/{book['book_id']}/page/1?lang=zh")
    assert fake_render[0][2] == "en"


def test_translation_failure_is_reported_and_writes_nothing(tmp_db, tmp_path, monkeypatch):
    import routes.books as book_routes

    book = _make_book(tmp_path)

    def _boom(html, lang, source="de"):
        raise knowledge.rendition.RenditionError("translator unavailable")

    monkeypatch.setattr(book_routes, "render_html", _boom)
    resp = client.get(f"/api/books/{book['book_id']}/page/1?lang=zh")
    assert resp.status_code == 502
    assert "translator unavailable" in resp.json()["detail"]
    assert database.get_book_rendition(book["book_id"], 1, "zh") is None


def test_out_of_range_page_is_a_404(tmp_db, tmp_path, fake_render):
    book = _make_book(tmp_path)
    resp = client.get(f"/api/books/{book['book_id']}/page/999?lang=zh")
    assert resp.status_code == 404
    assert "999" in resp.json()["detail"]


def test_unknown_language_is_a_400(tmp_db, tmp_path, fake_render):
    book = _make_book(tmp_path)
    assert client.get(f"/api/books/{book['book_id']}/page/1?lang=ru").status_code == 400


def test_missing_book_is_a_404(tmp_db, fake_render):
    assert client.get("/api/books/4242/page/1?lang=zh").status_code == 404


# --- progress ---------------------------------------------------------------

def test_progress_is_remembered_per_language(tmp_db, tmp_path, fake_render):
    book = _make_book(tmp_path)
    bid = book["book_id"]
    assert client.post(f"/api/books/{bid}/progress",
                       json={"lang": "zh", "page_no": 2}).status_code == 200
    assert client.post(f"/api/books/{bid}/progress",
                       json={"lang": "fr", "page_no": 1}).status_code == 200
    assert database.get_book_progress(bid, "zh") == 2
    assert database.get_book_progress(bid, "fr") == 1

    listed = client.get("/api/books").json()["books"]
    assert listed[0]["progress"] == {"zh": 2, "fr": 1}


def test_progress_out_of_range_is_rejected(tmp_db, tmp_path, fake_render):
    book = _make_book(tmp_path)
    resp = client.post(f"/api/books/{book['book_id']}/progress",
                       json={"lang": "zh", "page_no": 999})
    assert resp.status_code == 400


# --- deletion ---------------------------------------------------------------

def test_delete_removes_pages_renditions_and_the_file(tmp_db, tmp_path, fake_render, monkeypatch):
    book = _make_book(tmp_path)
    bid = book["book_id"]
    client.get(f"/api/books/{bid}/page/1?lang=zh")
    client.post(f"/api/books/{bid}/progress", json={"lang": "zh", "page_no": 1})
    assert os.path.isfile(database.get_book(bid)["file_path"])
    path = database.get_book(bid)["file_path"]

    assert client.delete(f"/api/books/{bid}").status_code == 200
    assert database.get_book(bid) is None
    assert database.get_page(bid, 1) is None
    assert database.get_book_rendition(bid, 1, "zh") is None
    assert database.get_book_progress(bid, "zh") is None
    assert not os.path.exists(path)


def test_deleting_a_missing_book_is_a_404(tmp_db):
    assert client.delete("/api/books/4242").status_code == 404


# --- render_html (the shared pipeline) --------------------------------------

def test_render_html_refuses_an_unknown_language():
    with pytest.raises(knowledge.rendition.RenditionError):
        knowledge.rendition.render_html("<p>Hallo</p>", "ru")


def test_render_html_raises_when_the_translator_fails(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("no network")

    monkeypatch.setattr(knowledge.rendition, "_translate_html_strict", _boom)
    with pytest.raises(knowledge.rendition.RenditionError):
        knowledge.rendition.render_html("<p>Hallo</p>", "zh")


# --- PDF --------------------------------------------------------------------

def test_scanned_pdf_without_a_text_layer_fails_loudly(tmp_path):
    """No OCR here: a scan yields no prose, and storing it as a book would
    give Daniel a reader full of blank pages with no explanation."""
    from pypdf import PdfWriter

    from books import pdf as book_pdf

    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with open(path, "wb") as fh:
        writer.write(fh)

    with pytest.raises(BookExtractionError) as excinfo:
        book_pdf.extract(str(path))
    assert "text layer" in str(excinfo.value)


def test_pdf_paragraphs_rejoin_hard_wrapped_lines():
    from books.pdf import _paragraphs

    text = "Dies ist eine sehr lan-\nge Zeile im Satz.\n\nZweiter Absatz."
    assert _paragraphs(text) == ["Dies ist eine sehr lange Zeile im Satz.",
                                 "Zweiter Absatz."]

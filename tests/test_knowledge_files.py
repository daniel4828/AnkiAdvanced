"""Tests for knowledge/files.py and POST /api/knowledge/add-file (issue #835).

PDF/DOCX fixtures are generated in-process by the same libraries the
extractor uses — no binary sample files checked into the repo, and no
network. A real (throwaway) sqlite db is used for the endpoint tests for
the same reason tests/test_knowledge_ingest_text.py uses one: the row
creation is the behaviour under test.
"""
import io

import pytest

import ai
import database
import knowledge.files as files


LONG_TEXT = "Das ist der Fließtext eines hochgeladenen Dokuments zum Testen. " * 8
assert len(LONG_TEXT) >= 200


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    """Both AI calls in this path are best-effort extras (#651 title
    translation, #833 metadata) — stubbed so nothing reaches a provider."""
    monkeypatch.setattr(ai, "translate_title", lambda title: None)
    monkeypatch.setattr(ai, "extract_article_metadata", lambda text: {})


# ---------------------------------------------------------------------------
# extract_file_text()
# ---------------------------------------------------------------------------

def test_txt_is_decoded_as_utf8():
    text, title = files.extract_file_text("Mein Artikel.txt", "Grüße aus Berlin".encode("utf-8"))
    assert text == "Grüße aus Berlin"
    assert title == "Mein Artikel"


def test_markdown_keeps_its_markup():
    """No HTML rendering: the summary prompts read Markdown fine, and
    stripping it here would only have to be undone later."""
    body = "# Überschrift\n\nEin **fetter** Absatz."
    text, _ = files.extract_file_text("note.md", body.encode("utf-8"))
    assert text == body


def test_filename_underscores_become_spaces_in_the_title_guess():
    _, title = files.extract_file_text("mein_langer_titel.txt", b"hallo")
    assert title == "mein langer titel"


def test_latin1_file_still_decodes():
    text, _ = files.extract_file_text("alt.txt", "Grüße".encode("cp1252"))
    assert "Gr" in text


def test_unknown_extension_is_an_error_not_a_guess():
    """A .zip read "as text anyway" produces replacement-character soup that
    looks like content and would be summarized and stored."""
    with pytest.raises(files.FileExtractionError) as e:
        files.extract_file_text("archiv.zip", b"PK\x03\x04binary junk")
    assert "unsupported" in str(e.value).lower()


def test_no_extension_is_an_error():
    with pytest.raises(files.FileExtractionError):
        files.extract_file_text("README", b"some text")


def test_empty_file_is_an_error():
    with pytest.raises(files.FileExtractionError):
        files.extract_file_text("leer.txt", b"")


def test_whitespace_only_file_is_an_error():
    with pytest.raises(files.FileExtractionError) as e:
        files.extract_file_text("leer.txt", b"   \n\n  \t ")
    assert "no text" in str(e.value).lower()


def test_oversized_file_is_rejected():
    data = b"x" * (files.MAX_FILE_BYTES + 1)
    with pytest.raises(files.FileExtractionError) as e:
        files.extract_file_text("riesig.txt", data)
    assert "too large" in str(e.value).lower()


# ── PDF ────────────────────────────────────────────────────────────────────

def _make_pdf(line: str) -> bytes:
    """Build a one-page PDF with a real text layer, by writing the content
    stream directly — pypdf can read text but not typeset it, and the point
    here is a page extract_text() actually returns something for."""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 24 Tf 50 700 Td ({line}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_text_layer_is_extracted():
    text, title = files.extract_file_text("Aufsatz.pdf", _make_pdf("Hallo Welt aus dem PDF"))
    assert "Hallo Welt aus dem PDF" in text
    assert title == "Aufsatz"


def _make_empty_pdf() -> bytes:
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_without_a_text_layer_is_an_error():
    """The scanned-PDF case. Storing a handful of empty strings as an
    "article" is the paywall-stub failure knowledge/article.py refuses."""
    with pytest.raises(files.FileExtractionError) as e:
        files.extract_file_text("scan.pdf", _make_empty_pdf())
    message = str(e.value).lower()
    assert "no text" in message or "scan" in message


def test_corrupt_pdf_is_an_error():
    with pytest.raises(files.FileExtractionError):
        files.extract_file_text("kaputt.pdf", b"%PDF-1.4 not really a pdf")


# ── DOCX ───────────────────────────────────────────────────────────────────

def _make_docx(paragraphs: list) -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_docx_paragraphs_are_extracted():
    data = _make_docx(["Erster Absatz.", "", "Zweiter Absatz."])
    text, title = files.extract_file_text("Bericht.docx", data)
    assert "Erster Absatz." in text
    assert "Zweiter Absatz." in text
    assert title == "Bericht"


def test_empty_docx_is_an_error():
    with pytest.raises(files.FileExtractionError):
        files.extract_file_text("leer.docx", _make_docx([]))


def test_corrupt_docx_is_an_error():
    with pytest.raises(files.FileExtractionError):
        files.extract_file_text("kaputt.docx", b"not a zip at all")


# ---------------------------------------------------------------------------
# POST /api/knowledge/add-file
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def _upload(client, name, data, **fields):
    return client.post("/api/knowledge/add-file",
                       files={"file": (name, data, "application/octet-stream")},
                       data=fields)


def test_upload_creates_an_article_row(client):
    resp = _upload(client, "artikel.txt", LONG_TEXT.encode("utf-8"))
    assert resp.status_code == 200, resp.text
    episode = database.get_episode(resp.json()["episode_id"])
    assert episode["kind"] == "article"
    assert episode["transcript_zh"] == LONG_TEXT.strip()
    # No title given, AI found none -> the filename.
    assert episode["title"] == "artikel"


def test_upload_uses_the_given_title_and_author(client):
    resp = _upload(client, "artikel.txt", LONG_TEXT.encode("utf-8"),
                   title="Mein Titel", author="Jan B.", source_url="https://x.example/a")
    episode = database.get_episode(resp.json()["episode_id"])
    assert episode["title"] == "Mein Titel"
    assert episode["channel_id"] == "Jan B."
    assert episode["youtube_url"] == "https://x.example/a"


def test_uploading_the_same_file_twice_dedupes(client):
    first = _upload(client, "artikel.txt", LONG_TEXT.encode("utf-8"))
    second = _upload(client, "kopie.txt", LONG_TEXT.encode("utf-8"))
    assert second.json() == {"status": "already_exists",
                             "episode_id": first.json()["episode_id"]}


def test_unsupported_type_returns_400(client):
    resp = _upload(client, "archiv.zip", b"PK\x03\x04junk")
    assert resp.status_code == 400
    assert "unsupported" in resp.json()["detail"].lower()


def test_too_short_file_returns_400(client):
    resp = _upload(client, "kurz.txt", b"zu kurz")
    assert resp.status_code == 400


def test_docx_upload_end_to_end(client):
    data = _make_docx([LONG_TEXT])
    resp = _upload(client, "Bericht.docx", data)
    assert resp.status_code == 200, resp.text
    episode = database.get_episode(resp.json()["episode_id"])
    assert LONG_TEXT.strip()[:40] in episode["transcript_zh"]


# ---------------------------------------------------------------------------
# One ingestion path (#643 / #681)
# ---------------------------------------------------------------------------

def test_upload_goes_through_ingest_text_not_a_second_pipeline():
    import inspect
    import routes.knowledge
    src = inspect.getsource(routes.knowledge.add_knowledge_file)
    assert "ingest_text(" in src
    assert "create_pending_episode" not in src


def test_pages_do_not_call_the_upload_endpoint_directly():
    """Both the app and /save must go through shared.js's
    ingestKnowledgeFile(), like they do for the paste box."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("app.js", "save.html"):
        body = (root / "static" / name).read_text(encoding="utf-8")
        assert "/api/knowledge/add-file" not in body, name
        assert "ingestKnowledgeFile" in body, name

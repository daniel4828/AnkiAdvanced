"""Book reader API (#836).

Upload an EPUB/PDF once (parsed and paginated in a background thread — a
400-page book takes tens of seconds), then read it one page at a time in
whichever language Daniel is studying. A page is translated and annotated on
first view by knowledge/rendition.py's render_html() — the same pipeline the
knowledge base uses — and cached, so re-reading and paging back are instant.

Reading position is stored per (book, language): the same book read in
Chinese and in French are two independent progressions through it.
"""
import logging
import os
import re
import threading
import time
import uuid

import database
from fastapi import APIRouter, Form, HTTPException, UploadFile

import books
from knowledge.rendition import RenditionError, render_html
from languages import DEFAULT_LANG, is_valid_lang

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_DIR = os.path.join("data", "books")
# Books are big; anything past this is more likely a mistake than a book.
_MAX_UPLOAD_BYTES = 80 * 1024 * 1024
_SOURCE_LANGS = ("de", "en")

# Upload/pagination jobs. Deliberately separate from routes/imports.py's
# _import_jobs: that dict is what the header task indicator reads to label
# vocabulary imports, and a book landing in it would be announced as one.
_upload_jobs: dict[str, dict] = {}
_upload_jobs_lock = threading.Lock()
_MAX_UPLOAD_JOBS = 10


def _prune_upload_jobs() -> None:
    with _upload_jobs_lock:
        for job_id in list(_upload_jobs):
            if len(_upload_jobs) <= _MAX_UPLOAD_JOBS:
                return
            if _upload_jobs[job_id]["status"] != "running":
                del _upload_jobs[job_id]


def _safe_filename(name: str) -> str:
    """A filesystem-safe basename, uniquified. Uploads keep a recognisable
    name (they are the only copy of the file), but nothing from the client
    is allowed into the path itself."""
    base = os.path.basename(name or "book")
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE).strip("._") or "book"
    return f"{uuid.uuid4().hex[:8]}_{base[:80]}"


def _resolve_lang(lang: str | None) -> str:
    lang = lang or DEFAULT_LANG
    if not is_valid_lang(lang):
        raise HTTPException(status_code=400, detail=f"Unknown language: {lang}")
    return lang


@router.get("/api/books")
def list_books():
    """Every uploaded book with its per-language reading progress."""
    return {"books": database.list_books()}


@router.post("/api/books")
async def upload_book(
    file: UploadFile,
    title: str | None = Form(None),
    source_lang: str | None = Form(None),
    char_budget: int = Form(books.DEFAULT_CHAR_BUDGET),
):
    """Store an uploaded EPUB/PDF and parse it in the background.

    Returns {job_id}; poll /api/books/upload-progress/{job_id}. Nothing is
    written to the database until the file has actually yielded text — a
    scan-only PDF or a DRM'd EPUB fails the job instead of creating a book
    that opens to a blank page.
    """
    if source_lang and source_lang not in _SOURCE_LANGS:
        raise HTTPException(
            status_code=400,
            detail=f"source_lang must be one of {', '.join(_SOURCE_LANGS)}")
    char_budget = max(300, min(int(char_budget or books.DEFAULT_CHAR_BUDGET), 5000))

    try:
        fmt = books.format_from_filename(file.filename)
    except books.BookExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File is too large ({len(content) // (1024 * 1024)} MB, limit "
                   f"{_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.join(UPLOAD_DIR, _safe_filename(file.filename))
    with open(path, "wb") as fh:
        fh.write(content)

    job_id = uuid.uuid4().hex[:8]
    label = title or os.path.basename(file.filename or "book")
    with _upload_jobs_lock:
        _upload_jobs[job_id] = {"status": "running", "message": f"Reading {label}…",
                                "title": label, "started_at": time.time()}
    _prune_upload_jobs()

    def _run():
        try:
            result = books.ingest_file(path, file.filename, title=title,
                                       source_lang=source_lang,
                                       char_budget=char_budget)
            with _upload_jobs_lock:
                started = _upload_jobs.get(job_id, {}).get("started_at", time.time())
                _upload_jobs[job_id] = {
                    "status": "done",
                    "message": f"{result['title']} — {result['page_count']} pages",
                    "title": result["title"], "summary": result, "started_at": started,
                }
        except Exception as e:
            # The file is useless without a book row, so don't leave it behind.
            try:
                os.remove(path)
            except OSError:
                pass
            logger.warning("books: upload %s (%s) failed — %s", label, fmt, e)
            with _upload_jobs_lock:
                started = _upload_jobs.get(job_id, {}).get("started_at", time.time())
                _upload_jobs[job_id] = {"status": "error", "message": "Upload failed",
                                        "title": label, "error": str(e),
                                        "started_at": started}

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


@router.get("/api/books/upload-progress/{job_id}")
def upload_progress(job_id: str):
    job = _upload_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


@router.delete("/api/books/{book_id}")
def delete_book(book_id: int):
    """Delete a book, its pages, its cached renditions and the uploaded file."""
    book = database.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    database.delete_book(book_id)
    path = book.get("file_path")
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as e:  # the row is gone either way; say so in the log
            logger.warning("books: could not delete %s — %s", path, e)
    return {"deleted": True}


@router.get("/api/books/{book_id}/page/{page_no}")
def get_book_page(book_id: int, page_no: int, lang: str | None = None):
    """One page, translated into `lang` and annotated.

    Cached after the first render (`cached` says which it was). A translation
    failure is a 502 with the reason and writes nothing — a page of untouched
    German presented as Chinese would only be discovered halfway down it.
    """
    lang = _resolve_lang(lang)
    book = database.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    page = database.get_page(book_id, page_no)
    if not page:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_no} does not exist (book has {book['page_count']} pages)")

    payload = {
        "book_id": book_id,
        "title": book["title"],
        "author": book.get("author"),
        "page_no": page_no,
        "page_count": book["page_count"],
        "ref_label": page.get("ref_label"),
        "lang": lang,
        "source_lang": book["source_lang"],
        "source_text": page["source_text"],
    }

    cached = database.get_book_rendition(book_id, page_no, lang)
    if cached:
        return {**payload, "text": cached["text"],
                "new_words": cached["new_words"], "cached": True}

    try:
        text, new_words = render_html(page["source_text"], lang,
                                      source=book["source_lang"])
    except RenditionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    database.save_book_rendition(book_id, page_no, lang, text, new_words)
    return {**payload, "text": text, "new_words": new_words, "cached": False}


@router.post("/api/books/{book_id}/progress")
def save_progress(book_id: int, body: dict):
    """Remember where Daniel is: body {lang, page_no}."""
    book = database.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    lang = _resolve_lang(body.get("lang"))
    try:
        page_no = int(body.get("page_no"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="page_no must be an integer")
    if not 1 <= page_no <= book["page_count"]:
        raise HTTPException(
            status_code=400,
            detail=f"page_no out of range (1–{book['page_count']})")
    database.set_book_progress(book_id, lang, page_no)
    return {"book_id": book_id, "lang": lang, "last_page": page_no}

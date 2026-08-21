"""PDF text extraction (#836) via pypdf.

Only the text layer is read — there is no OCR here. A scanned book therefore
yields nothing, and that is reported as a BookExtractionError rather than
stored as an empty book (same contract as knowledge/article.py's 200-character
floor: a source that produced no prose must never be presented as if it had).

The PDF's own page numbers are kept as each paragraph's `ref_label`, so the
reader can show "page 7 of 340 · PDF p. 214" even though reading pages are
cut by character budget.
"""
import logging
import re

from .epub import BookExtractionError

logger = logging.getLogger(__name__)

# A text layer that yields less than this in total is treated as absent:
# scanned PDFs often still carry a few stray characters (page numbers,
# a publisher's watermark) from partial OCR.
_MIN_TOTAL_CHARS = 200


def _paragraphs(text: str) -> list[str]:
    """PDF text comes back with hard line breaks at the typeset line width.
    Blank lines are real paragraph breaks; single breaks are not, so they are
    joined back into flowing text (hyphenated line-end words rejoined)."""
    out = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = re.sub(r"(\w)-\n(\w)", r"\1\2", chunk)
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if chunk:
            out.append(chunk)
    return out


def extract(path: str) -> dict:
    """{"title", "author", "blocks"} for a PDF file."""
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - dependency is in requirements.txt
        raise BookExtractionError("pypdf is not installed — cannot read PDF files") from e

    try:
        reader = PdfReader(path)
    except Exception as e:
        raise BookExtractionError(f"cannot open PDF: {e}") from e
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as e:
            raise BookExtractionError(f"PDF is password-protected: {e}") from e

    blocks: list[dict] = []
    total = 0
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as e:  # one broken page must not sink the book
            logger.warning("books.pdf: page %s unreadable — %s", page_no, e)
            continue
        for paragraph in _paragraphs(text):
            total += len(paragraph)
            blocks.append({"text": paragraph, "ref_label": f"PDF p. {page_no}"})

    if total < _MIN_TOTAL_CHARS:
        raise BookExtractionError(
            "this PDF has no text layer (only %d characters found) — it is probably "
            "a scan, which this reader cannot process" % total)

    meta = reader.metadata or {}
    return {
        "title": (meta.get("/Title") or "").strip() or None,
        "author": (meta.get("/Author") or "").strip() or None,
        "blocks": blocks,
    }

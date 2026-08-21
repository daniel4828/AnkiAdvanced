"""File -> plain text for the knowledge base (issue #835).

The knowledge base already eats URLs (knowledge/ingest.py's ingest_url) and
pasted bodies (ingest_text). This module adds the third shape Daniel's
material comes in: a file he already has — a saved .md note, an exported
.txt, a PDF paper, a .docx document.

This module does ONE thing: turn file bytes into an article body. It does
NOT create rows. The extracted text goes straight into `ingest_text()`, the
same function the paste box calls, so there is still exactly one path from
"some text" to a podcast_episodes row — see knowledge/ingest.py's docstring
for why this repo is strict about that (#643).

Two rules everything here follows:

  - An unrecognized extension is an error, never "read it as text anyway".
    Decoding a .zip or .jpg with errors="replace" produces a screenful of
    replacement characters that looks like content and would be summarized,
    stored, and turned into flashcards.

  - No text extracted is an error too. A scanned PDF (pages that are just
    images, no text layer) yields a handful of empty strings; storing that
    as an "article" is the same failure knowledge/article.py refuses for
    paywall stubs. The message says what happened so Daniel knows to run OCR
    rather than wondering why the summary is empty.
"""
import io
import logging
import os

logger = logging.getLogger(__name__)


class FileExtractionError(Exception):
    """A file could not be turned into an article body (unsupported type,
    unreadable bytes, no text layer, ...)."""


# Everything Daniel asked for (#835). Extensions, not MIME types: browsers
# and phone share sheets are wildly inconsistent about the Content-Type they
# attach (application/octet-stream is very common), but the filename is
# always there.
_PLAIN_TEXT_EXTS = {".txt", ".md", ".markdown", ".text"}
SUPPORTED_EXTENSIONS = sorted(_PLAIN_TEXT_EXTS | {".pdf", ".docx"})

# 10 MB. A ceiling has to exist — without one, a single request can pull an
# arbitrary amount of the server's memory. Well above any plausible article
# or book chapter, well below "someone uploaded a video".
MAX_FILE_BYTES = 10 * 1024 * 1024


def extract_file_text(filename: str, data: bytes) -> tuple[str, str]:
    """Return (text, title_guess) for one uploaded file.

    `title_guess` is the filename without its extension — a last-resort
    title only. ingest_text() prefers what Daniel typed, then what the AI
    reads out of the body (#833), and only then falls back.

    Raises FileExtractionError for anything that isn't a supported file
    type, is empty, or contains no extractable text.
    """
    filename = (filename or "").strip()
    if not data:
        raise FileExtractionError("the file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise FileExtractionError(
            f"file is too large ({len(data) // (1024 * 1024)} MB, limit is "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB)"
        )

    stem, ext = os.path.splitext(os.path.basename(filename))
    ext = ext.lower()
    title_guess = stem.strip().replace("_", " ") or "(untitled file)"

    if ext in _PLAIN_TEXT_EXTS:
        text = _decode_text(data)
    elif ext == ".pdf":
        text = _extract_pdf(data)
    elif ext == ".docx":
        text = _extract_docx(data)
    else:
        raise FileExtractionError(
            f"unsupported file type '{ext or filename}' — supported: "
            + ", ".join(SUPPORTED_EXTENSIONS)
        )

    text = text.strip()
    if not text:
        raise FileExtractionError("no text could be extracted from this file")
    return text, title_guess


def _decode_text(data: bytes) -> str:
    """Decode a .txt/.md file. Markdown is kept as-is — the summary prompts
    handle markup fine, and rendering it to HTML here would only mean
    stripping it again later."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # latin-1 decodes any byte sequence, so reaching here means something is
    # very wrong — better to say so than to hand back mojibake.
    raise FileExtractionError("could not decode this file as text")


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise FileExtractionError(f"PDF support is not installed on the server ({e})")

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as e:
        logger.warning("knowledge.files: PDF parse failed: %s", e)
        raise FileExtractionError(f"could not read this PDF: {e}")

    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text.strip():
        # The single most likely cause by far, and the one Daniel can act on.
        raise FileExtractionError(
            "this PDF has no text layer — it is probably a scan, which would "
            "need OCR before it can be added"
        )
    return text


def _extract_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as e:
        raise FileExtractionError(f"DOCX support is not installed on the server ({e})")

    try:
        document = docx.Document(io.BytesIO(data))
        paragraphs = [p.text.strip() for p in document.paragraphs]
    except Exception as e:
        logger.warning("knowledge.files: DOCX parse failed: %s", e)
        raise FileExtractionError(f"could not read this .docx file: {e}")

    return "\n\n".join(p for p in paragraphs if p)

"""EPUB text extraction (#836), standard library only.

An EPUB is a zip: META-INF/container.xml points at an OPF package file, whose
<manifest> maps ids to documents and whose <spine> lists those ids in reading
order. Walking that is a few dozen lines of zipfile + ElementTree, which is
why this module adds no dependency — `ebooklib`/`beautifulsoup4` would be two
new packages for work the stdlib already does.

trafilatura (already a dependency, used by knowledge/article.py) is not used
here either: it is tuned to find *one* article inside a noisy web page and
routinely drops chapter headings and short paragraphs from clean book XHTML.
A book chapter has no boilerplate to strip, so a plain block-level text walk
is both simpler and more faithful.
"""
import logging
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

_CONTAINER = "META-INF/container.xml"
# Elements whose content is markup/styling, never prose.
_SKIP_CONTENT = {"script", "style", "head", "title"}
# Elements that end the current paragraph.
_BLOCK = {"p", "div", "br", "li", "tr", "td", "blockquote", "section", "article",
          "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "pre"}
_HEADINGS = {"h1", "h2", "h3"}


class BookExtractionError(Exception):
    """The file could not be turned into readable text. Raised rather than
    storing an empty book: a book that opens to a blank page is a worse
    outcome than a failed upload, and the cause (DRM, no text layer, damaged
    archive) is exactly what Daniel needs told to him."""


class _TextBlocks(HTMLParser):
    """Collect block-level text runs, remembering the most recent heading so
    pages can be labelled with the chapter they start in."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.heading: str | None = None
        self._buf: list[str] = []
        self._skip = 0
        self._in_heading = False

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if not text:
            return
        if self._in_heading:
            self.heading = text
        self.blocks.append({"text": text, "ref_label": self.heading})

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_CONTENT:
            self._skip += 1
        elif tag in _BLOCK:
            self._flush()
            self._in_heading = tag in _HEADINGS

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self._flush()
            self._in_heading = False

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _opf_path(zf: zipfile.ZipFile) -> str:
    try:
        root = ElementTree.fromstring(zf.read(_CONTAINER))
    except (KeyError, ElementTree.ParseError) as e:
        raise BookExtractionError(f"not a valid EPUB (no {_CONTAINER}): {e}") from e
    for el in root.iter():
        if _strip_ns(el.tag) == "rootfile" and el.get("full-path"):
            return el.get("full-path")
    raise BookExtractionError("EPUB container.xml names no rootfile")


def _metadata(root) -> tuple[str | None, str | None]:
    title = author = None
    for el in root.iter():
        name, text = _strip_ns(el.tag), (el.text or "").strip()
        if not text:
            continue
        if name == "title" and title is None:
            title = text
        elif name == "creator" and author is None:
            author = text
    return title, author


def extract(path: str) -> dict:
    """{"title", "author", "blocks"} for an EPUB file.

    Raises BookExtractionError when the archive is unreadable, DRM-protected
    (its documents decompress to markup with no prose) or simply empty.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise BookExtractionError(f"cannot open EPUB: {e}") from e

    with zf:
        opf = _opf_path(zf)
        base = posixpath.dirname(opf)
        try:
            root = ElementTree.fromstring(zf.read(opf))
        except (KeyError, ElementTree.ParseError) as e:
            raise BookExtractionError(f"cannot read EPUB package file: {e}") from e

        manifest = {}
        spine: list[str] = []
        for el in root.iter():
            name = _strip_ns(el.tag)
            if name == "item" and el.get("id") and el.get("href"):
                manifest[el.get("id")] = el.get("href")
            elif name == "itemref" and el.get("idref"):
                spine.append(el.get("idref"))
        if not spine:
            # Some hand-made EPUBs have no usable spine; fall back to every
            # XHTML document in the manifest, in manifest order.
            spine = [i for i, href in manifest.items()
                     if href.lower().endswith((".xhtml", ".html", ".htm"))]

        title, author = _metadata(root)
        blocks: list[dict] = []
        for idref in spine:
            href = manifest.get(idref)
            if not href:
                continue
            name = posixpath.normpath(posixpath.join(base, href)) if base else href
            try:
                raw = zf.read(name)
            except KeyError:
                logger.warning("books.epub: spine item %s missing from archive", name)
                continue
            parser = _TextBlocks()
            try:
                parser.feed(raw.decode("utf-8", errors="replace"))
                parser.close()
            except Exception as e:  # a malformed chapter must not sink the book
                logger.warning("books.epub: cannot parse %s — %s", name, e)
                continue
            blocks.extend(parser.blocks)

    if not blocks:
        raise BookExtractionError(
            "no readable text found in this EPUB — it may be DRM-protected or image-only")
    return {"title": title, "author": author, "blocks": blocks}

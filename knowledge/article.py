"""Article ingestion (issue #652): turn a news/article URL into the podcast
pipeline's `transcript_zh`, so the existing summarize/story machinery can
consume it unchanged (kind='article', transcript_source='article'). See
docs/knowledge-base.md for the shared podcast_episodes column mapping.

Body extraction uses **trafilatura** (new dependency, pure Python, no
compilation) rather than a hand-rolled regex/BeautifulSoup scraper. Manually
verified against a live tagesschau.de article (clean full-text extraction,
correct title/date/sitename via `extract_metadata`) and a BBC 中文 article
(clean main body, minor byline/read-time cruft at the top that a generic
extractor is expected to leave in — not a paywall/JS-wall failure).

Unlike `knowledge.youtube` (cheap oEmbed title lookup, captions fetched
separately/lazily during processing), there is no cheap "metadata only"
endpoint for an arbitrary article URL — getting the title reliably requires
downloading and parsing the whole page, which is the same cost as getting
the body. So `routes.knowledge.add_knowledge` calls `fetch_article()` once,
synchronously, at add time, and stores the resulting text as `transcript_zh`
immediately (landing straight in `_process_episode`'s "reuse existing
transcript" fast path, see podcast.py) rather than deferring the fetch to
process time. `fetch_transcript()` below still exists for parity with
`knowledge.youtube.fetch_captions` and as the retry-time fallback for the
(unlikely) case of a row whose transcript wasn't stored at add time.
"""
from __future__ import annotations

import logging
import urllib.parse

logger = logging.getLogger(__name__)

# Tracking query params stripped by normalize_url() so the same article
# shared through different channels (WeChat, Twitter/X, a newsletter, a
# messaging app) normalizes to the same video_id — podcast_episodes' dedup
# key (UNIQUE constraint) — instead of creating a duplicate row per link
# variant.
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid",
    "igshid", "mc_cid", "mc_eid", "spm", "share_token",
    "ref", "ref_src", "ref_url", "from",
}

# Below this many characters, the extracted "body" is almost always a
# paywall/login-wall stub or a nav-bar fragment from a JS-only page, not a
# real article — see fetch_article()'s docstring.
_MIN_ARTICLE_CHARS = 200
# Length guard, not a cost optimization (DeepSeek input is fractions of a
# cent either way, see docs/knowledge-base.md) — matches the 15000-char
# ceiling already used elsewhere in the knowledge base pipeline for
# transcript-sized inputs.
_MAX_ARTICLE_CHARS = 15000


class ArticleExtractionError(Exception):
    """Raised when the article body can't be reliably extracted: a
    paywall/login wall, a page trafilatura can't download at all, or a
    JS-rendered page that yields only nav-bar/cookie-banner fragments.
    Callers MUST let this propagate rather than falling back to storing the
    (garbage) partial text — a stub masquerading as the article would flow
    straight into a plausible-looking but entirely wrong summary and, from
    there, into real flashcards (see docs/knowledge-base.md and issue #652).
    """


def normalize_url(url: str) -> str:
    """Strip tracking query params (utm_*, fbclid, gclid, ...) and the
    fragment, so the same article shared through different channels
    normalizes to one canonical URL — used as `podcast_episodes.video_id`,
    the table's dedup key. Idempotent: normalizing an already-normalized URL
    is a no-op."""
    url = (url or "").strip()
    parsed = urllib.parse.urlsplit(url)
    kept = [
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PARAM_PREFIXES) and k.lower() not in _TRACKING_PARAMS
    ]
    query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _site_domain(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def fetch_article(url: str) -> dict:
    """Download `url` and extract its main text via trafilatura. Returns
    `{title, text, site, published_at}`.

    Raises ArticleExtractionError when the page can't be downloaded at all,
    or the extracted body is under `_MIN_ARTICLE_CHARS` (200) — a
    paywall/login-wall/JS-only page typically yields only a nav-bar
    fragment at that length, and storing it as if it were the article would
    silently produce a fake summary and fake cards (see module docstring).
    Text longer than `_MAX_ARTICLE_CHARS` is truncated (context-window
    guard, not a cost one — see module docstring).
    """
    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise ArticleExtractionError(f"could not download the page: {url}")

    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    text = (text or "").strip()
    if len(text) < _MIN_ARTICLE_CHARS:
        raise ArticleExtractionError(
            f"extracted body too short ({len(text)} chars, need >= {_MIN_ARTICLE_CHARS}) "
            f"— likely a paywall, login wall, or JS-rendered page: {url}"
        )
    if len(text) > _MAX_ARTICLE_CHARS:
        text = text[:_MAX_ARTICLE_CHARS]

    metadata = trafilatura.extract_metadata(downloaded)
    title = (metadata.title if metadata else None) or url
    published_at = metadata.date if metadata else None

    return {
        "title": title,
        "text": text,
        "site": _site_domain(url),
        "published_at": published_at,
    }


def fetch_transcript(video: dict) -> tuple[str | None, dict]:
    """podcast.fetch_transcript-compatible entry point for kind='article'
    (signature-compatible with knowledge.youtube.fetch_captions), used by
    podcast.py's kind dispatch as the retry/reprocess fallback for an
    episode whose transcript_zh wasn't already stored at add time (the
    normal path — routes.knowledge.add_knowledge — stores it eagerly, see
    module docstring, so this mostly matters for future ingestion routes
    like the planned email intake, #655).

    Unlike knowledge.youtube.fetch_captions (returns (None, meta) when a
    video simply has no captions — a normal, non-error outcome), an article
    with no extractable body IS an error and ArticleExtractionError is
    allowed to propagate — podcast._process_episode's outer except stores
    it verbatim in episodes.error.
    """
    url = video.get("youtube_url") or video.get("video_id")
    article = fetch_article(url)
    meta = {"transcript_source": "article"}
    return article["text"], meta

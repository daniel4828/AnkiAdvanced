"""Shared ingestion core for the knowledge base "add a URL" / "paste text"
pipeline (issue #651/#652, extracted issue #655, extended #668).

`ingest_url()` is the ONE place that turns an arbitrary URL into a
podcast_episodes row (kind='video' for YouTube, kind='article' for
everything else trafilatura can extract a body from). `ingest_text()` is
the equivalent for a pasted article body (paywalled articles trafilatura
can't reach — #668) — same kind='article' row, same "build the row"
helper (`_store_article`), just a different source for the body text and
a different dedup key (no URL to hash, so the body itself is hashed
instead). routes/knowledge.py (POST /api/knowledge/add and
/api/knowledge/add-text, the paste boxes in the UI) and
knowledge/mailbox.py (IMAP mailbox polling, #655) call these functions
directly — no HTTP-calls-itself loop, no second parallel pipeline. This
repo has been burned by that exact mistake before (#643: two add-word
entry points, the bug fixed in one silently came back in the other) so
there must only ever be one ingestion path here too.

Deliberately framework-free: raises plain `IngestError` instead of
fastapi.HTTPException so non-HTTP callers (the mailbox script) don't need
to import fastapi just to catch a failure.
"""
import hashlib
import logging
import re

import database
import knowledge.article
import knowledge.youtube

logger = logging.getLogger(__name__)


class IngestError(Exception):
    """A URL or pasted text could not be turned into a podcast_episodes row
    (bad/unrecognized URL, metadata fetch failed, article extraction
    failed, pasted text too short, ...)."""


def ingest_url(url: str) -> dict:
    """Resolve `url` to a podcast_episodes row and return either
    {"episode_id": int} (newly created) or {"status": "already_exists",
    "episode_id": int} (deduped). Raises IngestError on failure."""
    url = (url or "").strip()
    if not url:
        raise IngestError("url is required")

    video_id = knowledge.youtube.parse_video_id(url)
    if video_id:
        return _ingest_video(url, video_id)
    return _ingest_article(url)


def _ingest_video(url: str, video_id: str) -> dict:
    existing = database.get_episode_by_video_id(video_id)
    if existing:
        return {"status": "already_exists", "episode_id": existing["id"]}

    try:
        meta = knowledge.youtube.fetch_metadata(video_id)
    except Exception as e:
        logger.warning("knowledge.ingest: oEmbed metadata lookup failed for %s: %s", video_id, e)
        raise IngestError(f"Could not fetch video metadata: {e}")

    title = meta.get("title") or video_id
    # translate_title (#651) is one cheap AI call — best-effort, must not
    # block/fail the ingest if it errors (translate_title itself already
    # swallows exceptions and returns None in that case).
    import ai
    title_en = ai.translate_title(title)

    episode_id = database.create_pending_episode(
        video_id=video_id,
        channel_id=meta.get("author_name"),
        title=title,
        published_at=None,
        youtube_url=url,
        audio_url=None,
        duration_seconds=None,
        kind="video",
    )
    if title_en:
        database.update_episode(episode_id, title_en=title_en)

    return {"episode_id": episode_id}


def _existing_episode(video_id: str) -> dict | None:
    """Dedup lookup shared by every ingestion path that lands on
    podcast_episodes.video_id (article-by-URL, article-by-pasted-text).
    Returns the already_exists response shape, or None if this is new."""
    existing = database.get_episode_by_video_id(video_id)
    if existing:
        return {"status": "already_exists", "episode_id": existing["id"]}
    return None


def _store_article(*, video_id: str, title: str, site: str | None, published_at,
                    source_url: str | None, text: str, transcript_source: str) -> dict:
    """Create the kind='article' podcast_episodes row and store `text` as
    transcript_zh immediately — this is the ONE row-building code path for
    both _ingest_article (URL) and ingest_text (pasted body, #668); they
    differ only in where `text`/`title`/`site`/`published_at` come from.
    Landing transcript_zh here (rather than deferring the fetch to process
    time) puts both paths straight into _process_episode's "reuse existing
    transcript" fast path when the frontend later calls
    POST /api/podcast/episodes/{id}/process — see article.py's docstring.

    Caller must already have deduped via `_existing_episode()`."""
    title = title or video_id
    # translate_title (#651) is one cheap AI call — best-effort, must not
    # block/fail the ingest if it errors (translate_title itself already
    # swallows exceptions and returns None in that case).
    import ai
    title_en = ai.translate_title(title)

    episode_id = database.create_pending_episode(
        video_id=video_id,
        channel_id=site,
        title=title,
        published_at=published_at,
        youtube_url=source_url,
        audio_url=None,
        duration_seconds=None,
        kind="article",
    )
    updates = {"transcript_zh": text, "transcript_source": transcript_source}
    if title_en:
        updates["title_en"] = title_en
    database.update_episode(episode_id, **updates)

    return {"episode_id": episode_id}


def _ingest_article(url: str) -> dict:
    """Article ingestion (#652): anything that isn't a recognized YouTube
    URL is treated as an article. normalize_url() (strips utm_*/fbclid/...)
    is the dedup key, so the same article shared via different links lands
    on the same row instead of duplicating.

    The dedup check happens BEFORE fetch_article() (a real network
    download) so an already-ingested URL never pays that cost again."""
    normalized = knowledge.article.normalize_url(url)
    dup = _existing_episode(normalized)
    if dup:
        return dup

    try:
        article = knowledge.article.fetch_article(url)
    except knowledge.article.ArticleExtractionError as e:
        raise IngestError(str(e))
    except Exception as e:
        logger.warning("knowledge.ingest: article extraction failed for %s: %s", url, e)
        raise IngestError(f"Could not fetch article: {e}")

    return _store_article(
        video_id=normalized,
        title=article["title"],
        site=article["site"],
        published_at=article.get("published_at"),
        source_url=url,
        text=article["text"],
        transcript_source="article",
    )


# Same paywall-stub / context-window guards as knowledge.article (#652) —
# a pasted body is stored exactly like a fetched one from here on, so the
# same thresholds apply for the same reasons (see that module's docstring).
_MIN_TEXT_CHARS = knowledge.article._MIN_ARTICLE_CHARS
_MAX_TEXT_CHARS = knowledge.article._MAX_ARTICLE_CHARS


def ingest_text(title: str, text: str, source_url: str | None = None) -> dict:
    """Ingest a pasted article body (#668) — for paywalled articles
    (Spiegel+, FAZ, ...) the server can't fetch, but the user can read in
    their browser and paste the text in directly. Same kind='article' row
    and transcript_zh storage as _ingest_article(), via _store_article();
    only the dedup key and body source differ (there's no URL to hash, so
    the body itself is hashed instead — see below).

    Raises IngestError if `text` is under 200 chars (same threshold as
    knowledge.article._MIN_ARTICLE_CHARS — too short to be a real article,
    not just a snippet/teaser). Text over 15000 chars is truncated (same
    ceiling as knowledge.article._MAX_ARTICLE_CHARS).
    """
    text = (text or "").strip()
    if len(text) < _MIN_TEXT_CHARS:
        raise IngestError(
            f"pasted text too short ({len(text)} chars, need >= {_MIN_TEXT_CHARS})"
        )
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS]

    title = (title or "").strip()
    source_url = (source_url or "").strip() or None

    # Whitespace must be normalized BEFORE hashing: the same article pasted
    # twice with different line-wrapping/blank-line whitespace must still
    # hash to the same dedup key, or every re-paste creates a new row.
    normalized = re.sub(r"\s+", " ", text).strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    video_id = f"pasted:{digest}"

    dup = _existing_episode(video_id)
    if dup:
        return dup

    return _store_article(
        video_id=video_id,
        title=title or "(untitled)",
        site=None,
        published_at=None,
        source_url=source_url,
        text=text,
        transcript_source="pasted",
    )

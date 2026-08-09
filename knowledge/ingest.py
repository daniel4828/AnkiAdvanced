"""Shared ingestion core for the knowledge base "add a URL" pipeline
(issue #651/#652, extracted issue #655).

`ingest_url()` is the ONE place that turns an arbitrary URL into a
podcast_episodes row (kind='video' for YouTube, kind='article' for
everything else trafilatura can extract a body from). Both
routes/knowledge.py (POST /api/knowledge/add, the paste-a-URL box in the
UI) and scripts/knowledge_mail_check.py (IMAP mailbox polling, #655) call
this function directly — no HTTP-calls-itself loop, no second parallel
pipeline. This repo has been burned by that exact mistake before (#643:
two add-word entry points, the bug fixed in one silently came back in the
other) so there must only ever be one ingestion path here too.

Deliberately framework-free: raises plain `IngestError` instead of
fastapi.HTTPException so non-HTTP callers (the mailbox script) don't need
to import fastapi just to catch a failure.
"""
import logging

import database
import knowledge.article
import knowledge.youtube

logger = logging.getLogger(__name__)


class IngestError(Exception):
    """A URL could not be turned into a podcast_episodes row (bad/unrecognized
    URL, metadata fetch failed, article extraction failed, ...)."""


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


def _ingest_article(url: str) -> dict:
    """Article ingestion (#652): anything that isn't a recognized YouTube
    URL is treated as an article. normalize_url() (strips utm_*/fbclid/...)
    is the dedup key, so the same article shared via different links lands
    on the same row instead of duplicating."""
    normalized = knowledge.article.normalize_url(url)
    existing = database.get_episode_by_video_id(normalized)
    if existing:
        return {"status": "already_exists", "episode_id": existing["id"]}

    try:
        article = knowledge.article.fetch_article(url)
    except knowledge.article.ArticleExtractionError as e:
        raise IngestError(str(e))
    except Exception as e:
        logger.warning("knowledge.ingest: article extraction failed for %s: %s", url, e)
        raise IngestError(f"Could not fetch article: {e}")

    title = article["title"]
    # translate_title (#651) is one cheap AI call — best-effort, must not
    # block/fail the ingest if it errors (translate_title itself already
    # swallows exceptions and returns None in that case).
    import ai
    title_en = ai.translate_title(title)

    episode_id = database.create_pending_episode(
        video_id=normalized,
        channel_id=article["site"],
        title=title,
        published_at=article.get("published_at"),
        youtube_url=url,
        audio_url=None,
        duration_seconds=None,
        kind="article",
    )
    # Store the body now (fetch_article() already paid for the download —
    # see its module docstring for why there's no cheap "metadata only"
    # step to defer this to). This lands in _process_episode's "reuse
    # existing transcript" fast path when the frontend later calls
    # POST /api/podcast/episodes/{id}/process.
    updates = {"transcript_zh": article["text"], "transcript_source": "article"}
    if title_en:
        updates["title_en"] = title_en
    database.update_episode(episode_id, **updates)

    return {"episode_id": episode_id}

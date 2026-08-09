"""Knowledge base ingestion API (issue #651, extended #652): turn a pasted
URL into a podcast_episodes row (kind='video' for YouTube, kind='article'
for everything else that trafilatura can extract a body from) that the
existing podcast pipeline can transcribe/summarize.

Mostly thin: for YouTube this route only resolves the URL to metadata and
inserts the pending row — transcription happens later via the existing
POST /api/podcast/episodes/{id}/process (background thread + polling,
#502). Articles are the exception (#652): there is no cheap "metadata
only" endpoint for an arbitrary URL the way YouTube has oEmbed — getting a
title reliably requires downloading and parsing the whole page, which is
the same cost as getting the body — so this route fetches + stores the
full article body synchronously here rather than deferring it. See
knowledge/article.py's module docstring for the full reasoning. Either way,
no new job/polling mechanism is introduced.
"""
import logging

import database
import knowledge.article
import knowledge.youtube
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class AddKnowledgeRequest(BaseModel):
    url: str


@router.post("/api/knowledge/add")
def add_knowledge(body: AddKnowledgeRequest):
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "url is required")

    video_id = knowledge.youtube.parse_video_id(url)
    if video_id:
        return _add_video(url, video_id)
    return _add_article(url)


def _add_video(url: str, video_id: str) -> dict:
    existing = database.get_episode_by_video_id(video_id)
    if existing:
        return {"status": "already_exists", "episode_id": existing["id"]}

    try:
        meta = knowledge.youtube.fetch_metadata(video_id)
    except Exception as e:
        logger.warning("knowledge.add: oEmbed metadata lookup failed for %s: %s", video_id, e)
        raise HTTPException(400, f"Could not fetch video metadata: {e}")

    title = meta.get("title") or video_id
    # translate_title (#651) is one cheap AI call — best-effort, must not
    # block/fail the add if it errors (translate_title itself already
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


def _add_article(url: str) -> dict:
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
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.warning("knowledge.add: article extraction failed for %s: %s", url, e)
        raise HTTPException(400, f"Could not fetch article: {e}")

    title = article["title"]
    # translate_title (#651) is one cheap AI call — best-effort, must not
    # block/fail the add if it errors (translate_title itself already
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

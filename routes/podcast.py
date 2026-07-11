"""Podcast crawler API (issue #479). Backend + scheduled-script only in this
issue — the review/settings UI is a follow-up.
"""
import logging

import database
import podcast
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/podcast/check")
def check_podcast():
    """Run one crawl cycle synchronously and return a summary. Called by
    scripts/podcast_check.py (cron) or manually for testing."""
    try:
        return podcast.run_check()
    except Exception as e:
        logger.error("podcast check failed: %s", e)
        raise HTTPException(500, str(e))


@router.get("/api/podcast/episodes")
def list_episodes(limit: int = 100):
    return database.list_episodes(limit=limit)


@router.get("/api/podcast/episodes/{episode_id}")
def get_episode(episode_id: int):
    episode = database.get_episode(episode_id)
    if not episode:
        raise HTTPException(404, "Episode not found")
    return episode


class PodcastConfigUpdate(BaseModel):
    detail_level: str | None = None
    enabled: str | None = None
    email_to: str | None = None
    channel_url: str | None = None
    whisper_fallback: str | None = None  # deprecated (#485), superseded by transcriber (#486)
    transcriber: str | None = None
    whisper_title_filter: str | None = None


@router.get("/api/podcast/config")
def get_config():
    return database.get_podcast_config()


@router.put("/api/podcast/config")
def update_config(body: PodcastConfigUpdate):
    updates = body.model_dump(exclude_none=True)
    if "detail_level" in updates and updates["detail_level"] not in ("short", "medium", "detailed"):
        raise HTTPException(400, "detail_level must be short, medium or detailed")
    if "transcriber" in updates and updates["transcriber"] not in ("auto", "notebooklm", "whisper", "off"):
        raise HTTPException(400, "transcriber must be auto, notebooklm, whisper or off")
    for key, value in updates.items():
        database.set_podcast_config(key, value)
    if "channel_url" in updates:
        # The cached channel_id belongs to the previous channel URL — clear it
        # so the next crawl re-resolves the handle (empty string is falsy for
        # resolve_channel_id's cache check).
        database.set_podcast_config("channel_id", "")
    return database.get_podcast_config()

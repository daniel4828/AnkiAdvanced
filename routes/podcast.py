"""Podcast crawler API (issue #479, RSS source #497, Tingwu transcriber
#498, per-feed manager #502).
"""
import json
import logging
import threading
from xml.etree import ElementTree

import database
import podcast
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# Episode ids currently being manually processed (#502, POST
# /api/podcast/episodes/{id}/process) — guards against double-submitting the
# same episode (e.g. a double click) and lets the episode-list endpoints
# report a "processing" status without writing anything to the DB for it.
_PROCESSING_IDS: set[int] = set()
_PROCESSING_LOCK = threading.Lock()


@router.post("/api/podcast/check")
def check_podcast():
    """Run one crawl cycle synchronously and return a summary. Called by
    scripts/podcast_check.py (cron) or manually for testing."""
    try:
        return podcast.run_check()
    except Exception as e:
        logger.error("podcast check failed: %s", e)
        raise HTTPException(500, str(e))


def _overlay_processing_status(episode: dict) -> dict:
    """Cosmetic-only: episodes currently being manually processed (#502) show
    status='processing' in API responses without that ever being written to
    the DB row (the DB status stays 'pending' until the background thread
    finishes and updates it for real)."""
    if episode["id"] in _PROCESSING_IDS:
        episode = {**episode, "status": "processing"}
    return episode


@router.get("/api/podcast/episodes")
def list_episodes(limit: int = 100, feed_id: int | None = None):
    feed_url = None
    if feed_id is not None:
        feed = database.get_feed(feed_id)
        if not feed:
            raise HTTPException(404, "Feed not found")
        feed_url = feed["url"]
    episodes = database.list_episodes(limit=limit, feed_url=feed_url)
    return [_overlay_processing_status(e) for e in episodes]


@router.get("/api/podcast/episodes/{episode_id}")
def get_episode(episode_id: int):
    episode = database.get_episode(episode_id)
    if not episode:
        raise HTTPException(404, "Episode not found")
    return _overlay_processing_status(episode)


@router.post("/api/podcast/episodes/{episode_id}/retry")
def retry_episode(episode_id: int):
    """Re-run the full processing pipeline for one failed episode (#491) —
    the manual per-episode recovery path after e.g. an expired YouTube cookie
    failed a whole batch. Only error/no_transcript episodes are retryable;
    summarized episodes are done and pending ones are still being worked on."""
    episode = database.get_episode(episode_id)
    if not episode:
        raise HTTPException(404, "Episode not found")
    if episode["status"] == "summarized":
        raise HTTPException(
            400, "Episode is already summarized — nothing to retry"
        )
    return podcast.retry_episode(episode_id)


def _process_episode_thread(episode_id: int) -> None:
    """Background thread body for POST /episodes/{id}/process (#502) — runs
    the full pipeline via podcast.retry_episode (which itself never raises,
    it stores status='error' on failure), but is wrapped again here anyway
    since a raise inside a bare thread would otherwise vanish silently."""
    try:
        podcast.retry_episode(episode_id)
    except Exception as e:
        logger.error("podcast: manual processing failed for episode %s: %s", episode_id, e)
    finally:
        with _PROCESSING_LOCK:
            _PROCESSING_IDS.discard(episode_id)


@router.post("/api/podcast/episodes/{episode_id}/process")
def process_episode(episode_id: int):
    """Manually trigger transcription+summary for one episode (#502) — used
    by the podcast manager UI for episodes from a non-auto-process feed (or
    a feed's backfilled back catalog), which are stored metadata-only until
    Daniel picks them to transcribe. Runs in a background thread; the
    response returns immediately so the UI can start polling."""
    episode = database.get_episode(episode_id)
    if not episode:
        raise HTTPException(404, "Episode not found")
    if episode["status"] == "summarized":
        raise HTTPException(400, "Episode is already summarized — nothing to process")
    with _PROCESSING_LOCK:
        if episode_id in _PROCESSING_IDS:
            raise HTTPException(409, "Episode is already being processed")
        _PROCESSING_IDS.add(episode_id)
    threading.Thread(target=_process_episode_thread, args=(episode_id,), daemon=True).start()
    return {"status": "processing"}


@router.get("/api/podcast/feeds")
def list_feeds():
    return database.list_feeds()


class FeedCreate(BaseModel):
    url: str


@router.post("/api/podcast/feeds")
def create_feed(body: FeedCreate):
    """Add a new RSS feed subscription. Validates the URL is a parseable RSS
    feed (fetches it once, synchronously) and pulls the channel <title> for
    display before storing — new feeds default to auto_process=0 (manual),
    matching #502's "opt in to automation per-source" design."""
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "url is required")
    try:
        root = ElementTree.fromstring(podcast._http_get(url, timeout=30))
    except Exception as e:
        raise HTTPException(400, f"Not a valid RSS feed: {e}")
    title_el = root.find("channel/title")
    title = title_el.text.strip() if title_el is not None and title_el.text else None
    try:
        feed_id = database.create_feed(url, title=title, auto_process=0)
    except Exception:
        # sqlite3.IntegrityError on the UNIQUE(url) constraint — anything
        # else here would be unexpected, but still just a 400 (bad input),
        # not a 500 (server bug).
        raise HTTPException(400, "Feed already exists")
    return database.get_feed(feed_id)


class FeedUpdate(BaseModel):
    auto_process: bool | None = None
    title: str | None = None


@router.put("/api/podcast/feeds/{feed_id}")
def update_feed(feed_id: int, body: FeedUpdate):
    if not database.get_feed(feed_id):
        raise HTTPException(404, "Feed not found")
    updates = body.model_dump(exclude_none=True)
    if "auto_process" in updates:
        updates["auto_process"] = int(updates["auto_process"])
    if updates:
        database.update_feed(feed_id, **updates)
    return database.get_feed(feed_id)


@router.delete("/api/podcast/feeds/{feed_id}")
def delete_feed(feed_id: int):
    if not database.get_feed(feed_id):
        raise HTTPException(404, "Feed not found")
    database.delete_feed(feed_id)
    return {"deleted": True}


class PodcastConfigUpdate(BaseModel):
    detail_level: str | None = None
    enabled: str | None = None
    email_to: str | None = None
    feeds: list[str] | None = None  # deprecated (#502) — feeds now live in podcast_feeds; kept only so old clients/scripts don't 422
    whisper_fallback: str | None = None  # deprecated (#485), superseded by transcriber (#486)
    transcriber: str | None = None
    whisper_max_minutes: str | None = None
    summarizer: str | None = None


@router.get("/api/podcast/config")
def get_config():
    cfg = database.get_podcast_config()
    if "feeds" in cfg:
        try:
            cfg["feeds"] = json.loads(cfg["feeds"])
        except (TypeError, ValueError):
            cfg["feeds"] = []
    return cfg


@router.put("/api/podcast/config")
def update_config(body: PodcastConfigUpdate):
    updates = body.model_dump(exclude_none=True)
    if "detail_level" in updates and updates["detail_level"] not in ("short", "medium", "detailed"):
        raise HTTPException(400, "detail_level must be short, medium or detailed")
    if "transcriber" in updates and updates["transcriber"] not in ("auto", "tingwu", "notebooklm", "whisper", "off"):
        raise HTTPException(400, "transcriber must be auto, tingwu, notebooklm, whisper or off")
    if "whisper_max_minutes" in updates:
        try:
            float(updates["whisper_max_minutes"])
        except (TypeError, ValueError):
            raise HTTPException(400, "whisper_max_minutes must be a number (0 = no limit)")
    if "summarizer" in updates and updates["summarizer"] not in ("auto", "api"):
        raise HTTPException(400, "summarizer must be auto or api")
    if "feeds" in updates:
        if not updates["feeds"] or not all(isinstance(u, str) and u.strip() for u in updates["feeds"]):
            raise HTTPException(400, "feeds must be a non-empty list of RSS feed URLs")
        updates["feeds"] = json.dumps(updates["feeds"])
    for key, value in updates.items():
        database.set_podcast_config(key, value)
    return get_config()

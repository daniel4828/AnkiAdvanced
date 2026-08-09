"""Knowledge base ingestion API (issue #651): turn a pasted URL into a
podcast_episodes row (kind='video' for YouTube, more kinds land in later
stages) that the existing podcast pipeline can transcribe/summarize.

Deliberately thin: this route only resolves the URL to metadata and inserts
the pending row. Transcription + summary is NOT done here — the frontend
takes the returned episode_id and calls the already-existing
POST /api/podcast/episodes/{id}/process (background thread + polling, #502).
No new job/polling mechanism is introduced.
"""
import logging

import database
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
    if not video_id:
        # Stage B only understands YouTube — article ingestion is stage C
        # (see schema.sql's podcast_episodes.kind comment).
        raise HTTPException(400, "Not a recognized YouTube URL (article ingestion isn't supported yet)")

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

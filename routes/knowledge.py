"""Knowledge base ingestion API (issue #651, extended #652, #668): turn a
pasted URL — or a pasted article body (#668, for paywalled articles the
server can't fetch) — into a podcast_episodes row (kind='video' for
YouTube, kind='article' for everything else / pasted text) that the
existing podcast pipeline can transcribe/summarize.

All the actual resolution logic lives in knowledge/ingest.py's
ingest_url()/ingest_text() (extracted issue #655, extended #668) so the
IMAP mailbox script (knowledge/mailbox.py) can call the exact same
pipeline instead of hitting this HTTP endpoint or reimplementing it — one
ingestion path per source type, see that module's docstring for why.
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import knowledge.ingest

logger = logging.getLogger(__name__)
router = APIRouter()


class AddKnowledgeRequest(BaseModel):
    url: str


class AddKnowledgeTextRequest(BaseModel):
    title: str
    text: str
    source_url: str | None = None


@router.post("/api/knowledge/add")
def add_knowledge(body: AddKnowledgeRequest):
    try:
        return knowledge.ingest.ingest_url(body.url)
    except knowledge.ingest.IngestError as e:
        raise HTTPException(400, str(e))


@router.post("/api/knowledge/add-text")
def add_knowledge_text(body: AddKnowledgeTextRequest):
    """Paste-a-body counterpart to POST /api/knowledge/add (#668). Same
    response contract ({episode_id} or {status:"already_exists",
    episode_id}) — the frontend's add flow branches on URL vs. text but
    otherwise treats the result identically (poll .../process next)."""
    try:
        return knowledge.ingest.ingest_text(body.title, body.text, source_url=body.source_url)
    except knowledge.ingest.IngestError as e:
        raise HTTPException(400, str(e))

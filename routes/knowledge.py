"""Knowledge base ingestion API (issue #651, extended #652): turn a pasted
URL into a podcast_episodes row (kind='video' for YouTube, kind='article'
for everything else that trafilatura can extract a body from) that the
existing podcast pipeline can transcribe/summarize.

All the actual resolution logic lives in knowledge/ingest.py's
ingest_url() (extracted issue #655) so the IMAP mailbox script
(scripts/knowledge_mail_check.py) can call the exact same pipeline instead
of hitting this HTTP endpoint or reimplementing it — one ingestion path,
see that module's docstring for why.
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import knowledge.ingest

logger = logging.getLogger(__name__)
router = APIRouter()


class AddKnowledgeRequest(BaseModel):
    url: str


@router.post("/api/knowledge/add")
def add_knowledge(body: AddKnowledgeRequest):
    try:
        return knowledge.ingest.ingest_url(body.url)
    except knowledge.ingest.IngestError as e:
        raise HTTPException(400, str(e))

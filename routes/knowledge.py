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

import database
import knowledge.ingest

logger = logging.getLogger(__name__)
router = APIRouter()


class AddKnowledgeRequest(BaseModel):
    url: str
    # #731: ticked at paste time for material critical of China, which is the
    # one case DeepSeek can't summarize honestly. Defaults to False so the
    # iOS-shortcut / mailbox callers that don't send it keep the cheap
    # DeepSeek-first behavior.
    china_critical: bool = False


class AddKnowledgeTextRequest(BaseModel):
    title: str
    text: str
    source_url: str | None = None
    china_critical: bool = False


@router.post("/api/knowledge/add")
def add_knowledge(body: AddKnowledgeRequest):
    try:
        return knowledge.ingest.ingest_url(body.url, china_critical=body.china_critical)
    except knowledge.ingest.IngestError as e:
        raise HTTPException(400, str(e))


@router.post("/api/knowledge/add-text")
def add_knowledge_text(body: AddKnowledgeTextRequest):
    """Paste-a-body counterpart to POST /api/knowledge/add (#668). Same
    response contract ({episode_id} or {status:"already_exists",
    episode_id}) — the frontend's add flow branches on URL vs. text but
    otherwise treats the result identically (poll .../process next)."""
    try:
        return knowledge.ingest.ingest_text(body.title, body.text, source_url=body.source_url,
                                            china_critical=body.china_critical)
    except knowledge.ingest.IngestError as e:
        raise HTTPException(400, str(e))


# ── Known words (#710) ──────────────────────────────────────────────────────
# Words Daniel knows without having studied them here. Marking one only
# widens zh_annotate's "already known" test (see database.known_words_exists)
# — no card is created and nothing is scheduled, which is the whole point:
# these are words he does NOT want to see again.


class KnownWordRequest(BaseModel):
    word: str
    # #804: known_words is keyed per language (see #803's known_words.lang) —
    # a known French word and a known Chinese word that happen to share a
    # surface form must not silently mark each other known. Defaults to 'zh'
    # so pre-#804 callers (no lang field sent) keep their existing behavior.
    lang: str = "zh"


@router.get("/api/known-words")
def get_known_words(lang: str | None = None):
    return {"words": database.list_known_words(lang)}


@router.post("/api/known-words")
def add_known_word(body: KnownWordRequest):
    word = (body.word or "").strip()
    if not word:
        raise HTTPException(400, "word required")
    database.add_known_word(word, body.lang)
    return {"status": "ok", "word": word, "lang": body.lang}


@router.delete("/api/known-words/{word}")
def delete_known_word(word: str, lang: str = "zh"):
    """Undo. Reports a miss instead of pretending — a 404 here means the
    frontend and the database disagree about what is on the list."""
    if not database.remove_known_word(word.strip(), lang):
        raise HTTPException(404, "word not on the known list")
    return {"status": "ok", "word": word, "lang": lang}

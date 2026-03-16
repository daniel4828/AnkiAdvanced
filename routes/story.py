import logging
import random
from datetime import date

import database
import ai
import tts
from fastapi import APIRouter

from .utils import DISABLE_AI, leaf_ids

logger = logging.getLogger(__name__)
router = APIRouter()


def _log_story(story: dict) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    sentences = story.get("sentences", [])
    lines = [f"  Story id={story['id']} ({len(sentences)} sentences):"]
    for s in sentences:
        lines.append(f"    {s['position']+1}. {s['sentence_zh']}")
        lines.append(f"       {s['sentence_en']}")
    logger.debug("\n".join(lines))


def _get_cards_for_story(deck_id: int, category: str) -> list:
    ids = leaf_ids(deck_id, category)
    return database.get_due_cards_multi(ids, category) if len(ids) > 1 \
        else database.get_due_cards(deck_id, category)


@router.get("/api/story/{deck_id}/{category}")
def get_story(deck_id: int, category: str,
              topic: str | None = None, max_hsk: int = 2):
    today = date.today().isoformat()
    # Only use cached story if no custom options were provided
    if not topic and max_hsk == 2:
        story = database.get_active_story(today, category, deck_id)
        if story:
            story["sentences"] = database.get_story_sentences(story["id"])
            logger.info("story  CACHED  deck=%d cat=%s sentences=%d story_id=%d",
                        deck_id, category, len(story["sentences"]), story["id"])
            _log_story(story)
            return story

    if DISABLE_AI:
        logger.info("story  DISABLED (DISABLE_AI=1) deck=%d cat=%s", deck_id, category)
        return None

    story = None
    cards = _get_cards_for_story(deck_id, category)
    logger.info("story  GENERATE deck=%d cat=%s due_cards=%d topic=%r max_hsk=%d",
                deck_id, category, len(cards), topic, max_hsk)
    if cards:
        try:
            ids = leaf_ids(deck_id, category)
            preset = database.get_preset_for_deck(ids[0] if ids else deck_id)
            if preset.get("randomize_story_order", 1):
                random.shuffle(cards)
            sentences = ai.generate_story(cards, topic=topic, max_hsk=max_hsk)
            for i, s in enumerate(sentences):
                s["position"] = i
            database.create_story(today, category, deck_id, sentences)
            story = database.get_active_story(today, category, deck_id)
        except Exception as e:
            logger.error("story  generation error: %s", e)

    if story:
        story["sentences"] = database.get_story_sentences(story["id"])
        logger.info("story  SAVED   deck=%d cat=%s sentences=%d",
                    deck_id, category, len(story["sentences"]))
        _log_story(story)
    return story


@router.post("/api/story/{deck_id}/{category}/regenerate")
def regenerate_story(deck_id: int, category: str,
                     topic: str | None = None, max_hsk: int = 2):
    if DISABLE_AI:
        return None
    today = date.today().isoformat()
    cards = _get_cards_for_story(deck_id, category)
    logger.info("regen  deck=%d cat=%s due_cards=%d topic=%r max_hsk=%d",
                deck_id, category, len(cards), topic, max_hsk)
    if not cards:
        return None
    ids = leaf_ids(deck_id, category)
    preset = database.get_preset_for_deck(ids[0] if ids else deck_id)
    if preset.get("randomize_story_order", 1):
        random.shuffle(cards)
    sentences = ai.generate_story(cards, topic=topic, max_hsk=max_hsk)
    for i, s in enumerate(sentences):
        s["position"] = i
    database.create_story(today, category, deck_id, sentences)
    story = database.get_active_story(today, category, deck_id)
    if story:
        story["sentences"] = database.get_story_sentences(story["id"])
        logger.info("regen  SAVED sentences=%d", len(story["sentences"]))
        _log_story(story)
    return story


@router.get("/api/story/{deck_id}/{category}/count")
def story_count(deck_id: int, category: str):
    """Return sentence count and whether a cached story already exists today."""
    today = date.today().isoformat()
    has_story = database.get_active_story(today, category, deck_id) is not None
    cards = _get_cards_for_story(deck_id, category)
    return {"count": len(cards), "has_story": has_story}


@router.post("/api/speak")
def speak(text: str):
    try:
        tts.speak_sync(text)
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/speak-multi")
def speak_multi(body: dict):
    try:
        tts.speak_multi(body.get("texts", []))
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/speak-stop")
def speak_stop():
    tts.stop()
    return {"ok": True}


@router.post("/api/preload")
def preload(text: str):
    try:
        tts.preload(text)
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/preload-session/{deck_id}/{category}")
async def preload_session(deck_id: int, category: str):
    today = date.today().isoformat()
    story = database.get_active_story(today, category, deck_id)
    if story:
        sentences = database.get_story_sentences(story["id"])
        texts = [s["sentence_zh"] for s in sentences if s.get("sentence_zh")]
        logger.info("tts  preloading %d sentences", len(texts))
        try:
            await tts.preload_all_async(texts)
            logger.info("tts  preload done")
        except Exception as e:
            logger.warning("tts  preload error: %s", e)
    return {"ok": True}

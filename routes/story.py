import logging

import jieba
import database
import ai
import tts
from fastapi import APIRouter
from fastapi.responses import FileResponse

from .utils import DISABLE_AI, leaf_ids

# Suppress jieba's startup log messages
jieba.setLogLevel(logging.ERROR)


def _add_tokens(sentences: list[dict]) -> list[dict]:
    """Ensure every sentence has tokens in [[text, word_id_or_null], ...] format.

    New stories already have AI-provided tokens stored in the DB.
    Old stories without tokens fall back to jieba segmentation (word_id=null for all).
    """
    for s in sentences:
        if s.get("tokens"):
            continue
        zh = s.get("sentence_zh") or ""
        s["tokens"] = [[tok, None] for tok in jieba.lcut(zh)] if zh else []
    return sentences

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
    if category == "unified":
        cards = database.get_due_cards_unified(deck_id)
    else:
        ids = leaf_ids(deck_id, category)
        if not ids:
            return []
        # sibling_suppression=True: each word appears only once across all categories
        # (the AI prompt should not receive the same word from both Listening and Reading)
        cards = (database.get_due_cards_multi(ids, category)
                 if len(ids) > 1
                 else database.get_due_cards(ids[0], category))
        # Sentence notes are standalone — never embed them in AI-generated stories
        cards = [c for c in cards if c.get("note_type") != "sentence"]

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "=== [story] 发给 AI 的词表（AI词表）===\n"
            "  deck=%d  cat=%s  共 %d 词（埋藏卡已排除，new/learning已交错，这就是故事句子的顺序）:\n%s",
            deck_id, category, len(cards),
            "\n".join(
                f"  {c.get('word_zh', '?'):<16}  {c.get('pinyin', ''):<28}  [{c.get('state', '?')}]"
                for c in cards
            ) or "  (empty)",
        )
    return cards


CHUNK_SIZE = 70


def _generate_story_sentences(cards: list, *, topic, max_hsk, model, progress_key,
                               grammar_focus, grammar_pct, mode) -> tuple[list, str]:
    """Generate story sentences, splitting into chunks of CHUNK_SIZE when cards > CHUNK_SIZE."""
    if len(cards) <= CHUNK_SIZE:
        return ai.generate_story(cards, topic=topic, max_hsk=max_hsk, model=model,
                                 progress_key=progress_key, grammar_focus=grammar_focus,
                                 grammar_pct=grammar_pct, mode=mode)

    chunks = [cards[i:i + CHUNK_SIZE] for i in range(0, len(cards), CHUNK_SIZE)]
    logger.info("story  CHUNKED %d cards → %d chunks of ≤%d", len(cards), len(chunks), CHUNK_SIZE)
    all_sentences: list[dict] = []
    combined_prompt = ""
    for idx, chunk in enumerate(chunks):
        ai._set_progress(progress_key, phase="generating",
                         msg=f"Generating chunk {idx + 1}/{len(chunks)}…",
                         percent=5 + int(85 * idx / len(chunks)))
        chunk_sentences, chunk_prompt = ai.generate_story(
            chunk, topic=topic, max_hsk=max_hsk, model=model,
            progress_key=None,  # suppress per-chunk progress spam
            grammar_focus=grammar_focus, grammar_pct=grammar_pct, mode=mode,
        )
        all_sentences.extend(chunk_sentences)
        if idx == 0:
            combined_prompt = chunk_prompt
    return all_sentences, combined_prompt


ALLOWED_MODELS = {
    "glm-4-flash",
    "glm-4-air",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "qwen-turbo",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
}
DEFAULT_MODEL = ai.DEFAULT_MODEL


def _validated_model(model: str | None) -> str:
    if model and model in ALLOWED_MODELS:
        return model
    return DEFAULT_MODEL


@router.get("/api/story/{deck_id}/{category}")
def get_story(deck_id: int, category: str,
              topic: str | None = None, max_hsk: int = 3,
              model: str | None = None,
              grammar_focus: str | None = None, grammar_pct: int = 75,
              mode: str = "story"):
    if database.is_sentences_deck(deck_id):
        return None

    today = database.anki_today().isoformat()
    # Always return today's cached story — custom params only apply when generating a new one
    story = database.get_active_story(today, category, deck_id)
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]))
        logger.info("story  CACHED  deck=%d cat=%s sentences=%d story_id=%d",
                    deck_id, category, len(story["sentences"]), story["id"])
        _log_story(story)
        return story

    if DISABLE_AI:
        logger.info("story  DISABLED (DISABLE_AI=1) deck=%d cat=%s", deck_id, category)
        return None

    chosen_model = _validated_model(model)
    story = None
    cards = _get_cards_for_story(deck_id, category)
    logger.info("story  GENERATE deck=%d cat=%s due_cards=%d topic=%r max_hsk=%d model=%s mode=%s",
                deck_id, category, len(cards), topic, max_hsk, chosen_model, mode)
    if cards:
        ai.fix_definition_commas(cards)
        progress_key = f"{deck_id}/{category}"
        ai._story_progress[progress_key] = {"phase": "starting", "msg": "Starting…", "percent": 5}
        last_error = None
        try:
            sentences, prompt_text = _generate_story_sentences(
                cards, topic=topic, max_hsk=max_hsk, model=chosen_model,
                progress_key=progress_key, grammar_focus=grammar_focus,
                grammar_pct=grammar_pct, mode=mode)
            for i, s in enumerate(sentences):
                s["position"] = i
            database.create_story(today, category, deck_id, sentences, prompt_text, topic)
            story = database.get_active_story(today, category, deck_id)
        except Exception as e:
            last_error = e
            logger.error("story  generation error: %s", e)
        finally:
            ai._story_progress.pop(progress_key, None)
        if last_error is not None:
            return {
                "error": True,
                "reason": str(last_error),
                "model": chosen_model,
                "has_history": database.has_story_history(deck_id, category),
            }

    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]))
        logger.info("story  SAVED   deck=%d cat=%s sentences=%d",
                    deck_id, category, len(story["sentences"]))
        _log_story(story)
    return story


@router.post("/api/story/{deck_id}/{category}/regenerate")
def regenerate_story(deck_id: int, category: str,
                     topic: str | None = None, max_hsk: int = 3,
                     model: str | None = None,
                     grammar_focus: str | None = None, grammar_pct: int = 75,
                     mode: str = "story"):
    if database.is_sentences_deck(deck_id):
        return None
    if DISABLE_AI:
        return None
    chosen_model = _validated_model(model)
    today = database.anki_today().isoformat()
    cards = _get_cards_for_story(deck_id, category)
    logger.info("regen  deck=%d cat=%s due_cards=%d topic=%r max_hsk=%d model=%s mode=%s",
                deck_id, category, len(cards), topic, max_hsk, chosen_model, mode)
    if not cards:
        return None
    ai.fix_definition_commas(cards)
    progress_key = f"{deck_id}/{category}"
    ai._story_progress[progress_key] = {"phase": "starting", "msg": "Starting…", "percent": 5}
    last_error = None
    try:
        sentences, prompt_text = _generate_story_sentences(
            cards, topic=topic, max_hsk=max_hsk, model=chosen_model,
            progress_key=progress_key, grammar_focus=grammar_focus,
            grammar_pct=grammar_pct, mode=mode)
        for i, s in enumerate(sentences):
            s["position"] = i
        database.create_story(today, category, deck_id, sentences, prompt_text, topic)
        story = database.get_active_story(today, category, deck_id)
    except Exception as e:
        last_error = e
        logger.error("regen  generation error: %s", e)
    finally:
        ai._story_progress.pop(progress_key, None)
    if last_error is not None:
        return {
            "error": True,
            "reason": str(last_error),
            "model": chosen_model,
            "has_history": database.has_story_history(deck_id, category),
        }
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]))
        logger.info("regen  SAVED sentences=%d", len(story["sentences"]))
        _log_story(story)
    return story


@router.get("/api/story/{deck_id}/{category}/history")
def get_history_story(deck_id: int, category: str):
    """Return the most recent story for this deck+category, regardless of date."""
    story = database.get_latest_story(deck_id, category)
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]))
    return story


@router.get("/api/story/{deck_id}/{category}/count")
def story_count(deck_id: int, category: str):
    """Return sentence count, whether a cached story exists today, and token estimate."""
    if database.is_sentences_deck(deck_id):
        return {"count": 0, "has_story": False, "estimated_tokens": 0}
    today = database.anki_today().isoformat()
    has_story = database.get_active_story(today, category, deck_id) is not None
    cards = _get_cards_for_story(deck_id, category)
    return {
        "count": len(cards),
        "has_story": has_story,
        "estimated_tokens": ai.estimate_story_tokens(len(cards)),
    }


@router.get("/api/tts-file")
async def tts_file(text: str):
    """Return the cached mp3 for text (generating it if needed). Used by the browser Audio API."""
    path = await tts.get_cached_path(text)
    return FileResponse(path, media_type="audio/mpeg")


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
        tts.speak_multi(body.get("texts", []), start_idx=body.get("start_idx", 0))
    except Exception:
        pass
    return {"ok": True}


@router.get("/api/speak-status")
def speak_status():
    return tts.get_status()


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
async def preload_session(deck_id: int, category: str, quick: bool = False):
    progress_key = f"{deck_id}/{category}"
    if quick:
        ids = leaf_ids(deck_id, category) or [deck_id]
        cards = database.get_due_cards_multi(ids, category) if len(ids) > 1 else database.get_due_cards(ids[0], category)
        texts = [c["word_zh"] for c in cards if c.get("word_zh")]
        logger.info("tts  quick preloading %d word audio files", len(texts))
        try:
            await tts.preload_all_async(texts, progress_key=progress_key)
        except Exception as e:
            logger.warning("tts  quick preload error: %s", e)
    else:
        today = database.anki_today().isoformat()
        story = database.get_active_story(today, category, deck_id)
        if story:
            sentences = _add_tokens(database.get_story_sentences(story["id"]))
            texts = [s["sentence_zh"] for s in sentences if s.get("sentence_zh")]
            logger.info("tts  preloading %d sentences", len(texts))
            try:
                await tts.preload_all_async(texts, progress_key=progress_key)
                logger.info("tts  preload done")
            except Exception as e:
                logger.warning("tts  preload error: %s", e)
    tts._preload_progress.pop(progress_key, None)
    return {"ok": True}


@router.get("/api/tts-progress/{deck_id}/{category}")
async def tts_progress(deck_id: int, category: str):
    key = f"{deck_id}/{category}"
    return tts._preload_progress.get(key, {"done": 0, "total": 0})


@router.get("/api/story-progress/{deck_id}/{category}")
def story_progress_endpoint(deck_id: int, category: str):
    key = f"{deck_id}/{category}"
    return ai._story_progress.get(key, {"phase": "idle", "msg": "", "percent": 0})

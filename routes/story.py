import json
import logging
import math
import os
import random
import threading

import jieba
import database
import ai
import news_fetcher
import tts
from fastapi import APIRouter
from fastapi.responses import FileResponse

from .utils import DISABLE_AI, leaf_ids

KAHNEMAN_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "kahneman_chapters.json")

# 每次 Kahneman AI 调用最多处理的词数（词太多会漏词、句子质量下降）
MAX_KAHNEMAN_BATCH = 10

# 《思考，快与慢》原书的 5 个部分（Part）—— 按章节号区间划分。
# 数据文件不存部分信息，统一在此按章节号计算，保证一致性。
_KAHNEMAN_PARTS = [
    (1, 9, 1, "两个系统", "Two Systems"),
    (10, 18, 2, "启发法与偏见", "Heuristics and Biases"),
    (19, 24, 3, "过度自信", "Overconfidence"),
    (25, 34, 4, "选择", "Choices"),
    (35, 38, 5, "两个自我", "Two Selves"),
]

# 部分序号 → 中文数字（用于「第一部分」等标签）
_PART_CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五"}
# 部分序号 → 罗马数字（原书英文用「Part I.」格式）
_PART_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}


def _attach_part(chapter: dict) -> dict:
    """Return a copy of `chapter` with part_number / part_zh / part_en attached."""
    num = chapter.get("number")
    for lo, hi, pnum, pzh, pen in _KAHNEMAN_PARTS:
        if num is not None and lo <= num <= hi:
            return {
                **chapter,
                "part_number": pnum,
                "part_zh": f"第{_PART_CN_NUM[pnum]}部分 {pzh}",
                "part_en": f"Part {_PART_ROMAN[pnum]}. {pen}",
            }
    return dict(chapter)


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

# In-flight background story generations, keyed by f"{deck_id}/{category}".
# Guards against starting a second generation for the same deck+category while
# one is already running (e.g. the frontend polling repeatedly in background mode).
_generating: set[str] = set()
_gen_lock = threading.Lock()


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
    "gpt-5-mini",
}
DEFAULT_MODEL = ai.DEFAULT_MODEL


def _validated_model(model: str | None) -> str:
    if model and model in ALLOWED_MODELS:
        return model
    return DEFAULT_MODEL


def _generate_and_store(deck_id: int, category: str, today: str, cards: list, *,
                        topic, max_hsk, model, grammar_focus, grammar_pct, mode,
                        chapter_ids, articles=None, progress_key) -> dict | None:
    """Generate a story for `cards`, persist it, and return the stored story
    (with sentences) — or an error dict on failure, or None if there is nothing
    to generate. Shared by the synchronous GET, the background thread, and regenerate.

    Sets ai._story_progress[progress_key] to a "starting" state up front; the
    generate functions update it during the run. Callers own terminal cleanup.
    """
    if not cards:
        return None
    ai.fix_definition_commas(cards)
    ai._story_progress[progress_key] = {"phase": "starting", "msg": "Starting…", "percent": 5}
    try:
        if mode == "kahneman":
            parsed_chapter_ids = [int(x) for x in chapter_ids.split(",") if x.strip()] if chapter_ids else []
            sentences, prompt_text = _generate_kahneman_story_sentences(
                cards, parsed_chapter_ids, model=model, progress_key=progress_key)
        elif mode == "news":
            if not articles:
                # No pasted articles → auto-fetch today's news and let the AI
                # condense it into briefing source articles (issue #387).
                # Rebinding `articles` here also persists the fetched articles
                # in gen_params below, so Again-regeneration reuses them.
                articles = _auto_news_articles(model=model, progress_key=progress_key)
            sentences, prompt_text = _generate_news_story_sentences(
                cards, articles, model=model, progress_key=progress_key)
        else:
            sentences, prompt_text = _generate_story_sentences(
                cards, topic=topic, max_hsk=max_hsk, model=model,
                progress_key=progress_key, grammar_focus=grammar_focus,
                grammar_pct=grammar_pct, mode=mode)
        for i, s in enumerate(sentences):
            s["position"] = i
        gen_params = _gen_params_dict(
            topic=topic, max_hsk=max_hsk, model=model,
            grammar_focus=grammar_focus, grammar_pct=grammar_pct,
            mode=mode, chapter_ids=chapter_ids, articles=articles)
        database.create_story(today, category, deck_id, sentences, prompt_text, topic, gen_params)
        story = database.get_active_story(today, category, deck_id)
    except Exception as e:
        logger.error("story  generation error: %s", e)
        return {
            "error": True,
            "reason": str(e),
            "model": model,
            "has_history": database.has_story_history(deck_id, category),
        }
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]))
        logger.info("story  SAVED   deck=%d cat=%s sentences=%d",
                    deck_id, category, len(story["sentences"]))
        _log_story(story)
    return story


def _start_background_generation(deck_id: int, category: str, today: str, cards: list, *,
                                 topic, max_hsk, model, grammar_focus, grammar_pct,
                                 mode, chapter_ids, articles=None, progress_key) -> None:
    """Spawn a daemon thread that generates+stores a story, recording a terminal
    progress state (done/error) the frontend can poll. De-duped by progress_key."""
    def _run() -> None:
        logger.info("story  BG-START deck=%d cat=%s model=%s", deck_id, category, model)
        try:
            result = _generate_and_store(
                deck_id, category, today, cards,
                topic=topic, max_hsk=max_hsk, model=model,
                grammar_focus=grammar_focus, grammar_pct=grammar_pct,
                mode=mode, chapter_ids=chapter_ids, articles=articles, progress_key=progress_key)
            if isinstance(result, dict) and result.get("error"):
                ai._story_progress[progress_key] = {
                    "phase": "error", "percent": 0, "msg": result.get("reason", "Generation failed")}
                logger.warning("story  BG-ERROR deck=%d cat=%s: %s",
                               deck_id, category, result.get("reason"))
            else:
                ai._story_progress[progress_key] = {
                    "phase": "done", "percent": 100, "msg": "Story ready!"}
                logger.info("story  BG-DONE  deck=%d cat=%s", deck_id, category)
        except Exception as e:
            ai._story_progress[progress_key] = {"phase": "error", "percent": 0, "msg": str(e)}
            logger.warning("story  BG-ERROR deck=%d cat=%s: %s", deck_id, category, e)
        finally:
            with _gen_lock:
                _generating.discard(progress_key)

    threading.Thread(target=_run, daemon=True).start()


def _gen_params_dict(*, topic, max_hsk, model, grammar_focus, grammar_pct,
                     mode, chapter_ids, articles=None) -> dict:
    """Bundle the story generation settings persisted on each story row, so the
    Again regeneration can reproduce the same style (see generate_sentence_for_word).

    articles: news mode's pasted article list ({url, title, text}), stored so
    single-word "Again" regenerations reuse the same news context.
    """
    return {
        "mode": mode,
        "topic": topic,
        "max_hsk": max_hsk,
        "model": model,
        "grammar_focus": grammar_focus,
        "grammar_pct": grammar_pct,
        "chapter_ids": chapter_ids,
        "articles": articles,
    }


def _pick_kahneman_chapter(chapter_ids) -> dict | None:
    """Pick one chapter to regenerate a single Again sentence in kahneman mode.

    chapter_ids: the chapters the story was generated from (list/comma-string).
    Picks one at random among them; falls back to a random chapter from the book.
    """
    if not os.path.exists(KAHNEMAN_PATH):
        return None
    with open(KAHNEMAN_PATH, encoding="utf-8") as f:
        all_chapters = json.load(f).get("chapters", [])
    if not all_chapters:
        return None
    ids: list[int] = []
    if isinstance(chapter_ids, str):
        ids = [int(x) for x in chapter_ids.split(",") if x.strip()]
    elif isinstance(chapter_ids, (list, tuple)):
        ids = [int(x) for x in chapter_ids]
    if ids:
        num_map = {ch["number"]: ch for ch in all_chapters}
        pool = [num_map[i] for i in ids if i in num_map]
        if pool:
            return random.choice(pool)
    return random.choice(all_chapters)


def generate_sentence_for_word(card: dict, gen_params: dict | None) -> dict | None:
    """Regenerate ONE sentence for a single word, honoring the deck story's
    generation settings (mode/topic/grammar/model; a random chapter for kahneman).

    Used by the Again background regeneration so the new sentence matches the deck's
    style instead of always being a plain story sentence. Returns a tokenized
    sentence dict (ready for store_again_sentence) or None.
    """
    gp = gen_params or {}
    mode = gp.get("mode") or "story"
    model = _validated_model(gp.get("model"))
    try:
        if mode == "kahneman":
            chapter = _pick_kahneman_chapter(gp.get("chapter_ids"))
            if chapter is not None:
                sentences = ai.generate_kahneman_sentences([card], chapter, model=model)
            else:  # book data missing → fall back to a plain sentence
                sentences, _ = ai.generate_story([card], model=model)
        elif mode == "news":
            articles = gp.get("articles") or []
            if articles:
                sentences = ai.generate_news_sentences([card], articles, model=model)
            else:  # no articles context saved → fall back to a plain sentence
                sentences, _ = ai.generate_story([card], model=model)
        else:
            sentences, _ = ai.generate_story(
                [card], topic=gp.get("topic"), max_hsk=gp.get("max_hsk", 2),
                model=model, grammar_focus=gp.get("grammar_focus"),
                grammar_pct=gp.get("grammar_pct", 75), mode=mode)
    except Exception as e:
        logger.warning("again-regen  generation error for word=%s: %s", card.get("word_zh"), e)
        return None
    if not sentences:
        return None
    _add_tokens(sentences)
    return sentences[0]


@router.get("/api/story/{deck_id}/{category}")
def get_story(deck_id: int, category: str,
              topic: str | None = None, max_hsk: int = 3,
              model: str | None = None,
              grammar_focus: str | None = None, grammar_pct: int = 75,
              mode: str = "story",
              chapter_ids: str | None = None,
              no_generate: bool = False,
              background: bool = False):
    """Return today's story for (deck_id, category).

    background=False (default): generate synchronously and return the story
      (or an error dict). Used by regenerate flows and callers that block.
    background=True: never block — return the cached story if ready, else kick
      off generation in a daemon thread and return {"generating": True}. The
      frontend polls this endpoint (and /api/story-progress) until the story is
      ready, and can navigate away meanwhile. A failed background run is sticky
      (returns the error dict) so polling stops instead of restarting forever.
    """
    if database.is_sentences_deck(deck_id):
        return None

    today = database.anki_today().isoformat()
    progress_key = f"{deck_id}/{category}"

    # Always return today's cached story — custom params only apply when generating a new one
    story = database.get_active_story(today, category, deck_id)
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]))
        logger.info("story  CACHED  deck=%d cat=%s sentences=%d story_id=%d",
                    deck_id, category, len(story["sentences"]), story["id"])
        _log_story(story)
        if background:
            ai._story_progress.pop(progress_key, None)  # clear any terminal state
        return story

    # Caller only wants an already-generated story (e.g. "use existing" mode):
    # no cached story → return nothing instead of generating a new one.
    if no_generate:
        logger.info("story  NO-GEN (no_generate=1) deck=%d cat=%s — no cached story", deck_id, category)
        return None

    if DISABLE_AI:
        logger.info("story  DISABLED (DISABLE_AI=1) deck=%d cat=%s", deck_id, category)
        return None

    chosen_model = _validated_model(model)

    # News mode without pasted articles auto-fetches today's news inside
    # _generate_and_store (issue #387), so it flows through the normal
    # (possibly backgrounded) generation below like every other mode.

    # ── Background mode: don't block — start a thread and report progress ──────
    if background:
        # A finished-with-error background run is sticky so polling stops here
        # (the user can retry via regenerate, which overwrites the progress state).
        prog = ai._story_progress.get(progress_key)
        if prog and prog.get("phase") == "error":
            return {
                "error": True,
                "reason": prog.get("msg", "Generation failed"),
                "model": chosen_model,
                "has_history": database.has_story_history(deck_id, category),
            }
        with _gen_lock:
            if progress_key in _generating:
                return {"generating": True}
            cards = _get_cards_for_story(deck_id, category)
            if not cards:
                return None
            _generating.add(progress_key)
        logger.info("story  BG-QUEUE deck=%d cat=%s due_cards=%d model=%s mode=%s",
                    deck_id, category, len(cards), chosen_model, mode)
        _start_background_generation(
            deck_id, category, today, cards,
            topic=topic, max_hsk=max_hsk, model=chosen_model,
            grammar_focus=grammar_focus, grammar_pct=grammar_pct,
            mode=mode, chapter_ids=chapter_ids, progress_key=progress_key)
        return {"generating": True}

    # ── Synchronous mode (default): generate now and return the story ─────────
    cards = _get_cards_for_story(deck_id, category)
    logger.info("story  GENERATE deck=%d cat=%s due_cards=%d topic=%r max_hsk=%d model=%s mode=%s",
                deck_id, category, len(cards), topic, max_hsk, chosen_model, mode)
    if not cards:
        return None
    result = _generate_and_store(
        deck_id, category, today, cards,
        topic=topic, max_hsk=max_hsk, model=chosen_model,
        grammar_focus=grammar_focus, grammar_pct=grammar_pct,
        mode=mode, chapter_ids=chapter_ids, progress_key=progress_key)
    ai._story_progress.pop(progress_key, None)
    return result


@router.post("/api/story/{deck_id}/{category}/regenerate")
def regenerate_story(deck_id: int, category: str,
                     topic: str | None = None, max_hsk: int = 3,
                     model: str | None = None,
                     grammar_focus: str | None = None, grammar_pct: int = 75,
                     mode: str = "story",
                     chapter_ids: str | None = None,
                     body: dict | None = None):
    """Regenerate today's story. body (optional JSON): {"articles": [{"url", "title", "text"}]}
    — pasted articles for mode="news" (too long to fit in a query string).
    Empty in news mode → today's news is auto-fetched (issue #387)."""
    if database.is_sentences_deck(deck_id):
        return None
    if DISABLE_AI:
        return None
    chosen_model = _validated_model(model)
    today = database.anki_today().isoformat()
    progress_key = f"{deck_id}/{category}"
    articles = (body or {}).get("articles") or []
    cards = _get_cards_for_story(deck_id, category)
    logger.info("regen  deck=%d cat=%s due_cards=%d topic=%r max_hsk=%d model=%s mode=%s articles=%d",
                deck_id, category, len(cards), topic, max_hsk, chosen_model, mode, len(articles))
    if not cards:
        return None
    result = _generate_and_store(
        deck_id, category, today, cards,
        topic=topic, max_hsk=max_hsk, model=chosen_model,
        grammar_focus=grammar_focus, grammar_pct=grammar_pct,
        mode=mode, chapter_ids=chapter_ids, articles=articles, progress_key=progress_key)
    ai._story_progress.pop(progress_key, None)
    return result


@router.get("/api/story/{deck_id}/{category}/history")
def get_history_story(deck_id: int, category: str):
    """Return the most recent story for this deck+category, regardless of date."""
    story = database.get_latest_story(deck_id, category)
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]))
    return story


@router.get("/api/sentence-for-word/{word_id}")
def sentence_for_word(word_id: int):
    """Return the most recent stored sentence containing this word (with translations).

    Fallback for mixed/"All" review when a cross-day card's word is not in the
    currently loaded story, so the card still shows its own correct sentence.
    """
    sentence = database.get_latest_sentence_for_word(word_id)
    if sentence is None:
        return {"sentence": None}
    _add_tokens([sentence])
    return {"sentence": sentence}


@router.get("/api/kahneman/chapters")
def kahneman_chapters():
    """Return all chapters from kahneman_chapters.json (without examples to reduce payload)."""
    if not os.path.exists(KAHNEMAN_PATH):
        return {"chapters": [], "available": False}
    with open(KAHNEMAN_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chapters = [
        _attach_part({k: v for k, v in ch.items() if k != "examples_zh"})
        for ch in data.get("chapters", [])
    ]
    return {"chapters": chapters, "available": True}


@router.get("/api/kahneman/chapter/{number}")
def kahneman_chapter(number: int):
    """Return one full chapter (including examples_zh — the book's original quotes)."""
    ch = _load_kahneman_chapter(number)
    if ch is None:
        return {"chapter": None, "available": False}
    return {"chapter": _attach_part(ch), "available": True}


def _load_kahneman_chapter(chapter_id: int) -> dict | None:
    if not os.path.exists(KAHNEMAN_PATH):
        return None
    with open(KAHNEMAN_PATH, encoding="utf-8") as f:
        data = json.load(f)
    for ch in data.get("chapters", []):
        if ch.get("number") == chapter_id:
            return ch
    return None


def _generate_kahneman_story_sentences(
    cards: list, chapter_ids: list[int] | None,
    model: str, progress_key: str | None,
) -> tuple[list, str]:
    """Distribute cards across selected chapters, call AI once per chapter, merge results."""
    if not os.path.exists(KAHNEMAN_PATH):
        raise RuntimeError("data/kahneman_chapters.json not found. Run python extract_kahneman.py first.")

    with open(KAHNEMAN_PATH, encoding="utf-8") as f:
        all_chapters = json.load(f).get("chapters", [])

    if not all_chapters:
        raise RuntimeError("kahneman_chapters.json is empty.")

    if chapter_ids:
        num_map = {ch["number"]: ch for ch in all_chapters}
        selected = [num_map[cid] for cid in chapter_ids if cid in num_map]
    else:
        selected = random.sample(all_chapters, min(5, len(all_chapters)))

    if not selected:
        raise RuntimeError("No valid chapters selected.")

    n = len(selected)
    # Cap words per AI call: large batches make the model skip words and dilute
    # sentence quality. Extra batches cycle through the selected chapters.
    chunk_size = min(math.ceil(len(cards) / n), MAX_KAHNEMAN_BATCH)
    chunks = [cards[i:i + chunk_size] for i in range(0, len(cards), chunk_size)]

    all_sentences: list[dict] = []
    prompt_summary = f"kahneman mode — {n} chapters"
    for idx, chunk in enumerate(chunks):
        chapter = selected[idx % n]
        label = f" ({idx + 1}/{len(chunks)})"
        chapter_sentences = ai.generate_kahneman_sentences(
            chunk, chapter, model=model, progress_key=progress_key, attempt_label=label
        )
        all_sentences.extend(chapter_sentences)

    return all_sentences, prompt_summary


def _generate_news_story_sentences(
    cards: list, articles: list[dict], model: str, progress_key: str | None,
) -> tuple[list, str]:
    """Split cards into batches of ai.MAX_NEWS_BATCH, call AI once per batch, merge
    results. All batches share the same `articles` context so the sentences form
    one coherent briefing."""
    if not articles:
        raise RuntimeError(
            "News mode requires at least one pasted article. "
            "Please add articles via the setup modal.")

    chunk_size = ai.MAX_NEWS_BATCH
    chunks = [cards[i:i + chunk_size] for i in range(0, len(cards), chunk_size)]

    all_sentences: list[dict] = []
    prompt_summary = f"news mode — {len(articles)} articles"
    for idx, chunk in enumerate(chunks):
        label = f" ({idx + 1}/{len(chunks)})"
        chunk_sentences = ai.generate_news_sentences(
            chunk, articles, model=model, progress_key=progress_key, attempt_label=label
        )
        all_sentences.extend(chunk_sentences)

    return all_sentences, prompt_summary


def _auto_news_articles(model: str, progress_key: str | None) -> list[dict]:
    """News auto mode: fetch today's news (sources per data/news_sources.json,
    cached per day) and have the AI pick + condense the most important items
    into briefing source articles ({url, title, text} — the pasted-articles shape).

    news_fetcher.NewsFetchError propagates when every source fails, so the
    caller reports a clear error instead of silently using another mode."""
    ai._set_progress(progress_key, phase="request", msg="Fetching today's news…", percent=8)
    items = news_fetcher.fetch_all()
    logger.info("news auto: fetched %d items from sources", len(items))
    return ai.summarize_news_items(items, model=model, progress_key=progress_key)


@router.get("/api/news/status")
def news_status():
    """Whether today's news has already been fetched (news auto mode) and how
    many items the per-day cache holds — shown in the story-setup news panel."""
    count = news_fetcher.cached_today_count()
    return {"cached": count is not None, "count": count or 0}


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

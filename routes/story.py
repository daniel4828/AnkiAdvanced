import asyncio
import json
import logging
import math
import os
import random
import re
import threading

import jieba
import database
import ai
import news_fetcher
import tts
from fastapi import APIRouter
from fastapi.responses import FileResponse

from .utils import DISABLE_AI, leaf_ids, queue_mgr

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


def _add_tokens(sentences: list[dict], lang: str = "zh") -> list[dict]:
    """Ensure every sentence has tokens in [[text, word_id_or_null], ...] format.

    New stories already have AI-provided tokens stored in the DB.
    Old stories without tokens fall back to segmentation (word_id=null for all):
    jieba for zh, whitespace-preserving split for other languages (so the
    frontend can rejoin tokens back into the exact original string).
    """
    for s in sentences:
        if s.get("tokens"):
            continue
        zh = s.get("sentence_zh") or ""
        if not zh:
            s["tokens"] = []
        elif lang == "zh":
            s["tokens"] = [[tok, None] for tok in jieba.lcut(zh)]
        else:
            s["tokens"] = [[tok, None] for tok in re.findall(r"\s+|\S+", zh)]
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


def _get_cards_for_story(deck_id: int, category: str, lang: str | None = None) -> list:
    if category == "unified":
        cards = database.get_due_cards_unified(deck_id, lang=lang)
    else:
        ids = leaf_ids(deck_id, category, lang=lang)
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
                               grammar_focus, grammar_pct, mode, lang: str = "zh") -> tuple[list, str]:
    """Generate story sentences, splitting into chunks of CHUNK_SIZE when cards > CHUNK_SIZE."""
    if len(cards) <= CHUNK_SIZE:
        return ai.generate_story(cards, topic=topic, max_hsk=max_hsk, model=model,
                                 progress_key=progress_key, grammar_focus=grammar_focus,
                                 grammar_pct=grammar_pct, mode=mode, lang=lang)

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
            grammar_focus=grammar_focus, grammar_pct=grammar_pct, mode=mode, lang=lang,
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
                        chapter_ids, articles=None, progress_key, lang: str | None = None,
                        origin: str | None = None, episode_id: int | None = None) -> dict | None:
    """Generate a story for `cards`, persist it, and return the stored story
    (with sentences) — or an error dict on failure, or None if there is nothing
    to generate. Shared by the synchronous GET, the background thread, and regenerate.

    `lang`: resolved target language (query param, or database.get_deck_lang(deck_id)
    if the caller didn't resolve it already). Overrides get_deck_lang so the lang
    tab the user is on wins even for an aggregate deck spanning multiple languages.

    Sets ai._story_progress[progress_key] to a "starting" state up front; the
    generate functions update it during the run. Callers own terminal cleanup.
    """
    if not cards:
        return None
    lang = lang or database.get_deck_lang(deck_id)
    ai.fix_definition_commas(cards)
    ai._story_progress[progress_key] = {"phase": "starting", "msg": "Starting…", "percent": 5}
    try:
        if mode == "news":
            # Issue #512: the old auto-fetch-only "news" mode has been removed
            # from new-story generation — briefing (News flow) replaced it.
            # Old stories generated with mode='news' still display fine and
            # their per-word Again regeneration still works (see
            # generate_sentence_for_word below), only *new* full-story
            # generation with this mode is rejected.
            raise ValueError("mode 'news' has been removed — use 'briefing' instead")
        if lang != "zh" and mode in ("kahneman", "paste", "briefing", "podcast"):
            raise ValueError(f"mode '{mode}' is only available for Chinese decks")
        if mode == "kahneman":
            parsed_chapter_ids = [int(x) for x in chapter_ids.split(",") if x.strip()] if chapter_ids else []
            sentences, prompt_text = _generate_kahneman_story_sentences(
                cards, parsed_chapter_ids, model=model, progress_key=progress_key)
        elif mode in ("paste", "briefing", "podcast"):
            # Briefing (issue #444), paste (issue #481) and podcast (issue
            # #482) all share the briefing pipeline and ignore the frontend's
            # model selection — they always use BRIEFING_MODEL (env, default
            # gpt-5.1, verified/cached via ai.resolve_briefing_model()),
            # OpenAI only. Resolved before the article-selection call so
            # summarize_news_items uses the same model as the sentence
            # generation (issue #448).
            model = ai.resolve_briefing_model()
            logger.info("story  %s model in use: %s", mode, model)
            if mode == "briefing" and not articles:
                # Briefing always auto-fetches today's news and lets the AI
                # condense it into briefing source articles (issue #387).
                # Rebinding `articles` here also persists the fetched articles
                # in gen_params below, so Again-regeneration reuses them.
                # Paste mode never auto-fetches: no pasted content is an error
                # (raised inside _generate_briefing_story_sentences).
                # Articles already used by another category's story today are
                # excluded so each category covers different news (issue #473).
                used = database.get_today_used_article_urls(
                    today, lang, exclude_deck_id=deck_id, exclude_category=category)
                articles = _auto_news_articles(model=model, progress_key=progress_key,
                                               exclude_urls=used)
            if mode == "podcast" and not articles:
                # Podcast mode (issue #482): the single article is the selected
                # episode's Chinese transcript — no auto-fetch, no pasted text.
                # Truncated to a safe length (transcripts can be very long).
                if not episode_id:
                    raise ValueError("Podcast mode requires selecting an episode.")
                episode = database.get_episode(episode_id)
                if not episode:
                    raise ValueError(f"Podcast episode {episode_id} not found.")
                transcript = (episode.get("transcript_zh") or "").strip()
                if not transcript:
                    raise ValueError("Selected podcast episode has no transcript.")
                articles = [{
                    "url": episode.get("youtube_url"),
                    "title": episode.get("title"),
                    "text": transcript[:15000],
                }]
            # Paste (issue #481) and podcast (issue #482) reuse the briefing
            # pipeline (Python validation, dedup, fact-check) with generic=True
            # swapping the news-briefing prompt wording for plain content
            # framing. Podcast additionally sets include_context=False — every
            # sentence must carry a target word, no context sentences at all.
            # Neither mode auto-fetches, so missing articles surface as a
            # clear error inside _generate_briefing_story_sentences (podcast's
            # own no-episode error above fires first).
            sentences, prompt_text = _generate_briefing_story_sentences(
                cards, articles, model=model, progress_key=progress_key,
                max_hsk=max_hsk, generic=(mode in ("paste", "podcast")),
                include_context=(mode != "podcast"))
        else:
            sentences, prompt_text = _generate_story_sentences(
                cards, topic=topic, max_hsk=max_hsk, model=model,
                progress_key=progress_key, grammar_focus=grammar_focus,
                grammar_pct=grammar_pct, mode=mode, lang=lang)
        for i, s in enumerate(sentences):
            s["position"] = i
        gen_params = _gen_params_dict(
            topic=topic, max_hsk=max_hsk, model=model,
            grammar_focus=grammar_focus, grammar_pct=grammar_pct,
            mode=mode, chapter_ids=chapter_ids, articles=articles, lang=lang,
            origin=origin, episode_id=episode_id)
        database.create_story(today, category, deck_id, sentences, prompt_text, topic, gen_params, lang=lang)
        story = database.get_active_story(today, category, deck_id, lang=lang)
    except Exception as e:
        logger.error("story  generation error: %s", e)
        return {
            "error": True,
            "reason": str(e),
            "model": model,
            "has_history": database.has_story_history(deck_id, category),
        }
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]), lang=lang)
        logger.info("story  SAVED   deck=%d cat=%s sentences=%d",
                    deck_id, category, len(story["sentences"]))
        _log_story(story)
    return story


def _start_background_generation(deck_id: int, category: str, today: str, cards: list, *,
                                 topic, max_hsk, model, grammar_focus, grammar_pct,
                                 mode, chapter_ids, articles=None, progress_key,
                                 lang: str | None = None, episode_id: int | None = None) -> None:
    """Spawn a daemon thread that generates+stores a story, recording a terminal
    progress state (done/error) the frontend can poll. De-duped by progress_key."""
    def _run() -> None:
        logger.info("story  BG-START deck=%d cat=%s model=%s", deck_id, category, model)
        try:
            result = _generate_and_store(
                deck_id, category, today, cards,
                topic=topic, max_hsk=max_hsk, model=model,
                grammar_focus=grammar_focus, grammar_pct=grammar_pct,
                mode=mode, chapter_ids=chapter_ids, articles=articles, progress_key=progress_key,
                lang=lang, episode_id=episode_id)
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
                     mode, chapter_ids, articles=None, lang="zh",
                     origin=None, episode_id=None) -> dict:
    """Bundle the story generation settings persisted on each story row, so the
    Again regeneration can reproduce the same style (see generate_sentence_for_word).

    articles: news mode's pasted article list ({url, title, text}), stored so
    single-word "Again" regenerations reuse the same news context.
    origin: "pregen" when the story was created by the morning pregen —
    get_recent_story_keys skips those rows so pregen only ever reproduces
    user-initiated generations instead of feeding on its own output (issue #468).
    episode_id: podcast mode's selected episode (issue #482) — kept alongside
    articles purely for display/debugging; Again-regen reuses `articles`.
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
        "lang": lang,
        "origin": origin,
        "episode_id": episode_id,
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
    lang = gp.get("lang") or database.get_deck_lang(card["deck_id"])
    try:
        if mode == "kahneman":
            chapter = _pick_kahneman_chapter(gp.get("chapter_ids"))
            if chapter is not None:
                sentences = ai.generate_kahneman_sentences([card], chapter, model=model)
            else:  # book data missing → fall back to a plain sentence
                sentences, _ = ai.generate_story([card], model=model, lang=lang)
        elif mode in ("news", "paste", "briefing", "podcast"):
            # Briefing/paste/podcast Again-regen reuses the single-sentence news
            # path: one word gets one fresh sentence from the same articles (no
            # context chain). All three always use BRIEFING_MODEL (issue
            # #444/#481/#482), ignoring the story's stored model — same
            # resolution as the main generation (paste/podcast's stored
            # gen_params.model is already the resolved server model, but this
            # stays consistent regardless).
            news_model = ai.resolve_briefing_model() if mode in ("briefing", "paste", "podcast") else model
            articles = gp.get("articles") or []
            if articles:
                sentences = ai.generate_news_sentences(
                    [card], articles, model=news_model, generic=(mode in ("paste", "podcast")))
            else:  # no articles context saved → fall back to a plain sentence
                sentences, _ = ai.generate_story([card], model=news_model, lang=lang)
        else:
            sentences, _ = ai.generate_story(
                [card], topic=gp.get("topic"), max_hsk=gp.get("max_hsk", 2),
                model=model, grammar_focus=gp.get("grammar_focus"),
                grammar_pct=gp.get("grammar_pct", 75), mode=mode, lang=lang)
    except Exception as e:
        logger.warning("again-regen  generation error for word=%s: %s", card.get("word_zh"), e)
        return None
    if not sentences:
        return None
    _add_tokens(sentences, lang=lang)
    return sentences[0]


@router.get("/api/story/{deck_id}/{category}")
def get_story(deck_id: int, category: str,
              topic: str | None = None, max_hsk: int = 3,
              model: str | None = None,
              grammar_focus: str | None = None, grammar_pct: int = 75,
              mode: str = "story",
              chapter_ids: str | None = None,
              episode_id: int | None = None,
              no_generate: bool = False,
              background: bool = False,
              lang: str | None = None):
    """Return today's story for (deck_id, category).

    background=False (default): generate synchronously and return the story
      (or an error dict). Used by regenerate flows and callers that block.
    background=True: never block — return the cached story if ready, else kick
      off generation in a daemon thread and return {"generating": True}. The
      frontend polls this endpoint (and /api/story-progress) until the story is
      ready, and can navigate away meanwhile. A failed background run is sticky
      (returns the error dict) so polling stops instead of restarting forever.

    `lang` (optional query param): the active language tab. Resolution rule is
    `lang or database.get_deck_lang(deck_id)` everywhere — lets the frontend force
    a language on an aggregate deck (e.g. "All") that spans several languages.
    """
    today = database.anki_today().isoformat()
    lang = lang or database.get_deck_lang(deck_id)
    # progress_key includes lang: without it, a zh generation and a fr generation
    # started back-to-back for the same aggregate deck+category would collide in
    # the _generating set / ai._story_progress dict (only one runs at a time via
    # _generating, so the *other* language's request would report "generating"
    # for the wrong story, or see the wrong terminal state once it finishes).
    progress_key = f"{deck_id}/{category}/{lang}"

    # Always return today's cached story — custom params only apply when generating a new one
    story = database.get_active_story(today, category, deck_id, lang=lang)
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]), lang=lang)
        logger.info("story  CACHED  deck=%d cat=%s lang=%s sentences=%d story_id=%d",
                    deck_id, category, lang, len(story["sentences"]), story["id"])
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
            cards = _get_cards_for_story(deck_id, category, lang=lang)
            if not cards:
                return None
            _generating.add(progress_key)
        logger.info("story  BG-QUEUE deck=%d cat=%s lang=%s due_cards=%d model=%s mode=%s",
                    deck_id, category, lang, len(cards), chosen_model, mode)
        _start_background_generation(
            deck_id, category, today, cards,
            topic=topic, max_hsk=max_hsk, model=chosen_model,
            grammar_focus=grammar_focus, grammar_pct=grammar_pct,
            mode=mode, chapter_ids=chapter_ids, progress_key=progress_key, lang=lang,
            episode_id=episode_id)
        return {"generating": True}

    # ── Synchronous mode (default): generate now and return the story ─────────
    cards = _get_cards_for_story(deck_id, category, lang=lang)
    logger.info("story  GENERATE deck=%d cat=%s lang=%s due_cards=%d topic=%r max_hsk=%d model=%s mode=%s",
                deck_id, category, lang, len(cards), topic, max_hsk, chosen_model, mode)
    if not cards:
        return None
    result = _generate_and_store(
        deck_id, category, today, cards,
        topic=topic, max_hsk=max_hsk, model=chosen_model,
        grammar_focus=grammar_focus, grammar_pct=grammar_pct,
        mode=mode, chapter_ids=chapter_ids, progress_key=progress_key, lang=lang,
        episode_id=episode_id)
    ai._story_progress.pop(progress_key, None)
    return result


@router.post("/api/story/{deck_id}/{category}/regenerate")
def regenerate_story(deck_id: int, category: str,
                     topic: str | None = None, max_hsk: int = 3,
                     model: str | None = None,
                     grammar_focus: str | None = None, grammar_pct: int = 75,
                     mode: str = "story",
                     chapter_ids: str | None = None,
                     episode_id: int | None = None,
                     body: dict | None = None,
                     lang: str | None = None):
    """Regenerate today's story. body (optional JSON): {"articles": [{"url", "title", "text"}]}
    — pasted texts for mode="paste" (too long to fit in a query string).
    mode="briefing" ignores pasted articles and auto-fetches today's news (issue #387);
    mode="paste" with no articles is an error (issue #396); mode="podcast" builds
    its single article from the episode_id's transcript (issue #482). mode="news"
    (the old auto-fetch-only mode) has been removed (issue #512) and is rejected."""
    if DISABLE_AI:
        return None
    chosen_model = _validated_model(model)
    today = database.anki_today().isoformat()
    lang = lang or database.get_deck_lang(deck_id)
    progress_key = f"{deck_id}/{category}/{lang}"
    articles = (body or {}).get("articles") or []
    cards = _get_cards_for_story(deck_id, category, lang=lang)
    logger.info("regen  deck=%d cat=%s lang=%s due_cards=%d topic=%r max_hsk=%d model=%s mode=%s articles=%d",
                deck_id, category, lang, len(cards), topic, max_hsk, chosen_model, mode, len(articles))
    if not cards:
        return None
    result = _generate_and_store(
        deck_id, category, today, cards,
        topic=topic, max_hsk=max_hsk, model=chosen_model,
        grammar_focus=grammar_focus, grammar_pct=grammar_pct,
        mode=mode, chapter_ids=chapter_ids, articles=articles, progress_key=progress_key, lang=lang,
        episode_id=episode_id)
    ai._story_progress.pop(progress_key, None)
    if result:
        # A new story means word→position mapping changed — invalidate every
        # in-memory session queue so review order picks up the new sentence
        # order on next access (issue #454). Full invalidation is coarse but
        # cheap: queues are just rebuilt lazily from cheap DB queries.
        queue_mgr.invalidate()
    return result


@router.get("/api/story/{deck_id}/{category}/history")
def get_history_story(deck_id: int, category: str, lang: str | None = None):
    """Return the most recent story for this deck+category, regardless of date."""
    lang = lang or database.get_deck_lang(deck_id)
    story = database.get_latest_story(deck_id, category, lang=lang)
    if story:
        story["sentences"] = _add_tokens(database.get_story_sentences(story["id"]), lang=lang)
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
    # No deck_id in this endpoint's path — read lang directly off the entry row.
    word = database.get_word(word_id)
    lang = (word or {}).get("lang") or "zh"
    _add_tokens([sentence], lang=lang)
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


def _group_sentences_by_article(sentences: list[dict], articles: list[dict]) -> list[dict]:
    """Regroup chunked news-family sentences topic-by-topic (issue #456).

    Every batch of ai.MAX_NEWS_BATCH words is an independent AI call that
    cycles through ALL articles once, so the concatenated story revisits every
    topic once per batch (production story 1225: article 1 at positions 0, 10,
    20, …). The #454 monotonic-article rule only constrains a single call, not
    the concatenation — this stable sort fixes the whole story: sentences are
    keyed by their article's index in `articles`, so each article becomes one
    contiguous block while batch order is preserved within a block. Sentences
    without a source_url keep their relative order at the end. Batches are
    independent mini-summaries anyway, so regrouping breaks no narrative flow —
    and since the review queue follows sentence positions (#454), reviewing
    becomes topic-by-topic as well."""
    order = {a.get("url"): i for i, a in enumerate(articles) if a.get("url")}
    return sorted(sentences, key=lambda s: order.get(s.get("source_url"), len(articles)))


def _generate_briefing_story_sentences(
    cards: list, articles: list[dict], model: str, progress_key: str | None,
    max_hsk: int = 3, generic: bool = False, include_context: bool = True,
) -> tuple[list, str]:
    """News flow mode (issue #399): split cards into batches of ai.MAX_NEWS_BATCH,
    each batch produces its own flowing mini-summary (target-word sentences +
    context sentences attached as context_de). Card order = batch order, and
    within a batch = order of appearance in the summary.

    generic=True (issue #481): paste mode reusing this same pipeline — content-
    summary framing instead of news-briefing framing, everything else (context
    sentences, validation, dedup, fact-check) identical. Paste never auto-fetches,
    so the no-articles error below is the only guard against an empty run.

    include_context=False (issue #482): podcast mode — no context sentences,
    every sentence carries a target word."""
    if not articles:
        raise RuntimeError(
            "Paste mode requires at least one pasted text. "
            "Please add content via the setup modal."
            if generic else
            "News flow mode found no articles. "
            "Today's news fetch may have failed — please try again.")

    chunk_size = ai.MAX_NEWS_BATCH
    chunks = [cards[i:i + chunk_size] for i in range(0, len(cards), chunk_size)]

    # Detailed loading-screen progress (issue #407): overall word counter and the
    # news headlines being covered, carried through every progress update.
    titles = [a.get("title") or a.get("url") or "" for a in articles]
    titles = [t for t in titles if t][:10]

    all_sentences: list[dict] = []
    prompt_summary = f"{'paste' if generic else 'briefing'} mode — {len(articles)} articles"
    words_done = 0
    for idx, chunk in enumerate(chunks):
        label = f" ({idx + 1}/{len(chunks)})"
        chunk_sentences = ai.generate_briefing_sentences(
            chunk, articles, model=model, progress_key=progress_key, attempt_label=label,
            max_hsk=max_hsk, generic=generic, include_context=include_context,
            progress_extra={
                "words_done": words_done,
                "words_total": len(cards),
                "articles": titles,
            },
        )
        all_sentences.extend(chunk_sentences)
        words_done += len(chunk)

    return _group_sentences_by_article(all_sentences, articles), prompt_summary


def _auto_news_articles(model: str, progress_key: str | None,
                        exclude_urls: set | None = None) -> list[dict]:
    """News auto mode: fetch today's news (sources per data/news_sources.json,
    cached per day) and have the AI pick + condense the most important items
    into briefing source articles ({url, title, text} — the pasted-articles shape).

    exclude_urls: articles already used by another category's story today —
    removed from the pool so the two categories cover different news (issue
    #473). Skipped when filtering would leave an empty pool.

    news_fetcher.NewsFetchError propagates when every source fails, so the
    caller reports a clear error instead of silently using another mode."""
    ai._set_progress(progress_key, phase="request", msg="Fetching today's news…", percent=8)
    items = news_fetcher.fetch_all()
    logger.info("news auto: fetched %d items from sources", len(items))
    if exclude_urls:
        remaining = [i for i in items if i.get("url") not in exclude_urls]
        if remaining:
            logger.info("news auto: excluded %d already-used articles, %d remain",
                        len(items) - len(remaining), len(remaining))
            items = remaining
        else:
            logger.warning("news auto: exclusion would empty the pool — keeping all items")
    return ai.summarize_news_items(items, model=model, progress_key=progress_key)


@router.get("/api/news/status")
def news_status():
    """Whether today's news has already been fetched (news auto mode) and how
    many items the per-day cache holds — shown in the story-setup news panel."""
    count = news_fetcher.cached_today_count()
    return {"cached": count is not None, "count": count or 0}


@router.get("/api/story/{deck_id}/{category}/count")
def story_count(deck_id: int, category: str, lang: str | None = None):
    """Return sentence count, whether a cached story exists today, and token estimate."""
    today = database.anki_today().isoformat()
    lang = lang or database.get_deck_lang(deck_id)
    has_story = database.get_active_story(today, category, deck_id, lang=lang) is not None
    cards = _get_cards_for_story(deck_id, category, lang=lang)
    return {
        "count": len(cards),
        "has_story": has_story,
        "estimated_tokens": ai.estimate_story_tokens(len(cards)),
    }


@router.get("/api/tts-file")
async def tts_file(text: str, lang: str = "zh"):
    """Return the cached mp3 for text (generating it if needed). Used by the browser Audio API."""
    path = await tts.get_cached_path(text, lang=lang)
    return FileResponse(path, media_type="audio/mpeg")


# 以下四个端点（speak/speak-multi/speak-status/speak-stop）通过 afplay/say 在服务器端播放音频，
# 仅本地 macOS 使用，服务器部署不依赖此端点——前端已全部改为浏览器端播放（/api/tts-file + <audio>）。
@router.post("/api/speak")
def speak(text: str, lang: str = "zh"):
    try:
        tts.speak_sync(text, lang=lang)
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/speak-multi")
def speak_multi(body: dict):
    try:
        tts.speak_multi(body.get("texts", []), start_idx=body.get("start_idx", 0),
                        lang=body.get("lang", "zh"))
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
def preload(text: str, body: dict | None = None):
    lang = (body or {}).get("lang", "zh")
    try:
        tts.preload(text, lang=lang)
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/preload-session/{deck_id}/{category}")
async def preload_session(deck_id: int, category: str, quick: bool = False, lang: str | None = None):
    lang = lang or database.get_deck_lang(deck_id)
    progress_key = f"{deck_id}/{category}/{lang}"
    if quick:
        ids = leaf_ids(deck_id, category, lang=lang) or [deck_id]
        cards = database.get_due_cards_multi(ids, category) if len(ids) > 1 else database.get_due_cards(ids[0], category)
        texts = [c["word_zh"] for c in cards if c.get("word_zh")]
        logger.info("tts  quick preloading %d word audio files", len(texts))
        try:
            await tts.preload_all_async(texts, progress_key=progress_key, lang=lang)
        except Exception as e:
            logger.warning("tts  quick preload error: %s", e)
    else:
        today = database.anki_today().isoformat()
        story = database.get_active_story(today, category, deck_id, lang=lang)
        if story:
            sentences = _add_tokens(database.get_story_sentences(story["id"]), lang=lang)
            texts = [s["sentence_zh"] for s in sentences if s.get("sentence_zh")]
            logger.info("tts  preloading %d sentences", len(texts))
            try:
                await tts.preload_all_async(texts, progress_key=progress_key, lang=lang)
                logger.info("tts  preload done")
            except Exception as e:
                logger.warning("tts  preload error: %s", e)
    tts._preload_progress.pop(progress_key, None)
    return {"ok": True}


@router.get("/api/tts-progress/{deck_id}/{category}")
async def tts_progress(deck_id: int, category: str, lang: str | None = None):
    lang = lang or database.get_deck_lang(deck_id)
    key = f"{deck_id}/{category}/{lang}"
    return tts._preload_progress.get(key, {"done": 0, "total": 0})


@router.post("/api/pregen-today")
async def pregen_today():
    """Morning pregen (issue #458): instead of blindly generating a story for
    every leaf deck (the old behavior — 88 useless mode="story" stories, none
    of which matched what Daniel actually reviews), reproduce the story keys
    (deck_id, category, lang) that were really used on the most recent day with
    real stories (database.get_recent_story_keys), each with its own gen_params
    (mode/topic/grammar/…). `articles` is deliberately dropped so news/briefing
    modes re-fetch today's news instead of replaying stale content.

    For each key: skip if today's story is already cached, skip if there are no
    due cards today, otherwise generate synchronously and warm the TTS cache.
    A failure on one key is logged and does not stop the remaining keys.
    """
    today = database.anki_today().isoformat()
    keys = database.get_recent_story_keys(today)
    # Explicit pregen config (issue #473) takes priority: its gen_params come
    # straight from the settings, never from whatever was regenerated during
    # the day. Heuristic keys (reproduce the user's most recent generations)
    # still fill in anything not configured.
    config = database.get_pregen_config()
    cfg_keys = {(c["deck_id"], c["category"], c["lang"]) for c in config}
    keys = ([{"deck_id": c["deck_id"], "category": c["category"], "lang": c["lang"],
              "gen_params": {"mode": c["mode"], "max_hsk": c["max_hsk"]}}
             for c in config]
            + [k for k in keys
               if (k["deck_id"], k["category"], k["lang"]) not in cfg_keys])
    generated: list[str] = []
    skipped_cached: list[str] = []
    skipped_no_due: list[str] = []
    failed: list[dict] = []

    if DISABLE_AI:
        logger.info("pregen-today  DISABLED (DISABLE_AI=1), %d candidate keys skipped", len(keys))
        skipped_no_due = [f"{k['deck_id']}/{k['category']}/{k['lang']}" for k in keys]
        return {"date": today, "keys": len(keys), "generated": generated,
                "skipped_cached": skipped_cached, "skipped_no_due": skipped_no_due, "failed": failed}

    for k in keys:
        deck_id, category, lang = k["deck_id"], k["category"], k["lang"]
        label = f"{deck_id}/{category}/{lang}"

        if database.get_active_story(today, category, deck_id, lang=lang):
            logger.info("pregen-today  SKIP-CACHED  %s", label)
            skipped_cached.append(label)
            continue

        cards = _get_cards_for_story(deck_id, category, lang=lang)
        if not cards:
            logger.info("pregen-today  SKIP-NO-DUE  %s", label)
            skipped_no_due.append(label)
            continue

        gp = k["gen_params"] or {}
        mode = gp.get("mode", "story")
        progress_key = f"{deck_id}/{category}/{lang}"
        try:
            chosen_model = _validated_model(gp.get("model"))
            # _generate_and_store blocks for minutes (serial AI calls) — run it
            # off the event loop so the server stays responsive during pregen.
            result = await asyncio.to_thread(
                _generate_and_store,
                deck_id, category, today, cards,
                topic=gp.get("topic"), max_hsk=gp.get("max_hsk", 3), model=chosen_model,
                grammar_focus=gp.get("grammar_focus"), grammar_pct=gp.get("grammar_pct", 75),
                mode=mode, chapter_ids=gp.get("chapter_ids"), articles=None,
                progress_key=progress_key, lang=lang, origin="pregen")
            ai._story_progress.pop(progress_key, None)
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(result.get("reason", "generation failed"))
            n_sentences = len((result or {}).get("sentences", []))
            logger.info("pregen-today  OK  %s mode=%s sentences=%d", label, mode, n_sentences)
            generated.append(label)
        except Exception as e:
            logger.warning("pregen-today  FAIL  %s: %s", label, e)
            failed.append({"key": label, "error": str(e)})
            continue

        try:
            await preload_session(deck_id, category, quick=False, lang=lang)
            logger.info("pregen-today  TTS-DONE  %s", label)
        except Exception as e:
            # Story generation already succeeded — a TTS preload failure is only
            # logged, not counted as a failed key.
            logger.warning("pregen-today  TTS-FAIL  %s: %s", label, e)

    logger.info(
        "pregen-today  SUMMARY  date=%s keys=%d generated=%d skipped_cached=%d skipped_no_due=%d failed=%d",
        today, len(keys), len(generated), len(skipped_cached), len(skipped_no_due), len(failed))
    return {"date": today, "keys": len(keys), "generated": generated,
            "skipped_cached": skipped_cached, "skipped_no_due": skipped_no_due, "failed": failed}


_PREGEN_MODES = {"story", "qa", "expository", "kahneman", "briefing"}
_PREGEN_CATEGORIES = {"listening", "reading", "creating", "unified"}


@router.get("/api/pregen-config")
def get_pregen_config_endpoint():
    """Morning-pregen config rows (issue #473) — what the 06:00 pregen generates
    per (deck, category), independent of the day's ad-hoc regenerations."""
    return {"entries": database.get_pregen_config()}


@router.put("/api/pregen-config")
def put_pregen_config(body: dict):
    """Replace the config rows for one deck. body: {"deck_id": int,
    "entries": [{"category", "mode", "max_hsk", "lang"?}]} — an empty entries
    list clears the deck's config (every category back to heuristic pregen)."""
    deck_id = body.get("deck_id")
    entries = body.get("entries") or []
    if not deck_id or not database.get_deck(deck_id):
        return {"error": True, "reason": f"unknown deck_id {deck_id!r}"}
    for e in entries:
        if e.get("category") not in _PREGEN_CATEGORIES:
            return {"error": True, "reason": f"invalid category {e.get('category')!r}"}
        if e.get("mode") not in _PREGEN_MODES:
            return {"error": True, "reason": f"invalid mode {e.get('mode')!r}"}
    database.set_pregen_config(deck_id, entries)
    logger.info("pregen-config  deck=%s set to %s", deck_id,
                [(e["category"], e["mode"]) for e in entries])
    return {"ok": True, "entries": database.get_pregen_config()}


@router.get("/api/story-progress/{deck_id}/{category}")
def story_progress_endpoint(deck_id: int, category: str, lang: str | None = None):
    lang = lang or database.get_deck_lang(deck_id)
    key = f"{deck_id}/{category}/{lang}"
    return ai._story_progress.get(key, {"phase": "idle", "msg": "", "percent": 0})

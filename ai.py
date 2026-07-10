"""
AI story generation — supports Anthropic and OpenAI-compatible providers.

Supported model prefixes:
  claude-*      → Anthropic SDK (ANTHROPIC_API_KEY)
  glm-*         → Zhipu AI (ZHIPU_API_KEY)
  deepseek-*    → DeepSeek (DEEPSEEK_API_KEY)
  qwen-*        → Alibaba Qwen/DashScope (QWEN_API_KEY)
  gpt-*         → OpenAI (OPENAI_API_KEY) — used for news mode (DeepSeek censors news content)
"""

import json
import logging
import os
import re
import time

import anthropic
import openai

import database
import languages

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-v4-flash"

# briefing mode (issue #444) — env var BRIEFING_MODEL, default gpt-5.1, verified
# against the OpenAI models API with a fallback chain, cached for process lifetime.
BRIEFING_MODEL_FALLBACKS = ("gpt-5.1", "gpt-5", "gpt-5-mini")
_briefing_model_cache: str | None = None

# Per-session story generation progress: key → {phase, msg, percent, translate_warn?}
_story_progress: dict[str, dict] = {}


def _set_progress(key: str | None, **kwargs) -> None:
    if key:
        _story_progress[key] = kwargs


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------

def _openai_client(model: str) -> openai.OpenAI:
    if model.startswith("deepseek-"):
        return openai.OpenAI(
            base_url="https://api.deepseek.com/v1",
            api_key=os.environ["DEEPSEEK_API_KEY"],
        )
    elif model.startswith("glm-"):
        return openai.OpenAI(
            base_url="https://open.bigmodel.cn/api/paas/v4/",
            api_key=os.environ["ZHIPU_API_KEY"],
        )
    elif model.startswith("qwen-"):
        return openai.OpenAI(
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            api_key=os.environ["QWEN_API_KEY"],
        )
    elif model.startswith("gpt-"):
        return openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    raise ValueError(f"Unknown provider for model: {model}")


def _call_api(model: str, messages: list, max_tokens: int, purpose: str,
              thinking: bool = False) -> str:
    """Call the appropriate provider, log usage, and return the raw text response.

    thinking: enable DeepSeek thinking/reasoning mode (default False — disabled).
              deepseek-v4-flash defaults to thinking=on server-side, so we must
              explicitly disable it for tasks that don't need chain-of-thought.
    """
    t0 = time.time()
    if model.startswith("claude-"):
        client = anthropic.Anthropic()
        msg = client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
        elapsed = time.time() - t0
        logger.info("[%s] API call done in %.1fs — in=%d out=%d purpose=%s",
                    model, elapsed, msg.usage.input_tokens, msg.usage.output_tokens, purpose)
        database.log_api_call(
            model=msg.model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            purpose=purpose,
        )
        return msg.content[0].text.strip()
    else:
        client = _openai_client(model)
        extra: dict = {}
        if model.startswith("deepseek-"):
            extra["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
            logger.debug("[%s] thinking=%s", model, thinking)
        if model.startswith("gpt-"):
            # gpt-5 series (Chat Completions): max_completion_tokens replaces max_tokens
            # and is shared with internal reasoning tokens; custom temperature is not
            # supported. reasoning_effort="low" — sentence generation needs no deep
            # reasoning, and higher efforts can eat the whole token budget.
            resp = client.chat.completions.create(
                model=model, max_completion_tokens=max_tokens, messages=messages,
                reasoning_effort="low",
            )
        else:
            resp = client.chat.completions.create(
                model=model, max_tokens=max_tokens, messages=messages, **extra
            )
        elapsed = time.time() - t0
        choice = resp.choices[0]
        content = choice.message.content
        reasoning = getattr(choice.message, "reasoning_content", None)
        reasoning_chars = len(reasoning) if reasoning else 0

        logger.info("[%s] API call done in %.1fs — in=%d out=%d reasoning_chars=%d purpose=%s",
                    model, elapsed,
                    resp.usage.prompt_tokens, resp.usage.completion_tokens,
                    reasoning_chars, purpose)
        database.log_api_call(
            model=resp.model,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
            purpose=purpose,
        )

        if choice.finish_reason == "length":
            if reasoning_chars > 0 and not content:
                logger.warning(
                    "[%s] thinking mode exhausted max_tokens=%d (%d reasoning chars) "
                    "— no content produced. Pass thinking=False or increase max_tokens.",
                    model, max_tokens, reasoning_chars,
                )
            else:
                logger.warning("[%s] response truncated (finish_reason=length, max_tokens=%d, "
                               "content_chars=%d)", model, max_tokens, len(content or ""))

        if not content and not reasoning:
            logger.warning("[%s] empty response — no content and no reasoning (purpose=%s)",
                           model, purpose)

        return (content or "").strip()


def resolve_briefing_model() -> str:
    """Resolve the OpenAI model id used for briefing mode (issue #444).

    Reads BRIEFING_MODEL (default "gpt-5.1"), verifies on first use that the id
    actually exists via the OpenAI models API, and falls back through
    gpt-5.1 → gpt-5 → gpt-5-mini if not (or if the id is some other unlisted
    string). The resolved id is cached for the process lifetime — the models
    API is only ever hit once per process. OpenAI only (briefing is OpenAI-only,
    same reasoning as news/paste: DeepSeek censors news content).
    """
    global _briefing_model_cache
    if _briefing_model_cache is not None:
        return _briefing_model_cache

    requested = os.environ.get("BRIEFING_MODEL") or BRIEFING_MODEL_FALLBACKS[0]
    candidates = [requested] + [m for m in BRIEFING_MODEL_FALLBACKS if m != requested]

    try:
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        available = {m.id for m in client.models.list().data}
        for candidate in candidates:
            if candidate in available:
                _briefing_model_cache = candidate
                logger.info("briefing model resolved: %s (requested=%s, available=%d models)",
                           candidate, requested, len(available))
                return candidate
        logger.warning("briefing model: none of %s found via OpenAI models API — using last "
                       "resort %s", candidates, BRIEFING_MODEL_FALLBACKS[-1])
        _briefing_model_cache = BRIEFING_MODEL_FALLBACKS[-1]
        return _briefing_model_cache
    except Exception as e:
        logger.warning("briefing model: could not verify via OpenAI models API (%s) — "
                       "falling back to %s", e, BRIEFING_MODEL_FALLBACKS[-1])
        _briefing_model_cache = BRIEFING_MODEL_FALLBACKS[-1]
        return _briefing_model_cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_story(cards: list[dict], topic: str | None = None, max_hsk: int = 2,
                   model: str = DEFAULT_MODEL,
                   progress_key: str | None = None,
                   grammar_focus: str | None = None,
                   grammar_pct: int = 75,
                   mode: str = "story",
                   lang: str = "zh") -> tuple[list[dict], str]:
    """
    Generate sentences (in `lang`) covering all target vocab words.

    cards:         list of dicts with keys word_id, word_zh, pinyin, definition, pos
    topic:         optional theme/question/topic to guide the content
    max_hsk:       maximum HSK level for non-target background vocabulary (1-6, zh only)
    model:         model ID to use for generation
    grammar_focus: optional grammar pattern to encourage (e.g. "把字句", zh only)
    grammar_pct:   approximate percentage of sentences that should use the grammar (0-100)
    mode:          "story" | "qa" | "expository"
    lang:          "zh" | "fr" — determines prompt language, level system, and matching rules
    Returns: (sentences, prompt_text)
      sentences: list of {word_ids: [int, ...], sentence_zh, sentence_en, sentence_de, sentence_fr}.
                 Multiple cards may share one sentence. Each card's word_id appears in exactly one sentence.
      prompt_text: the full prompt string sent to the AI.
    Returns ([], "") immediately if cards is empty (no API call made).
    """
    if not cards:
        return [], ""

    word_id_set = {c["word_id"] for c in cards}

    def _is_sentence(word_zh: str) -> bool:
        if lang != "zh" and word_zh.endswith('.'):
            return True
        return word_zh.endswith(('。', '！', '？', '!', '?'))

    # French articles to strip from the target word before matching it inside a
    # generated sentence — the AI may adapt/drop the article to fit the sentence.
    _FR_ARTICLE_PREFIXES = ("le ", "la ", "les ", "un ", "une ", "des ", "du ", "de la ", "de l'", "l'")

    def _word_in_sentence(word_zh: str, sentence_zh: str) -> bool:
        if lang != "zh":
            w = word_zh.casefold()
            s = sentence_zh.casefold()
            for prefix in _FR_ARTICLE_PREFIXES:
                if w.startswith(prefix):
                    w = w[len(prefix):]
                    break
            # Word-boundary match so short words don't match inside longer ones
            # (e.g. "art" inside "partir"); tolerate a plural suffix.
            return re.search(rf"(?<!\w){re.escape(w)}(?:s|es)?(?!\w)", s) is not None
        if '...' in word_zh or '…' in word_zh:
            chars = [c for c in word_zh if c not in '.…']
            return all(c in sentence_zh for c in chars)
        return word_zh in sentence_zh

    word_list_lines = []
    for i, c in enumerate(cards):
        if _is_sentence(c['word_zh']):
            word_list_lines.append(f"{i + 1}. [SENTENCE] {c['word_zh']}")
        else:
            word_list_lines.append(f"{i + 1}. {c['word_zh']}")
    word_list = "\n".join(word_list_lines)

    if grammar_focus:
        n_sentences = max(1, round(len(cards) * grammar_pct / 100))
        grammar_first = (
            f"GRAMMAR FOCUS: Use the pattern 「{grammar_focus}」 in roughly "
            f"{n_sentences} of the sentences (about {grammar_pct}%).\n\n"
        )
    else:
        grammar_first = ""

    if lang == "zh":
        # zh prompt kept EXACTLY as before the multi-language pipeline — no template
        # sharing with non-zh so this path can never drift when other languages change.
        if mode == "qa":
            task_line = f"Answer the following question in Mandarin Chinese, one sentence at a time, to help an HSK 4-5 learner review vocabulary.\nQuestion: {topic or 'Describe something interesting.'}"
            style_rule = "- The sentences together should form a coherent, informative answer to the question above\n- Do NOT use fictional characters or narrative story structure"
        elif mode == "expository":
            task_line = f"Write a short informative text in Mandarin Chinese about the following topic, to help an HSK 4-5 learner review vocabulary.\nTopic: {topic or 'an interesting subject'}"
            style_rule = "- The sentences together should form a coherent, factual explanation of the topic above\n- Do NOT use fictional characters or narrative story structure"
        else:
            task_line = "Write a short Mandarin Chinese story to help an HSK 4-5 learner review vocabulary."
            topic_clause = f"- The story should be set around this topic or theme: {topic}\n" if topic else ""
            style_rule = f"{topic_clause}- The sentences must form a coherent narrative with the same recurring characters"

        prompt = f"""{task_line}

{grammar_first}Target words (each must appear verbatim in at least one sentence):
{word_list}

Rules:
- Each target word MUST appear verbatim in at least one sentence
- Write the sentences in the same order as the target word list above
- For items marked [SENTENCE]: use that exact text as the sentence, unchanged
- Use proper Chinese punctuation — include commas（，）where natural pauses occur
- Use only HSK 1-{max_hsk} vocabulary for non-target words; each sentence must contain exactly ONE target word from the list — do not use other target words from the list in that sentence
- Keep each sentence short and simple
{style_rule}
- NEVER highlight, quote, or mark target words in any way — no "quotes", no 「brackets」, no （parentheses）, no bold, no underline; write them as plain text embedded naturally in the sentence
- NEVER use markdown formatting (**bold**, _italic_, etc.) anywhere in the output — write plain text only

Return ONLY a numbered list of Chinese sentences, no explanation:
1. ...
2. ..."""
    else:
        cfg = languages.get_lang_config(lang)
        lang_name = cfg["name_en"]
        learner = cfg["learner_level"]

        if mode == "qa":
            task_line = f"Answer the following question in {lang_name}, one sentence at a time, to help a {learner} learner review vocabulary.\nQuestion: {topic or 'Describe something interesting.'}"
            style_rule = "- The sentences together should form a coherent, informative answer to the question above\n- Do NOT use fictional characters or narrative story structure"
        elif mode == "expository":
            task_line = f"Write a short informative text in {lang_name} about the following topic, to help a {learner} learner review vocabulary.\nTopic: {topic or 'an interesting subject'}"
            style_rule = "- The sentences together should form a coherent, factual explanation of the topic above\n- Do NOT use fictional characters or narrative story structure"
        else:
            task_line = f"Write a short {lang_name} story to help a {learner} learner review vocabulary."
            topic_clause = f"- The story should be set around this topic or theme: {topic}\n" if topic else ""
            style_rule = f"{topic_clause}- The sentences must form a coherent narrative with the same recurring characters"

        prompt = f"""{task_line}

{grammar_first}Target words (each must appear verbatim in at least one sentence):
{word_list}

Rules:
- Each target word MUST appear verbatim in at least one sentence
- Write the sentences in the same order as the target word list above
- For items marked [SENTENCE]: use that exact text as the sentence, unchanged
- Use natural {lang_name} punctuation
- Use only simple {cfg["background_vocab"]} level vocabulary for non-target words; each sentence must contain exactly ONE target word from the list — do not use other target words from the list in that sentence
- Keep each sentence short and simple (max {cfg["sentence_limit"]})
{style_rule}
- Each target word must appear in exactly the given form; you may adapt or drop its leading article (le/la/les/un/une) to fit the sentence
- NEVER highlight, quote, or mark target words in any way — no "quotes", no 「brackets」, no （parentheses）, no bold, no underline; write them as plain text embedded naturally in the sentence
- NEVER use markdown formatting (**bold**, _italic_, etc.) anywhere in the output — write plain text only

Return ONLY a numbered list of {lang_name} sentences, no explanation:
1. ...
2. ..."""

    max_tokens = 8192

    logger.info("[%s] generate_story: %d 张卡片 mode=%s", model, len(cards), mode)
    logger.debug("Prompt:\n%s", prompt)

    card_by_id = {c["word_id"]: c for c in cards}
    t_start = time.time()
    missing_hint = ""
    last_partial: tuple | None = None  # (sentences, missing_word_ids)
    for attempt in range(3):
        retry_label = f" (retry {attempt}/{2})" if attempt > 0 else ""
        _set_progress(progress_key, phase="request", attempt=attempt + 1,
                      msg=f"Sending request to AI…{retry_label}", percent=max(5, 10 - attempt * 4))
        raw = _call_api(model, [{"role": "user", "content": prompt + missing_hint}], max_tokens,
                        purpose="story")

        logger.debug("Raw response attempt=%d (%d chars):\n%s", attempt + 1, len(raw), raw)

        # Parse numbered list: extract lines like "1. 句子"
        sentences_zh = []
        for line in raw.splitlines():
            m = re.match(r'^\d+\.\s+(.+)', line.strip())
            if m:
                sentences_zh.append(m.group(1).strip())

        if not sentences_zh:
            if not raw:
                logger.error("generate_story: attempt %d — empty response from API "
                             "(model=%s, max_tokens=%d). "
                             "If this is a reasoning model, it may have exhausted its token budget on thinking.",
                             attempt + 1, model, max_tokens)
            else:
                logger.error("generate_story: attempt %d — no numbered sentences found "
                             "(response was %d chars):\n%.500s…",
                             attempt + 1, len(raw), raw)
            continue

        # Match target words to sentences by string search
        seen_ids: set[int] = set()
        parsed = []
        for s_zh in sentences_zh:
            word_ids = []
            for card in cards:
                wid = card["word_id"]
                if wid not in seen_ids and _word_in_sentence(card["word_zh"], s_zh):
                    word_ids.append(wid)
                    seen_ids.add(wid)
                    break  # one target word per sentence
            parsed.append({"word_ids": word_ids, "sentence_zh": s_zh, "tokens": []})

        missing_ids = [wid for wid in word_id_set if wid not in seen_ids]
        if missing_ids:
            missing_words = [card_by_id[wid]["word_zh"] for wid in missing_ids if wid in card_by_id]
            logger.warning("generate_story: attempt %d — words missing: %s", attempt + 1, missing_words)
            _set_progress(progress_key, phase="warning", attempt=attempt + 1,
                          msg=f"⚠ Attempt {attempt + 1}: missing {missing_words} — retrying",
                          percent=0)
            missing_ratio = len(missing_ids) / len(cards)
            last_partial = (parsed, missing_ids)
            if missing_ratio < 0.05:
                _patch_missing(parsed, missing_ids, card_by_id, lang=lang)
                _set_progress(progress_key, phase="translating",
                              msg="Translating sentences…", percent=88)
                _fill_translations(parsed, progress_key=progress_key, lang=lang)
                _set_progress(progress_key, phase="ai_done",
                              msg=f"✓ {len(parsed)} sentences (attempt {attempt + 1})", percent=93)
                return parsed, prompt
            missing_hint = (
                f"\n\nIMPORTANT: Your previous attempt was missing these words "
                f"— each MUST appear verbatim in a sentence: {', '.join(missing_words)}"
            )
            continue

        logger.info("generate_story: success — %d sentences covering %d words (attempt %d) in %.1fs",
                    len(parsed), len(cards), attempt + 1, time.time() - t_start)
        _set_progress(progress_key, phase="translating",
                      msg="Translating sentences…", percent=88)
        _fill_translations(parsed, progress_key=progress_key, lang=lang)
        logger.info("generate_story: DONE — %.1fs total", time.time() - t_start)
        _set_progress(progress_key, phase="ai_done",
                      msg=f"✓ {len(parsed)} sentences (attempt {attempt + 1})", percent=93)
        return parsed, prompt

    if last_partial is not None:
        parsed, missing_ids = last_partial
        if len(missing_ids) / len(cards) < 0.03:
            _patch_missing(parsed, missing_ids, card_by_id, lang=lang)
            _set_progress(progress_key, phase="translating",
                          msg="Translating sentences…", percent=88)
            _fill_translations(parsed, progress_key=progress_key, lang=lang)
            _set_progress(progress_key, phase="ai_done",
                          msg=f"✓ {len(parsed)} sentences (patched)", percent=93)
            return parsed, prompt

    missing_count = len(last_partial[1]) if last_partial else len(cards)
    raise RuntimeError(
        f"Story generation failed after 3 attempts "
        f"({missing_count} word(s) still missing from the story). "
        "Please try again or switch to a different model."
    )


def _patch_missing(sentences: list[dict], missing_word_ids: list[int],
                   card_by_id: dict[int, dict], lang: str = "zh") -> None:
    """Append fallback sentences for words the AI failed to include."""
    for wid in missing_word_ids:
        card = card_by_id.get(wid)
        if not card:
            continue
        if lang == "zh":
            fallback_zh = card.get("source_sentence") or f"我学了{card['word_zh']}这个词。"
        else:
            fallback_zh = card.get("source_sentence") or f"J'ai appris le mot {card['word_zh']}."
        sentences.append({"word_ids": [wid], "sentence_zh": fallback_zh,
                          "sentence_en": "", "sentence_de": "", "sentence_fr": ""})


def regenerate_entry_fields(
    word: dict,
    characters: list[dict],
    fields: list[str],
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Regenerate specified fields for a vocabulary entry using SKILL.md format.

    fields: subset of ["notes", "examples", "etymology", "compounds"]
    Returns a dict with a subset of:
      notes:      str  (German prose)
      examples:   list[{zh, pinyin, english, de}]
      characters: list[{char, etymology?, compounds?}]
    """
    if not fields:
        return {}

    note_type = word.get("note_type", "vocabulary")
    word_zh   = word.get("word_zh", "")
    trad      = word.get("traditional", "")
    pinyin_   = word.get("pinyin", "")
    eng       = word.get("definition", "")
    de        = word.get("definition_de", "")
    hsk       = word.get("hsk_level", "")
    register  = word.get("register", "")

    want_notes    = "notes" in fields
    want_examples = "examples" in fields
    want_etym     = "etymology" in fields
    want_comp     = "compounds" in fields
    want_meanings = "other_meanings" in fields
    want_chars    = want_etym or want_comp or want_meanings
    want_def      = any(f in fields for f in ("definition", "definition_zh", "definition_de", "definition_fr", "pos"))

    # --- Entry header ---
    trad_line = f" / traditional: {trad}" if trad and trad != word_zh else ""
    entry_block = (
        f"type: {note_type}\n"
        f"simplified: {word_zh}{trad_line}\n"
        f"pinyin: {pinyin_}\n"
        f"english: {eng}\n"
        f"german: {de}\n"
        f"hsk: {hsk}\n"
        f"register: {register}"
    )

    # --- Character list (only when needed) ---
    char_block = ""
    if want_chars and characters:
        lines = [
            f"  - {c['char']} (pinyin: {c.get('pinyin', '')}, HSK {c.get('hsk_level', '?')})"
            for c in characters
        ]
        char_block = "\nCharacters:\n" + "\n".join(lines)

    # --- Per-field instructions ---
    sections: list[str] = []

    if want_def:
        def_lines = ["Generate concise one-line definitions. Keep each under 10 words."]
        if "pos" in fields:
            def_lines.append('- pos: part of speech abbreviation (e.g. "v.", "n.", "adj.", "adv.", "expr.", "pron.", "conj.")')
        if "definition" in fields:
            def_lines.append('- definition: English definition (e.g. "to study; to learn")')
        if "definition_zh" in fields:
            def_lines.append('- definition_zh: Chinese definition (e.g. "学习；研究")')
        if "definition_de" in fields:
            def_lines.append('- definition_de: German definition (e.g. "studieren; lernen")')
        if "definition_fr" in fields:
            def_lines.append('- definition_fr: French definition (e.g. "étudier; apprendre")')
        sections.append("DEFINITION FIELDS:\n" + "\n".join(def_lines))

    if want_notes:
        if note_type == "sentence":
            sections.append(
                "NOTES: Write a German explanation for this sentence (2-3 paragraphs).\n"
                "Include: breakdown of key vocabulary + grammar; list key components as "
                "「- 词语 (pinyin) — meaning」; explain grammar structures used."
            )
        else:
            sections.append(
                "NOTES: Write German usage notes (2-4 paragraphs). Include:\n"
                "- Opening sentence on what the word means and how it's used\n"
                "- **Häufige Ausdrücke:** with 3-5 collocations (「- 词语 (pinyin) — German meaning」)\n"
                "- **Wichtiger Unterschied:** comparing with a similar word (if applicable)\n"
                "- **Kulturelle Anmerkung:** (if relevant)"
            )

    if want_examples:
        sections.append(
            "EXAMPLES: Generate 3-4 example sentences. Each must use the target word verbatim.\n"
            "Each example: {\"zh\": \"<sentence>\", \"pinyin\": \"<full pinyin>\", "
            "\"english\": \"<translation>\", \"de\": \"<German translation>\"}"
        )

    if want_chars:
        n_chars = len(characters)
        char_field_lines = []
        if want_meanings:
            char_field_lines.append(
                "  - other_meanings: array of 2-4 short German strings giving the core meaning(s) of this single character "
                "(e.g. [\"tragen\", \"mitnehmen\", \"begleiten\"]). REQUIRED — do not omit."
            )
        if want_etym:
            char_field_lines.append(
                "  - etymology: 2-4 sentences German PROSE (NO bullet points) on components, "
                "historical origin, meaning evolution"
            )
        if want_comp:
            char_field_lines.append(
                "  - compounds: 3-5 common compound words using this character. "
                "Each: {\"simplified\": \"词\", \"pinyin\": \"...\", \"meaning\": \"German (NO colons)\"}"
            )
        sections.append(
            f"CHARACTER DATA: Return EXACTLY {n_chars} object(s) in the \"characters\" array — "
            f"one per character listed above. Do NOT skip any character.\n"
            + "\n".join(char_field_lines)
        )

    # --- JSON template ---
    json_keys = []
    if "pos" in fields:
        json_keys.append('  "pos": "v."')
    if "definition" in fields:
        json_keys.append('  "definition": "<English>"')
    if "definition_zh" in fields:
        json_keys.append('  "definition_zh": "<中文>"')
    if "definition_de" in fields:
        json_keys.append('  "definition_de": "<Deutsch>"')
    if "definition_fr" in fields:
        json_keys.append('  "definition_fr": "<français>"')
    if want_notes:
        json_keys.append('  "notes": "<German prose>"')
    if want_examples:
        json_keys.append('  "examples": [{"zh": "...", "pinyin": "...", "english": "...", "de": "..."}]')
    if want_chars:
        char_obj_keys = '"char": "X"'
        if want_meanings:
            char_obj_keys += ', "other_meanings": ["...", "..."]'
        if want_etym:
            char_obj_keys += ', "etymology": "..."'
        if want_comp:
            char_obj_keys += ', "compounds": [{"simplified": "...", "pinyin": "...", "meaning": "..."}]'
        # Show one example object per actual character so the AI knows the expected array length
        char_example = "{" + char_obj_keys + "}"
        char_array = ", ".join([char_example] * max(len(characters), 1))
        json_keys.append(f'  "characters": [{char_array}]')

    json_template = "{\n" + ",\n".join(json_keys) + "\n}"

    prompt = (
        f"You are a Chinese dictionary expert generating SRS flashcard content.\n\n"
        f"Entry:\n{entry_block}{char_block}\n\n"
        f"Generate ONLY the fields listed. All German text must be in German.\n\n"
        + "\n\n".join(sections)
        + f"\n\nReturn ONLY valid JSON with exactly these top-level keys:\n{json_template}"
    )

    logger.info("[%s] regenerate_entry_fields: %s fields=%s", model, word_zh, fields)
    raw = _call_api(model, [{"role": "user", "content": prompt}], max_tokens=2400,
                    purpose=f"regen:{word_zh}")

    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.error("regenerate_entry_fields: JSON parse error for %s: %s", word_zh, e)
        return {}


def generate_character_info(char: str, pinyin: str, model: str = DEFAULT_MODEL) -> dict:
    """
    Generate etymology and translation for a single Chinese character.
    Returns: {etymology: str, translation: str}
    """
    prompt = f"""For the Chinese character {char} (pinyin: {pinyin}), provide:
1. An etymological description: explain the character's components, historical origin, and meaning evolution (2-4 sentences)
2. A concise English translation: 2-5 words covering the core meaning

Return ONLY valid JSON, no explanation, no markdown:
{{"etymology": "<etymological description>", "translation": "<concise English meaning>"}}"""

    raw = _call_api(model, [{"role": "user", "content": prompt}], max_tokens=400,
                    purpose=f"hanzi:{char}")

    json_match = re.search(r'\{.*?\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        result = json.loads(raw)
        return {
            "etymology": result.get("etymology", ""),
            "translation": result.get("translation", ""),
        }
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.error("generate_character_info: JSON parse error: %s", e)
        return {"etymology": "", "translation": ""}


_ENRICH_MODEL = "deepseek-v4-flash"


def enrich_word(word: dict, characters: list[dict], model: str = DEFAULT_MODEL) -> dict:
    """
    Determine HSK level for a word and fill missing character data (etymology, other_meanings).
    Always uses DeepSeek — the model parameter is ignored.
    Only requests data for fields that are currently empty.
    Returns: {hsk_level: int|None, characters: [{char, etymology, other_meanings}]}
    """
    # Identify which characters need which fields
    chars_needing_data = []
    for c in characters:
        needs = []
        if not c.get("etymology"):
            needs.append("etymology")
        if not c.get("other_meanings"):
            needs.append("other_meanings (array of short English meanings)")
        if needs:
            chars_needing_data.append(
                f'  - {c["char"]} (pinyin: {c.get("pinyin", "")}) → needs: {", ".join(needs)}'
            )

    char_section = ""
    if chars_needing_data:
        char_section = (
            "\n\nFor each character below, provide only the requested fields:\n"
            + "\n".join(chars_needing_data)
            + '\n\nReturn these under "characters" as an array of objects with keys: '
            '"char", "etymology" (2–4 sentences on origin & components), '
            '"other_meanings" (array of 2–5 short English strings).'
        )
    else:
        char_section = '\n\nNo character data needed — return "characters": [].'

    prompt = f"""You are a Chinese language expert. For the word {word["word_zh"]} \
({word.get("pinyin", "")}) — {word.get("definition", "")}:

1. What is its HSK level (1–6)? Return null if it is not in the standard HSK list.{char_section}

Return ONLY valid JSON, no explanation, no markdown:
{{
  "hsk_level": <integer 1-6 or null>,
  "characters": [
    {{"char": "<char>", "etymology": "<text>", "other_meanings": ["<m1>", "<m2>"]}}
  ]
}}"""

    raw = _call_api(_ENRICH_MODEL, [{"role": "user", "content": prompt}], max_tokens=800,
                    purpose=f"enrich:{word['word_zh']}")

    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        result = json.loads(raw)
        return {
            "hsk_level": result.get("hsk_level"),
            "characters": result.get("characters", []),
        }
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.error("enrich_word: JSON parse error: %s", e)
        return {"hsk_level": None, "characters": []}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fill_translations(sentences: list[dict], progress_key: str | None = None,
                       lang: str = "zh") -> None:
    """Translate sentence_zh → sentence_de in-place using Google Translate."""
    try:
        import translator as _t
        source = languages.get_lang_config(lang)["translator_source"]
        texts = [s.get("sentence_zh", "") for s in sentences]
        total = len(texts)

        if progress_key and total > 0:
            _set_progress(progress_key, phase="translating",
                          msg=f"Translating… 0/{total}", percent=88)

        t0 = time.time()
        de_results = _t.translate_batch(texts, target="de", source=source)
        logger.info("translate DE done in %.1fs (%d sentences)", time.time() - t0, total)

        if progress_key and total > 0:
            _set_progress(progress_key, phase="translating",
                          msg=f"Translating… {total}/{total}", percent=92)

        for s, de in zip(sentences, de_results):
            s["sentence_de"] = de
            s.setdefault("sentence_en", "")
            s.setdefault("sentence_fr", "")
    except Exception as e:
        err = str(e)
        vpn_hint = " (VPN issue?)" if any(k in err.lower() for k in ("eof", "connect", "timeout", "proxy", "ssl")) else ""
        logger.warning("_fill_translations: fallback to empty — %s%s", e, vpn_hint)
        if progress_key and progress_key in _story_progress:
            _story_progress[progress_key]["translate_warn"] = f"⚠ Translation failed{vpn_hint}"
        for s in sentences:
            s.setdefault("sentence_en", "")
            s.setdefault("sentence_de", "")
            s.setdefault("sentence_fr", "")


def _fallback_sentences(cards: list[dict], lang: str = "zh") -> list[dict]:
    """Minimal sentences used when the AI response cannot be parsed."""
    fallback = (lambda w: f"我学了{w}这个词。") if lang == "zh" else (lambda w: f"J'ai appris le mot {w}.")
    result = [
        {
            "word_ids": [c["word_id"]],
            "sentence_zh": fallback(c["word_zh"]),
            "sentence_en": "",
            "sentence_de": "",
            "sentence_fr": "",
        }
        for c in cards
    ]
    _fill_translations(result, lang=lang)
    return result


_FIX_BATCH = 25  # words per DeepSeek call


def _needs_comma_fix(text: str | None) -> bool:
    if not text or len(text) < 15:
        return False
    return "," not in text and ";" not in text and "/" not in text


def fix_definition_commas(cards: list[dict]) -> int:
    """Add missing commas to English/German definitions of today's due words.

    Sends batches to DeepSeek, updates entries in-place and in the DB.
    Returns the number of entries actually updated.
    """
    to_fix = [
        {"id": c["word_id"], "word_zh": c["word_zh"],
         "en": c.get("definition"), "de": c.get("definition_de")}
        for c in cards
        if _needs_comma_fix(c.get("definition")) or _needs_comma_fix(c.get("definition_de"))
    ]
    # deduplicate by word_id
    seen: set[int] = set()
    unique: list[dict] = []
    for item in to_fix:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)

    if not unique:
        return 0

    logger.info("fix_commas  %d entries need comma repair", len(unique))
    total_fixed = 0

    for i in range(0, len(unique), _FIX_BATCH):
        batch = unique[i:i + _FIX_BATCH]
        word_lines = "\n".join(
            f'{item["word_zh"]} | EN: {item["en"] or ""} | DE: {item["de"] or ""}'
            for item in batch
        )
        prompt = (
            "The following Chinese vocabulary definitions are missing commas between "
            "their separate meanings. Add commas (or slashes where a slash is the natural "
            "separator) to make the meanings clearly distinct. Do not add or remove meanings, "
            "only insert the missing punctuation.\n\n"
            "Return ONLY a JSON array. Each element: "
            '{"word_zh": "...", "en": "fixed English or null", "de": "fixed German or null"}\n\n'
            f"Words:\n{word_lines}"
        )
        try:
            raw = _call_api("deepseek-v4-flash",
                            [{"role": "user", "content": prompt}],
                            max_tokens=2000, purpose="fix_commas")
            # extract JSON array from response
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            if not m:
                logger.warning("fix_commas  no JSON array in response")
                continue
            updates = json.loads(m.group())
            id_map = {item["word_zh"]: item["id"] for item in batch}
            for upd in updates:
                wid = id_map.get(upd.get("word_zh"))
                if not wid:
                    continue
                fields: dict = {}
                if upd.get("en"):
                    fields["definition"] = upd["en"]
                if upd.get("de"):
                    fields["definition_de"] = upd["de"]
                if fields:
                    database.update_word(wid, fields)
                    # also patch the in-memory card dicts so the story prompt sees new values
                    for c in cards:
                        if c.get("word_id") == wid:
                            c.update(fields)
                    total_fixed += 1
        except Exception as e:
            logger.warning("fix_commas  batch error: %s", e)

    logger.info("fix_commas  updated %d entries", total_fixed)
    return total_fixed


def generate_kahneman_sentences(
    cards: list[dict],
    chapter: dict,
    model: str = DEFAULT_MODEL,
    progress_key: str | None = None,
    attempt_label: str = "",
) -> list[dict]:
    """Generate sentences in the style of Kahneman's "Speaking of..." chapter endings.

    Each sentence uses one vocabulary word and implicitly reveals the cognitive bias
    described in `chapter`. Returns list of sentence dicts with concept fields attached.

    cards:   vocab cards assigned to this chapter (word_id, word_zh, pinyin, definition)
    chapter: {number, title_zh, title_en, concept_zh, concept_en, examples_zh}
    """
    if not cards:
        return []

    def _word_in_sentence(word_zh: str, sentence_zh: str) -> bool:
        if '...' in word_zh or '…' in word_zh:
            chars = [c for c in word_zh if c not in '.…']
            return all(c in sentence_zh for c in chars)
        return word_zh in sentence_zh

    examples_block = "\n".join(f"  {ex}" for ex in chapter.get("examples_zh", []))
    concept_label = f"第{chapter['number']}章《{chapter['title_zh']}》：{chapter['concept_zh']}"
    concept_en = f"Chapter {chapter['number']}: {chapter['title_en']}"
    concept_zh = f"第{chapter['number']}章：{chapter['title_zh']}"
    summary_zh = (chapter.get("summary_zh") or "").strip()
    summary_block = f"\n本章机制与典型情境：\n{summary_zh}\n" if summary_zh else ""

    def _build_prompt(batch: list[dict]) -> str:
        word_list = "\n".join(
            f"{i + 1}. {c['word_zh']}（{c.get('pinyin', '')}）— {c.get('definition', '')}"
            for i, c in enumerate(batch)
        )
        return f"""任务：模仿《思考，快与慢》每章末尾"示例"部分的风格，写若干中文句子帮助HSK 4-5学习者复习词汇。
每个句子应该是某人在日常情境中说的一句话，自然地透露出一种认知偏误或心理定势，而不直接点明偏误名称。

本章概念：{concept_label}
{summary_block}
风格范例（模仿这种语气和结构）：
{examples_block}

目标词汇（每句话必须恰好包含其中一个，原文出现）：
{word_list}

写作步骤（对每个词汇按此顺序思考）：
1. 先在 reasoning_zh 里确定：要展示本章偏误的哪个具体情境（谁、在什么场合、犯了什么思维错误）
2. 再写 sentence_zh：把这个情境浓缩成某人说的一句话，并自然地包含目标词汇

规则：
- 每句话恰好包含一个目标词汇，词汇必须以原文形式出现
- 每个目标词汇都必须有自己的句子，一个都不能漏
- 用自然口语风格，隐性透露本章所描述的认知偏误
- 句子里不要直接提及偏误名称或心理学术语
- 句子要简短（不超过28个字）
- 不要使用markdown格式

reasoning_zh 的规则：
- 用中文写，1-2句话，简明扼要，说明这句话为什么体现了本章的认知偏误
- 可以点明偏误名称，帮助学习者理解
- 面向HSK 4-5学习者，用词不要太难

仅返回如下JSON数组，不加任何其他文字（reasoning_zh 在前，sentence_zh 在后）：
[
  {{"reasoning_zh": "解释内容", "sentence_zh": "句子内容"}},
  {{"reasoning_zh": "解释内容", "sentence_zh": "句子内容"}}
]"""

    _set_progress(progress_key, phase="request", msg=f"生成第{chapter['number']}章句子…{attempt_label}", percent=20)

    sentences: list[dict] = []
    remaining = list(cards)

    # Incremental retries: keep good sentences, re-request only the words the
    # model skipped — resending the full list just reproduces the same gaps.
    for attempt in range(3):
        if not remaining:
            break
        prompt = _build_prompt(remaining)
        raw = _call_api(model, [{"role": "user", "content": prompt}], 4096, purpose="kahneman")

        json_start = raw.find("[")
        json_end = raw.rfind("]") + 1
        if json_start == -1 or json_end == 0:
            logger.warning("kahneman attempt %d: no JSON array found", attempt + 1)
            continue

        try:
            items = json.loads(raw[json_start:json_end])
        except json.JSONDecodeError as e:
            logger.warning("kahneman attempt %d: JSON parse error: %s", attempt + 1, e)
            continue

        for item in items:
            s_zh = item.get("sentence_zh", "").strip()
            if not s_zh:
                continue
            matched = None
            for card in remaining:
                if _word_in_sentence(card["word_zh"], s_zh):
                    matched = card
                    break
            if matched is None:
                # Sentence contains none of the still-missing words; keeping it
                # would create an orphan once the word is re-requested.
                continue
            remaining.remove(matched)
            sentences.append({
                "word_ids": [matched["word_id"]],
                "sentence_zh": s_zh,
                "sentence_en": "",
                "concept_en": concept_en,
                "concept_zh": concept_zh,
                "reasoning_zh": item.get("reasoning_zh", "").strip(),
                "tokens": [],
            })

        if remaining:
            logger.warning(
                "kahneman attempt %d: missing words (will re-request): %s",
                attempt + 1, [c["word_zh"] for c in remaining],
            )

    # Per-word fallback so every card ends up with a sentence.
    for card in remaining:
        logger.warning("kahneman: using fallback sentence for %s", card["word_zh"])
        sentences.append({
            "word_ids": [card["word_id"]],
            "sentence_zh": card.get("source_sentence") or f"我学了{card['word_zh']}这个词。",
            "sentence_en": "",
            "concept_en": concept_en,
            "concept_zh": concept_zh,
            "reasoning_zh": "",
            "tokens": [],
        })

    _fill_translations(sentences, progress_key=progress_key)
    return sentences


# Max words per AI call for news mode — mirrors MAX_KAHNEMAN_BATCH (routes/story.py):
# large batches make the model skip words and dilute sentence quality.
MAX_NEWS_BATCH = 10


def generate_news_sentences(
    cards: list[dict],
    articles: list[dict],
    model: str = "gpt-5-mini",
    max_hsk: int = 2,
    progress_key: str | None = None,
    attempt_label: str = "",
    generic: bool = False,
) -> list[dict]:
    """Generate a Chinese summary sentence per target word, summarizing `articles`.

    Sentences all together form a coherent briefing/summary. Each sentence uses
    exactly one target word (in HSK-limited background vocabulary otherwise) and is
    tagged with which article it refers to, a one-line Chinese headline, and a short
    Chinese background explanation — stored as concept_zh/reasoning_zh/source_url.

    cards:    vocab cards to cover (word_id, word_zh, pinyin, definition)
    articles: [{url, title, text}, ...] — pasted texts (url/title optional)
    generic:  False = news-briefing framing (mode="news"); True = plain content
              summary of arbitrary pasted texts (mode="paste")
    """
    if not cards or not articles:
        return []

    def _word_in_sentence(word_zh: str, sentence_zh: str) -> bool:
        if '...' in word_zh or '…' in word_zh:
            chars = [c for c in word_zh if c not in '.…']
            return all(c in sentence_zh for c in chars)
        return word_zh in sentence_zh

    # generic=True swaps the news-briefing framing for a plain content summary
    # (pasted content can be an email, blog post, book excerpt — not just news).
    noun = "内容" if generic else "文章"
    goal = "对这些内容的连贯中文摘要" if generic else "对这些文章的连贯新闻简报"
    block_header = "内容（按 0 开始编号）" if generic else "新闻文章（按 0 开始编号）"
    coherence_rule = (
        "- 所有句子合起来必须构成一篇连贯的中文摘要，覆盖内容的关键信息" if generic
        else "- 所有句子合起来必须像一段连贯的新闻简报，覆盖文章的关键信息")
    headline_rule = (
        "- headline_zh 是该段内容主题的中文一句话标题" if generic
        else "- headline_zh 是该文章对应新闻事件的中文一句话标题")
    background_rule = (
        "- background_zh 是2-3句中文背景说明，帮助学习者理解这部分内容" if generic
        else "- background_zh 是2-3句中文背景说明，帮助学习者理解这条新闻的来龙去脉")

    articles_block = "\n\n".join(
        f"{noun}{i}（标题：{a.get('title') or '（无标题）'}）：\n{a.get('text', '').strip()}"
        for i, a in enumerate(articles)
    )

    def _build_prompt(batch: list[dict]) -> str:
        word_list = "\n".join(
            f"{i + 1}. {c['word_zh']}（{c.get('pinyin', '')}）— {c.get('definition', '')}"
            for i, c in enumerate(batch)
        )
        return f"""任务：根据下面提供的{noun}，写一组中文句子，合起来构成{goal}，
帮助HSK 4-5学习者复习词汇。

{block_header}：
{articles_block}

目标词汇（每句话必须恰好包含其中一个，原文出现）：
{word_list}

规则：
- 每句话恰好包含一个目标词汇，词汇必须以原文形式出现
- 每个目标词汇都必须有自己的句子，一个都不能漏
{coherence_rule}
- 非目标词汇只使用HSK 1-{max_hsk}的词汇，尽量简单
- 句子要简短（不超过15个字）
- 所有输出（句子、标题、背景说明）只用简体中文，绝对不要出现繁体字
- 不要使用markdown格式
- article_idx 是该句子所总结/涉及的{noun}编号（上面的 0 开始编号）
{headline_rule}
{background_rule}

仅返回如下JSON数组，不加任何其他文字：
[
  {{"sentence_zh": "句子内容", "article_idx": 0, "headline_zh": "标题", "background_zh": "背景说明"}},
  {{"sentence_zh": "句子内容", "article_idx": 0, "headline_zh": "标题", "background_zh": "背景说明"}}
]"""

    _set_progress(progress_key, phase="request",
                  msg=f"{'生成内容摘要句子' if generic else '生成新闻简报句子'}…{attempt_label}", percent=20)

    sentences: list[dict] = []
    remaining = list(cards)

    for attempt in range(3):
        if not remaining:
            break
        prompt = _build_prompt(remaining)
        # 8192: gpt-5 series shares this budget with internal reasoning tokens,
        # so leave generous headroom above the ~2-3k tokens of actual output.
        raw = _call_api(model, [{"role": "user", "content": prompt}], 8192,
                        purpose="paste" if generic else "news")

        json_start = raw.find("[")
        json_end = raw.rfind("]") + 1
        if json_start == -1 or json_end == 0:
            logger.warning("news attempt %d: no JSON array found", attempt + 1)
            continue

        try:
            items = json.loads(raw[json_start:json_end])
        except json.JSONDecodeError as e:
            logger.warning("news attempt %d: JSON parse error: %s", attempt + 1, e)
            continue

        for item in items:
            s_zh = item.get("sentence_zh", "").strip()
            if not s_zh:
                continue
            matched = None
            for card in remaining:
                if _word_in_sentence(card["word_zh"], s_zh):
                    matched = card
                    break
            if matched is None:
                continue
            remaining.remove(matched)
            article_idx = item.get("article_idx")
            source_url = source_title = source_name = None
            if isinstance(article_idx, int) and 0 <= article_idx < len(articles):
                _art = articles[article_idx]
                source_url = _art.get("url") or None
                source_title = _art.get("title") or None
                source_name = _art.get("source_name") or None
            sentences.append({
                "word_ids": [matched["word_id"]],
                "sentence_zh": s_zh,
                "sentence_en": "",
                "concept_en": "",
                "concept_zh": item.get("headline_zh", "").strip(),
                "reasoning_zh": item.get("background_zh", "").strip(),
                "source_url": source_url,
                "source_title": source_title,
                "source_name": source_name,
                "tokens": [],
            })

        if remaining:
            logger.warning(
                "news attempt %d: missing words (will re-request): %s",
                attempt + 1, [c["word_zh"] for c in remaining],
            )

    # Per-word fallback so every card ends up with a sentence.
    for card in remaining:
        logger.warning("news: using fallback sentence for %s", card["word_zh"])
        sentences.append({
            "word_ids": [card["word_id"]],
            "sentence_zh": card.get("source_sentence") or f"我学了{card['word_zh']}这个词。",
            "sentence_en": "",
            "concept_en": "",
            "concept_zh": "",
            "reasoning_zh": "",
            "source_url": None,
            "tokens": [],
        })

    _fill_translations(sentences, progress_key=progress_key)
    return sentences


def _briefing_word_match(word_zh: str, sentence_zh: str) -> bool:
    if '...' in word_zh or '…' in word_zh:
        chars = [c for c in word_zh if c not in '.…']
        return all(c in sentence_zh for c in chars)
    return word_zh in sentence_zh


def validate_briefing_items(items: list[dict], cards: list[dict]) -> list[str]:
    """Python-only validation (no AI) of a raw briefing sentence array (issue #444).

    Checks:
      a) every target word appears exactly once across all sentences
      b) no two consecutive context sentences (sentences with no target word)
      c) target-word sentences are at most 18 characters
    Returns a list of human-readable violation descriptions — empty means valid.
    """
    issues: list[str] = []
    if not cards:
        return issues

    word_counts = {c["word_id"]: 0 for c in cards}
    is_context: list[bool] = []
    for item in items:
        s_zh = (item.get("sentence_zh") or "").strip()
        matched_any = False
        for c in cards:
            if _briefing_word_match(c["word_zh"], s_zh):
                word_counts[c["word_id"]] += 1
                matched_any = True
        is_context.append(not matched_any)

    missing = [c["word_zh"] for c in cards if word_counts[c["word_id"]] == 0]
    duplicated = [c["word_zh"] for c in cards if word_counts[c["word_id"]] > 1]
    if missing:
        issues.append(f"目标词缺失：{'、'.join(missing)}")
    if duplicated:
        issues.append(f"目标词重复出现（每个词必须恰好出现一次）：{'、'.join(duplicated)}")

    run = 0
    for ctx in is_context:
        run = run + 1 if ctx else 0
        if run >= 2:
            issues.append("存在连续两个以上不含目标词的上下文句子（每个目标句前最多只能有一个上下文句）")
            break

    if len(items) > 2 * len(cards):
        issues.append(f"句子总数（{len(items)}）超过目标词数量两倍的上限（{2 * len(cards)}）")

    # article_idx must be non-decreasing across the sequence (issue #454) —
    # the AI is told to process articles one at a time, in order. None/missing
    # article_idx is not a violation (just skipped when tracking the max seen).
    max_idx_seen = None
    for pos, item in enumerate(items):
        idx = item.get("article_idx")
        if not isinstance(idx, int):
            continue
        if max_idx_seen is not None and idx < max_idx_seen:
            issues.append(
                f"文章顺序回跳：句子{pos}属于文章{idx}但之前已进入文章{max_idx_seen}"
            )
            break
        max_idx_seen = idx if max_idx_seen is None else max(max_idx_seen, idx)

    for item in items:
        s_zh = (item.get("sentence_zh") or "").strip()
        if s_zh and any(_briefing_word_match(c["word_zh"], s_zh) for c in cards) and len(s_zh) > 18:
            issues.append(f"目标句超过18字（{len(s_zh)}字）：{s_zh}")

    return issues


def _dedupe_consecutive_briefing_context(items: list[dict], cards: list[dict]) -> list[dict]:
    """Fallback repair when consecutive context-only sentences survive the
    validation retry: keep only the LAST sentence of each consecutive
    context-only run, dropping the extras (issue #444 acceptance criteria)."""
    fixed: list[dict] = []
    buf: list[dict] = []
    for item in items:
        s_zh = (item.get("sentence_zh") or "").strip()
        is_ctx = not (s_zh and any(_briefing_word_match(c["word_zh"], s_zh) for c in cards))
        if is_ctx:
            buf.append(item)
        else:
            if buf:
                fixed.append(buf[-1])
                buf = []
            fixed.append(item)
    if buf:
        fixed.append(buf[-1])
    return fixed


def fact_check_briefing(articles: list[dict], items: list[dict], model: str) -> list[str]:
    """One extra AI call (issue #454) to catch hallucinated facts in the
    generated briefing sentences — numbers, names, causality invented rather
    than taken from the source articles.

    Returns a list of Chinese issue descriptions ("句子N：问题描述"); an empty
    list means either everything checked out or the check itself failed —
    fact-checking is best-effort and must never block story generation.
    """
    if not articles or not items:
        return []

    articles_block = "\n\n".join(
        f"文章{i}（标题：{a.get('title') or '（无标题）'}）：\n{a.get('text', '').strip()}"
        for i, a in enumerate(articles)
    )
    sentences_block = "\n".join(
        f"{i}. {item.get('sentence_zh', '')}" for i, item in enumerate(items)
    )
    prompt = f"""任务：核对下面每一句中文摘要句子是否符合原始新闻文章的事实。

新闻文章（按 0 开始编号）：
{articles_block}

生成的摘要句子（按 0 开始编号）：
{sentences_block}

请重点检查：数字（金额、人数、日期等）、人名/地名/机构名、因果关系是否准确，
以及是否有原文中完全没有提到、凭空捏造的内容。

只返回如下 JSON，不加任何其他文字：
- 全部符合事实：{{"ok": true}}
- 存在问题：{{"ok": false, "issues": ["句子N：问题描述", ...]}}"""

    try:
        raw = _call_api(model, [{"role": "user", "content": prompt}], 2048, purpose="briefing_fact_check")
        json_start = raw.find("{")
        json_end = raw.rfind("}") + 1
        if json_start == -1 or json_end == 0:
            logger.warning("briefing fact-check: no JSON object found in response")
            return []
        result = json.loads(raw[json_start:json_end])
        if result.get("ok"):
            return []
        issues = result.get("issues") or []
        return [str(i) for i in issues]
    except Exception as e:
        logger.warning("briefing fact-check: call failed (%s) — skipping", e)
        return []


def generate_briefing_sentences(
    cards: list[dict],
    articles: list[dict],
    model: str = "gpt-5-mini",
    # HSK 1-5 background vocabulary (issue #448): Daniel is HSK 4-5 — capping
    # the non-target words at HSK 1-2 made sentences childish and was the
    # tightest remaining constraint after the #444 rework.
    max_hsk: int = 3,
    progress_key: str | None = None,
    attempt_label: str = "",
    progress_extra: dict | None = None,
) -> list[dict]:
    """News flow mode (issue #399, reworked in #444): one flowing Chinese news
    summary instead of one forced sentence per word.

    The AI writes a coherent summary in which each target word appears exactly
    once — in whatever order produces the most natural summary (word order is
    free, issue #444) — but plain context sentences (facts, numbers — no target
    word) are allowed in between, so nothing has to be padded artificially. At
    most ONE context sentence may precede a target sentence; two consecutive
    context sentences are never allowed. We then scan the sentences in order:
    a sentence containing a target word becomes a card sentence; the context
    sentence before it (since the previous card) is attached to it — Chinese
    into reasoning_zh (background popup), German (Google Translate, no extra AI
    cost) into context_de (shown on the card). Target-word order = order of
    appearance in the summary (arbitrary, chosen by the AI).

    After generation, `validate_briefing_items` checks the raw output in Python
    (no AI): every target word exactly once, no consecutive context sentences,
    target sentences ≤18 chars. On violation we retry ONCE with the concrete
    issues fed back into the prompt. If violations persist: consecutive context
    runs are collapsed to their last sentence (extras dropped); anything else
    is accepted with a logged warning — the existing per-word missing-word
    retry loop and fallback-sentence mechanism below still guarantee every
    card gets a sentence.

    progress_extra: extra fields merged into every progress update (issue #407) —
    the chunker passes words_done/words_total/articles so the loading screen can
    show real overall progress; words_done is advanced here as words get covered.
    """
    if not cards or not articles:
        return []

    extra = dict(progress_extra or {})
    base_done = extra.get("words_done", 0)
    words_total = extra.get("words_total")

    def _progress(msg: str) -> None:
        if not progress_key:
            return
        fields = dict(extra)
        if words_total:
            done = base_done + (len(cards) - len(remaining))
            fields["words_done"] = done
            fields["percent"] = 15 + int(70 * done / max(words_total, 1))
        else:
            fields.setdefault("percent", 20)
        _set_progress(progress_key, phase="request", msg=msg, **fields)

    articles_block = "\n\n".join(
        f"文章{i}（标题：{a.get('title') or '（无标题）'}）：\n{a.get('text', '').strip()}"
        for i, a in enumerate(articles)
    )

    def _build_prompt(batch: list[dict], extra_hint: str = "") -> str:
        word_list = "\n".join(
            f"{i + 1}. {c['word_zh']}（{c.get('pinyin', '')}）— {c.get('definition', '')}"
            for i, c in enumerate(batch)
        )
        return f"""任务：根据下面的新闻文章，写一篇连贯的中文新闻摘要（像新闻串播一样，从一条新闻自然过渡到下一条），
帮助HSK 4-5学习者复习词汇。

新闻文章（按 0 开始编号）：
{articles_block}

目标词汇（每个词必须在整篇摘要中恰好出现一次，以原文形式出现）：
{word_list}

【词序自由】你可以任意安排这些目标词在摘要中出现的先后顺序——不必按上面列表的顺序，
请选择能写出最自然、最连贯摘要的顺序。

规则：
- 摘要按句子输出为 JSON 数组，数组顺序就是阅读顺序
- 不是每句话都要包含目标词汇：目标词句子之间【最多插入一个】不含目标词的上下文句子，
  用来交代事实、数字和背景，让摘要自然连贯——绝对不允许连续出现两个或以上不含目标词的上下文句子
- 因此句子总数不能超过目标词数量的两倍
- 一句话最多包含一个目标词汇
- 【难度控制，严格遵守】含目标词汇的句子长度为8到18个字，其中除目标词外只允许
  HSK 1-{max_hsk} 的词汇——这是学习者自己选择的难度上限，超纲词会让句子无法学习。
  如果某个事实需要更难的词才能表达，把它放进上下文句子里，目标句只保留简单的部分
- 【重要】含目标词汇的句子也必须传达该新闻中的一个具体事实（谁、做了什么、在哪里、多少），
  读者只看这一句也能学到新闻内容。严禁没有信息量的空洞句子，
  例如"组织很大。""火箭很快。""它指代。""未知很大。"这类句子绝对不可以出现
- 上下文句子【不受 HSK 词汇限制】——它最终会被翻译成德文显示在卡片正面，可以自由
  使用专有名词、数字和任何词汇来准确传达事实；长度保持一两句话的合理范围即可
- 所有输出只用简体中文，绝对不要出现繁体字
- 不要使用markdown格式
- article_idx 是该句子所涉及的文章编号（上面的 0 开始编号）
- target_word 是该句包含的目标词汇原文；不含目标词的上下文句子填 null
- 【逐篇处理】必须按文章编号顺序依次处理：先写完文章0涉及的所有句子（含其上下文句），
  再开始写文章1的句子，以此类推——article_idx 在整个输出中只能递增或不变，绝不允许
  写到文章1之后又跳回去写文章0的句子
{extra_hint}

仅返回如下JSON数组，不加任何其他文字：
[
  {{"sentence_zh": "上下文句子", "target_word": null, "article_idx": 0}},
  {{"sentence_zh": "含目标词的句子", "target_word": "词汇", "article_idx": 0}}
]"""

    sentences: list[dict] = []
    remaining = list(cards)
    validation_retried = False
    fact_check_done = False

    _progress(f"生成新闻总结…{attempt_label}")

    for attempt in range(3):
        if not remaining:
            break
        if attempt > 0:
            _progress(f"补漏 {len(remaining)} 个词（第{attempt + 1}轮）…{attempt_label}")
        expected_cards = list(remaining)
        prompt = _build_prompt(remaining)
        # 8192: gpt-5 series shares this budget with internal reasoning tokens,
        # and context sentences add output on top of the card sentences.
        raw = _call_api(model, [{"role": "user", "content": prompt}], 8192, purpose="briefing")

        json_start = raw.find("[")
        json_end = raw.rfind("]") + 1
        if json_start == -1 or json_end == 0:
            logger.warning("briefing attempt %d: no JSON array found", attempt + 1)
            continue

        try:
            items = json.loads(raw[json_start:json_end])
        except json.JSONDecodeError as e:
            logger.warning("briefing attempt %d: JSON parse error: %s", attempt + 1, e)
            continue

        # Python-only validation + a single retry (issue #444) — only once per
        # call, on whichever attempt first produces parseable JSON.
        if not validation_retried:
            validation_retried = True
            issues = validate_briefing_items(items, expected_cards)
            if issues:
                logger.warning("briefing attempt %d: validation issues, retrying once: %s",
                               attempt + 1, issues)
                hint = "\n【上一次的结果有以下问题，请修正后重新生成整篇摘要】\n" + \
                       "\n".join(f"- {i}" for i in issues)
                retry_raw = _call_api(
                    model, [{"role": "user", "content": _build_prompt(remaining, extra_hint=hint)}],
                    8192, purpose="briefing",
                )
                r_start, r_end = retry_raw.find("["), retry_raw.rfind("]") + 1
                if r_start != -1 and r_end != 0:
                    try:
                        retry_items = json.loads(retry_raw[r_start:r_end])
                        items = retry_items
                        remaining_issues = validate_briefing_items(items, expected_cards)
                        if remaining_issues:
                            logger.warning(
                                "briefing: validation issues persist after retry (accepting with "
                                "fallback repair): %s", remaining_issues)
                    except json.JSONDecodeError as e:
                        logger.warning("briefing: validation retry JSON parse error (%s) — "
                                       "keeping original attempt", e)
                else:
                    logger.warning("briefing: validation retry produced no JSON array — "
                                   "keeping original attempt")
                # Fallback repair: collapse any remaining consecutive context runs
                # to their last sentence — safe no-op if already valid.
                items = _dedupe_consecutive_briefing_context(items, expected_cards)

        # AI fact-check against the source articles (issue #454) — runs once,
        # right after Python validation and before any translation. On issues,
        # retry generation once with the concrete problems fed back in; the
        # retry's own fact-check result (if any) is logged only, never retried
        # again, to avoid an unbounded loop.
        if not fact_check_done:
            fact_check_done = True
            _progress(f"核对事实…{attempt_label}")
            fc_issues = fact_check_briefing(articles, items, model)
            if fc_issues:
                logger.warning("briefing attempt %d: fact-check issues, retrying once: %s",
                               attempt + 1, fc_issues)
                fc_hint = "\n【事实核查发现以下问题，请修正后重新生成整篇摘要，确保严格符合原文事实】\n" + \
                          "\n".join(f"- {i}" for i in fc_issues)
                fc_retry_raw = _call_api(
                    model, [{"role": "user", "content": _build_prompt(remaining, extra_hint=fc_hint)}],
                    8192, purpose="briefing",
                )
                fc_r_start, fc_r_end = fc_retry_raw.find("["), fc_retry_raw.rfind("]") + 1
                if fc_r_start != -1 and fc_r_end != 0:
                    try:
                        fc_retry_items = json.loads(fc_retry_raw[fc_r_start:fc_r_end])
                        items = _dedupe_consecutive_briefing_context(fc_retry_items, expected_cards)
                        second_fc_issues = fact_check_briefing(articles, items, model)
                        if second_fc_issues:
                            logger.warning(
                                "briefing: fact-check issues persist after retry (accepting): %s",
                                second_fc_issues)
                    except json.JSONDecodeError as e:
                        logger.warning("briefing: fact-check retry JSON parse error (%s) — "
                                       "keeping original attempt", e)
                else:
                    logger.warning("briefing: fact-check retry produced no JSON array — "
                                   "keeping original attempt")

        # Scan in reading order: context sentences accumulate until the next
        # sentence containing a still-uncovered target word, then attach to it.
        # We match by scanning the text ourselves — the AI's target_word tag is
        # not trusted (it can lie), only the actual sentence content counts.
        context_buf: list[str] = []
        for item in items:
            s_zh = item.get("sentence_zh", "").strip()
            if not s_zh:
                continue
            matched = None
            for card in remaining:
                if _briefing_word_match(card["word_zh"], s_zh):
                    matched = card
                    break
            if matched is None:
                context_buf.append(s_zh)
                continue
            remaining.remove(matched)
            article_idx = item.get("article_idx")
            source_url = source_title = source_name = None
            if isinstance(article_idx, int) and 0 <= article_idx < len(articles):
                _art = articles[article_idx]
                source_url = _art.get("url") or None
                source_title = _art.get("title") or None
                source_name = _art.get("source_name") or None
            context_zh = " ".join(context_buf)
            context_buf = []
            sentences.append({
                "word_ids": [matched["word_id"]],
                "sentence_zh": s_zh,
                "sentence_en": "",
                "concept_en": "",
                "concept_zh": "",
                "reasoning_zh": context_zh,
                "context_zh": context_zh,
                "source_url": source_url,
                "source_title": source_title,
                "source_name": source_name,
                "tokens": [],
            })

        _progress(f"生成新闻总结…{attempt_label}")
        if remaining:
            logger.warning(
                "briefing attempt %d: missing words (will re-request): %s",
                attempt + 1, [c["word_zh"] for c in remaining],
            )

    # Per-word fallback so every card ends up with a sentence.
    for card in remaining:
        logger.warning("briefing: using fallback sentence for %s", card["word_zh"])
        sentences.append({
            "word_ids": [card["word_id"]],
            "sentence_zh": card.get("source_sentence") or f"我学了{card['word_zh']}这个词。",
            "sentence_en": "",
            "concept_en": "",
            "concept_zh": "",
            "reasoning_zh": "",
            "context_zh": "",
            "source_url": None,
            "tokens": [],
        })

    _fill_translations(sentences, progress_key=progress_key)

    # Context → German via Google Translate (translator.py), per Daniel's design:
    # keep the AI's job small, translation is mechanical. Only non-empty contexts
    # go into the batch — empty lines break translate_batch's newline splitting.
    ctx_texts = [s.pop("context_zh", "") or "" for s in sentences]
    for s in sentences:
        s["context_de"] = None
    nonempty = [(i, t) for i, t in enumerate(ctx_texts) if t]
    if nonempty:
        try:
            import translator as _t
            de_list = _t.translate_batch([t for _, t in nonempty], target="de")
            for (i, _), de in zip(nonempty, de_list):
                sentences[i]["context_de"] = de.strip() or None
        except Exception as e:
            logger.warning("briefing: context translation failed — %s", e)

    return sentences


def summarize_news_items(items: list[dict], model: str = "gpt-5-mini",
                         max_items: int = 8, progress_key: str | None = None) -> list[dict]:
    """News auto mode, step 1 of 2: pick the most important of today's fetched
    news items and condense each into a short summary. The result feeds
    generate_news_sentences exactly like pasted articles do.

    items: news_fetcher.fetch_all() output [{url, title, text, source_name}]
    Returns [{url, title, text}] — text is the AI's condensed summary.

    Falls back to the first max_items raw items when the AI reply is unusable —
    that is still real news content, only the selection/condensing is skipped
    (a network failure upstream raises news_fetcher.NewsFetchError instead).
    """
    if not items:
        return []

    _set_progress(progress_key, phase="request", msg="Selecting today's top news…", percent=12)
    listing = "\n\n".join(
        f"[{i}] ({it.get('source_name', '')}) {it.get('title', '')}\n{(it.get('text') or '')[:400]}"
        for i, it in enumerate(items)
    )
    prompt = f"""Below are today's news items fetched from German and international sources.

{listing}

Task: choose the {max_items} most important items for a daily world-news briefing.
Balance the selection: German domestic news, international news, and China-related news (when available).
Skip near-duplicates covering the same event.

Return ONLY a JSON array, no other text:
[
  {{"idx": 0, "summary": "3-5 sentence factual English summary of the item"}}
]
idx is the item number in square brackets above."""

    try:
        raw = _call_api(model, [{"role": "user", "content": prompt}], 8192, purpose="news-select")
        start, end = raw.find("["), raw.rfind("]") + 1
        picked = json.loads(raw[start:end]) if start != -1 and end != 0 else []
        articles = []
        for p in picked:
            idx = p.get("idx")
            summary = (p.get("summary") or "").strip()
            if isinstance(idx, int) and 0 <= idx < len(items) and summary:
                it = items[idx]
                articles.append({"url": it.get("url", ""), "title": it.get("title", ""),
                                 "source_name": it.get("source_name", ""), "text": summary})
        if articles:
            logger.info("[%s] summarize_news_items: %d/%d items selected",
                        model, len(articles), len(items))
            return articles[:max_items]
        logger.warning("summarize_news_items: empty/unusable selection, falling back to raw items")
    except Exception as e:
        logger.warning("summarize_news_items failed (%s), falling back to raw items", e)
    return [{"url": it.get("url", ""), "title": it.get("title", ""),
             "source_name": it.get("source_name", ""), "text": (it.get("text") or "")[:600]}
            for it in items[:max_items]]


_PODCAST_DETAIL_WORDS = {
    "short": "~150",
    "medium": "~300",
    "detailed": "500-700",
}


def summarize_podcast_transcript(transcript: str, title: str,
                                 detail_level: str = "detailed") -> dict:
    """Podcast crawler (issue #479): one AI call that turns a raw Chinese
    transcript into a German summary + a list of HSK5+ vocabulary worth
    reviewing before listening.

    Uses resolve_briefing_model() (OpenAI) — same reasoning as news/briefing:
    DeepSeek censors this kind of freeform content, and the model is already
    verified/cached there.

    Returns {"summary_de": str, "words": [{"word", "pinyin", "definition_de", "hsk"}]}.
    Falls back to an empty-ish result (summary_de note, words=[]) on any
    parse/API failure — callers store status='error' and move on, they don't
    crash the whole crawl run over one bad transcript.
    """
    words_target = _PODCAST_DETAIL_WORDS.get(detail_level, _PODCAST_DETAIL_WORDS["detailed"])
    # Transcripts can be long (auto-captions of a 30-60min episode) — cap input
    # to keep the request within a reasonable token budget.
    excerpt = transcript[:20000]

    prompt = f"""You are summarizing a Chinese-language podcast episode for a German-speaking
learner of Chinese (HSK 4-5 level, learning towards HSK 6).

Episode title: {title}

Transcript (Chinese, auto/manual captions, may contain minor recognition errors):
{excerpt}

Task:
1. Write a detailed German-language summary of what is discussed in the episode, so the
   listener understands the content before listening. Target length: {words_target} words.
   Structure it into multiple paragraphs. Wrap the most important vocabulary/terms/names in
   <strong>...</strong> HTML tags (these become the highlighted words in the email).
2. Extract the 10-20 most important Chinese words/phrases from the transcript that are HSK
   level 5 or above (i.e. non-basic vocabulary Daniel would benefit from pre-learning). For
   each, give pinyin and a German definition.

Return ONLY a JSON object, no other text, no markdown fences:
{{
  "summary_de": "<German HTML summary with <strong> tags>",
  "words": [
    {{"word": "词语", "pinyin": "cí yǔ", "definition_de": "kurze deutsche Definition", "hsk": 5}}
  ]
}}"""

    model = resolve_briefing_model()
    try:
        raw = _call_api(model, [{"role": "user", "content": prompt}], 8192, purpose="podcast-summary")
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end]) if start != -1 and end != 0 else {}
        summary_de = (data.get("summary_de") or "").strip()
        words = []
        for w in data.get("words") or []:
            word = (w.get("word") or "").strip()
            if not word:
                continue
            words.append({
                "word": word,
                "pinyin": (w.get("pinyin") or "").strip(),
                "definition_de": (w.get("definition_de") or "").strip(),
                "hsk": w.get("hsk") if isinstance(w.get("hsk"), int) else 5,
            })
        if summary_de:
            return {"summary_de": summary_de, "words": words}
        logger.warning("summarize_podcast_transcript: empty summary_de in AI reply")
    except Exception as e:
        logger.warning("summarize_podcast_transcript failed (%s)", e)
    return {"summary_de": "", "words": []}


def estimate_story_tokens(num_cards: int) -> int:
    """Rough token estimate for generating a story with num_cards words.

    Input:  ~200 base + 13 tokens/card
    Output: ~75 tokens/card + 100 overhead
    """
    return 200 + 13 * num_cards + 75 * num_cards + 100

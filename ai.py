"""
AI story generation — supports Anthropic and OpenAI-compatible providers.

Supported model prefixes:
  claude-*      → Anthropic SDK (ANTHROPIC_API_KEY)
  glm-*         → Zhipu AI (ZHIPU_API_KEY)
  deepseek-*    → DeepSeek (DEEPSEEK_API_KEY)
  qwen-*        → Alibaba Qwen/DashScope (QWEN_API_KEY)
"""

import json
import logging
import os
import re
import time

import anthropic
import openai

import database

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-v4-flash"

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
    raise ValueError(f"Unknown provider for model: {model}")


def _call_api(model: str, messages: list, max_tokens: int, purpose: str) -> str:
    """Call the appropriate provider, log usage, and return the raw text response."""
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
        resp = client.chat.completions.create(model=model, max_tokens=max_tokens, messages=messages)
        elapsed = time.time() - t0
        logger.info("[%s] API call done in %.1fs — in=%d out=%d purpose=%s",
                    model, elapsed, resp.usage.prompt_tokens, resp.usage.completion_tokens, purpose)
        database.log_api_call(
            model=resp.model,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
            purpose=purpose,
        )
        return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_story(cards: list[dict], topic: str | None = None, max_hsk: int = 2,
                   model: str = DEFAULT_MODEL,
                   progress_key: str | None = None,
                   grammar_focus: str | None = None,
                   grammar_pct: int = 75,
                   mode: str = "story") -> tuple[list[dict], str]:
    """
    Generate Mandarin sentences covering all target vocab words.

    cards:         list of dicts with keys word_id, word_zh, pinyin, definition, pos
    topic:         optional theme/question/topic to guide the content
    max_hsk:       maximum HSK level for non-target background vocabulary (1-6)
    model:         model ID to use for generation
    grammar_focus: optional grammar pattern to encourage (e.g. "把字句")
    grammar_pct:   approximate percentage of sentences that should use the grammar (0-100)
    mode:          "story" | "qa" | "expository"
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
        return word_zh.endswith(('。', '！', '？', '!', '?'))

    def _word_in_sentence(word_zh: str, sentence_zh: str) -> bool:
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
- Use only HSK 1-{max_hsk} vocabulary for non-target words
- Keep each sentence short and simple
{style_rule}
- NEVER use ASCII double-quote characters (") inside Chinese sentences — use 「」or （）instead

Return ONLY a numbered list of Chinese sentences, no explanation:
1. ...
2. ..."""

    logger.info("[%s] generate_story: %d 张卡片 mode=%s", model, len(cards), mode)
    logger.debug("Prompt:\n%s", prompt)

    max_tokens = len(cards) * 150 + 200
    if not model.startswith("claude-"):
        max_tokens = min(max_tokens, 8192)

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
            logger.error("generate_story: no numbered sentences found in response")
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
            if attempt >= 1 and missing_ratio < 0.03:
                _patch_missing(parsed, missing_ids, card_by_id)
                _set_progress(progress_key, phase="translating",
                              msg="Translating sentences…", percent=88)
                _fill_translations(parsed, progress_key=progress_key)
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
        _fill_translations(parsed, progress_key=progress_key)
        logger.info("generate_story: DONE — %.1fs total", time.time() - t_start)
        _set_progress(progress_key, phase="ai_done",
                      msg=f"✓ {len(parsed)} sentences (attempt {attempt + 1})", percent=93)
        return parsed, prompt

    if last_partial is not None:
        parsed, missing_ids = last_partial
        if len(missing_ids) / len(cards) < 0.03:
            _patch_missing(parsed, missing_ids, card_by_id)
            _set_progress(progress_key, phase="translating",
                          msg="Translating sentences…", percent=88)
            _fill_translations(parsed, progress_key=progress_key)
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
                   card_by_id: dict[int, dict]) -> None:
    """Append fallback sentences for words the AI failed to include."""
    for wid in missing_word_ids:
        card = card_by_id.get(wid)
        if not card:
            continue
        fallback_zh = card.get("source_sentence") or f"我学了{card['word_zh']}这个词。"
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
    want_chars    = want_etym or want_comp
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
        char_field_lines = []
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
        sections.append("CHARACTER DATA: For each character above:\n" + "\n".join(char_field_lines))

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
        if want_etym:
            char_obj_keys += ', "etymology": "..."'
        if want_comp:
            char_obj_keys += ', "compounds": [{"simplified": "...", "pinyin": "...", "meaning": "..."}]'
        json_keys.append(f'  "characters": [{{{char_obj_keys}}}]')

    json_template = "{\n" + ",\n".join(json_keys) + "\n}"

    prompt = (
        f"You are a Chinese dictionary expert generating SRS flashcard content.\n\n"
        f"Entry:\n{entry_block}{char_block}\n\n"
        f"Generate ONLY the fields listed. All German text must be in German.\n\n"
        + "\n\n".join(sections)
        + f"\n\nReturn ONLY valid JSON with exactly these top-level keys:\n{json_template}"
    )

    logger.info("[%s] regenerate_entry_fields: %s fields=%s", model, word_zh, fields)
    raw = _call_api(model, [{"role": "user", "content": prompt}], max_tokens=1800,
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

def _fill_translations(sentences: list[dict], progress_key: str | None = None) -> None:
    """Translate sentence_zh → sentence_de and sentence_fr in-place using Google Translate."""
    try:
        import translator as _t
        texts = [s.get("sentence_zh", "") for s in sentences]
        total = len(texts)

        if progress_key and total > 0:
            _set_progress(progress_key, phase="translating",
                          msg=f"Translating… 0/{total}", percent=88)

        t0 = time.time()
        de_results = _t.translate_batch(texts, target="de")
        logger.info("translate DE done in %.1fs (%d sentences)", time.time() - t0, total)

        t1 = time.time()
        fr_results = _t.translate_batch(texts, target="fr")
        logger.info("translate FR done in %.1fs (%d sentences)", time.time() - t1, total)

        if progress_key and total > 0:
            _set_progress(progress_key, phase="translating",
                          msg=f"Translating… {total}/{total}", percent=92)

        for s, de, fr in zip(sentences, de_results, fr_results):
            s["sentence_de"] = de
            s["sentence_fr"] = fr
            s.setdefault("sentence_en", "")
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


def _fallback_sentences(cards: list[dict]) -> list[dict]:
    """Minimal sentences used when the AI response cannot be parsed."""
    result = [
        {
            "word_ids": [c["word_id"]],
            "sentence_zh": f"我学了{c['word_zh']}这个词。",
            "sentence_en": "",
            "sentence_de": "",
            "sentence_fr": "",
        }
        for c in cards
    ]
    _fill_translations(result)
    return result


def estimate_story_tokens(num_cards: int) -> int:
    """Rough token estimate for generating a story with num_cards words.

    Input:  ~200 base + 13 tokens/card
    Output: ~75 tokens/card + 100 overhead
    """
    return 200 + 13 * num_cards + 75 * num_cards + 100

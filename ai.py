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

import anthropic
import openai

import database

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-chat"


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
    if model.startswith("claude-"):
        client = anthropic.Anthropic()
        msg = client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
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
                   model: str = DEFAULT_MODEL) -> list[dict]:
    """
    Generate a short Mandarin story, one sentence per card.

    cards:   list of dicts with keys word_id, word_zh, pinyin, definition, pos
    topic:   optional theme/setting to guide the story
    max_hsk: maximum HSK level for non-target background vocabulary (1-6)
    model:   model ID to use for generation
    Returns: list of {word_id, sentence_zh, sentence_en} — same length as cards.
    Returns [] immediately if cards is empty (no API call made).
    """
    if not cards:
        return []

    def _is_sentence(word_zh: str) -> bool:
        return word_zh.endswith(('。', '！', '？', '!', '?'))

    word_list_lines = []
    for i, c in enumerate(cards):
        if _is_sentence(c['word_zh']):
            word_list_lines.append(
                f"{i + 1}. (word_id={c['word_id']}) [USE AS-IS] {c['word_zh']}"
            )
        else:
            word_list_lines.append(
                f"{i + 1}. (word_id={c['word_id']}) {c['word_zh']}"
                f" ({c.get('pinyin', '')}) — {c.get('definition', '')}"
            )
    word_list = "\n".join(word_list_lines)

    topic_line = f"- The story should be set around this topic or theme: {topic}\n" if topic else ""

    prompt = f"""Write a short Mandarin Chinese story to help an HSK 4-5 learner review vocabulary.

Target words — write exactly one sentence per word, in this order:
{word_list}

Rules:
- Exactly {len(cards)} sentences total
- For items marked [USE AS-IS]: use that text verbatim as sentence_zh — only provide an English translation
- For all other items: write a sentence that naturally contains the target word
- Each generated sentence must be ≤20 Chinese characters
- Use proper Chinese punctuation — include commas（，）where natural pauses occur
- Use only HSK 1-{max_hsk} vocabulary for non-target words
{topic_line}- The sentences must form a coherent narrative with the same recurring characters
- NEVER use ASCII double-quote characters (") inside Chinese sentences — use 「」or （）instead if quoting is needed
- Return ONLY valid JSON, no explanation, no markdown:
[
  {{"word_id": <integer>, "sentence_zh": "<Chinese sentence>", "sentence_en": "<English translation>"}},
  ...
]"""

    logger.info("[%s] generate_story: %d 张卡片", model, len(cards))
    logger.debug("Prompt:\n%s", prompt)

    # 150 tokens per sentence is generous; add 200 for overhead/fences
    max_tokens = len(cards) * 150 + 200

    missing_hint = ""
    for attempt in range(3):
        full_prompt = prompt + missing_hint
        raw = _call_api(model, [{"role": "user", "content": full_prompt}], max_tokens,
                        purpose="story")

        logger.debug("Raw response attempt=%d (%d chars):\n%s", attempt + 1, len(raw), raw)

        # Try to extract a JSON array from the response (handles markdown fences,
        # leading/trailing text, or extra commentary the model sometimes adds)
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(0)

        try:
            result = json.loads(raw)
            if isinstance(result, list) and len(result) >= 1:
                result = result[:len(cards)]
                for item, card in zip(result, cards):
                    item["word_id"] = card["word_id"]
                if len(result) == len(cards):
                    missing = [
                        card["word_zh"]
                        for item, card in zip(result, cards)
                        if not _is_sentence(card["word_zh"])
                        and card["word_zh"] not in item.get("sentence_zh", "")
                    ]
                    if missing:
                        logger.warning(
                            "generate_story: attempt %d — words missing from sentences: %s",
                            attempt + 1, missing,
                        )
                        missing_hint = (
                            f"\n\nIMPORTANT: Your previous attempt was missing these words "
                            f"— each MUST appear verbatim in its sentence: {', '.join(missing)}"
                        )
                        continue
                    logger.info("generate_story: success — %d sentences (attempt %d)",
                                len(result), attempt + 1)
                    return result
                else:
                    logger.warning("generate_story: count mismatch — got %d, need %d",
                                   len(result), len(cards))
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.error("generate_story: JSON parse error: %s", e)

    logger.warning("generate_story: falling back to placeholder sentences")
    return _fallback_sentences(cards)


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


_ENRICH_MODEL = "glm-4-flash"


def enrich_word(word: dict, characters: list[dict], model: str = DEFAULT_MODEL) -> dict:
    """
    Determine HSK level for a word and fill missing character data (etymology, other_meanings).
    Always uses GLM-4-Flash (free tier) — the model parameter is ignored.
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

def _fallback_sentences(cards: list[dict]) -> list[dict]:
    """Minimal sentences used when the AI response cannot be parsed."""
    return [
        {
            "word_id": c["word_id"],
            "sentence_zh": f"我学了{c['word_zh']}这个词。",
            "sentence_en": f"I learned the word: {c.get('definition', c['word_zh'])}.",
        }
        for c in cards
    ]

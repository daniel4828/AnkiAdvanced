"""
AI story generation using the Anthropic API.

generate_story() calls Haiku to produce a short coherent Chinese story —
one sentence per target word. All error cases fall back to simple placeholder
sentences so the review session always continues.
"""

import json
import logging
import re

import anthropic

logger = logging.getLogger(__name__)


def generate_story(cards: list[dict], topic: str | None = None, max_hsk: int = 2) -> list[dict]:
    """
    Generate a short Mandarin story using Haiku, one sentence per card.

    cards:   list of dicts with keys word_id, word_zh, pinyin, definition, pos
    topic:   optional theme/setting to guide the story
    max_hsk: maximum HSK level for non-target background vocabulary (1-6)
    Returns: list of {word_id, sentence_zh, sentence_en} — same length as cards.
    Returns [] immediately if cards is empty (no API call made).
    """
    if not cards:
        return []

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env automatically

    word_list = "\n".join(
        f"{i + 1}. (word_id={c['word_id']}) {c['word_zh']}"
        f" ({c.get('pinyin', '')}) — {c.get('definition', '')}"
        for i, c in enumerate(cards)
    )

    topic_line = f"- The story should be set around this topic or theme: {topic}\n" if topic else ""

    prompt = f"""Write a short Mandarin Chinese story to help an HSK 4-5 learner review vocabulary.

Target words — write exactly one sentence per word, in this order:
{word_list}

Rules:
- Exactly {len(cards)} sentences total
- Each sentence must naturally contain the corresponding target word
- Each sentence must be ≤15 Chinese characters
- Use proper Chinese punctuation — include commas（，）where natural pauses occur
- Use only HSK 1-{max_hsk} vocabulary for non-target words
{topic_line}- The sentences must form a coherent narrative with the same recurring characters
- Return ONLY valid JSON, no explanation, no markdown:
[
  {{"word_id": <integer>, "sentence_zh": "<Chinese sentence>", "sentence_en": "<English translation>"}},
  ...
]"""

    words = [c['word_zh'] for c in cards]
    logger.info("generate_story: %d cards: %s", len(cards), words)
    logger.debug("Prompt:\n%s", prompt)

    # 150 tokens per sentence is generous; add 200 for overhead/fences
    max_tokens = len(cards) * 150 + 200

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    logger.debug("Raw response (%d chars, stop=%s):\n%s",
                 len(raw), message.stop_reason, raw)

    if message.stop_reason == "max_tokens":
        logger.warning("Response truncated — increase max_tokens (was %d)", max_tokens)

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
                logger.info("generate_story: success — %d sentences", len(result))
                return result
            else:
                logger.warning("generate_story: count mismatch — got %d, need %d",
                               len(result), len(cards))
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.error("generate_story: JSON parse error: %s", e)

    logger.warning("generate_story: falling back to placeholder sentences")
    return _fallback_sentences(cards)


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

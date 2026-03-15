"""
AI story generation using the Anthropic API.

generate_story() calls Haiku to produce a short coherent Chinese story —
one sentence per target word. All error cases fall back to simple placeholder
sentences so the review session always continues.
"""

import json
import re
import sys

import anthropic


def generate_story(cards: list[dict]) -> list[dict]:
    """
    Generate a short Mandarin story using Haiku, one sentence per card.

    cards: list of dicts with keys word_id, word_zh, pinyin, definition, pos
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

    prompt = f"""Write a short Mandarin Chinese story to help an HSK 4-5 learner review vocabulary.

Target words — write exactly one sentence per word, in this order:
{word_list}

Rules:
- Exactly {len(cards)} sentences total
- Each sentence must naturally contain the corresponding target word
- Each sentence must be ≤15 Chinese characters
- Use only HSK 1-2 vocabulary for non-target words
- The sentences must form a coherent narrative with the same recurring characters
- Return ONLY valid JSON, no explanation, no markdown:
[
  {{"word_id": <integer>, "sentence_zh": "<Chinese sentence>", "sentence_en": "<English translation>"}},
  ...
]"""

    print(f"[ai] Requesting story for {len(cards)} cards: "
          f"{[c['word_zh'] for c in cards]}", file=sys.stderr)

    # 150 tokens per sentence is generous; add 200 for overhead/fences
    max_tokens = len(cards) * 150 + 200

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    print(f"[ai] Raw response ({len(raw)} chars, stop={message.stop_reason}): "
          f"{raw[:120]!r}{'...' if len(raw) > 120 else ''}", file=sys.stderr)

    if message.stop_reason == "max_tokens":
        print(f"[ai] Response was truncated! Increase max_tokens.", file=sys.stderr)

    # Try to extract a JSON array from the response (handles markdown fences,
    # leading/trailing text, or extra commentary the model sometimes adds)
    json_match = re.search(r'\[\s*\{.*?\}\s*\]', raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        result = json.loads(raw)
        if isinstance(result, list) and len(result) >= 1:
            # Truncate if the model returned too many sentences
            result = result[:len(cards)]
            # Enforce correct word_ids — the model sometimes uses wrong values
            for item, card in zip(result, cards):
                item["word_id"] = card["word_id"]
            if len(result) == len(cards):
                print(f"[ai] Success: {len(result)} sentences generated.", file=sys.stderr)
                return result
            else:
                print(f"[ai] Count mismatch: got {len(result)}, need {len(cards)}.",
                      file=sys.stderr)
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        print(f"[ai] JSON parse error: {e}", file=sys.stderr)

    # Fallback
    print(f"[ai] Using placeholder sentences.", file=sys.stderr)
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

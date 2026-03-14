import json
import os
import re

from anthropic import Anthropic

_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def generate_sentences(target_words: list[dict], easy_words: list[str] | None = None) -> dict:
    """
    Generate a coherent story with one sentence per target word using Haiku.

    target_words: list of {word_zh, pinyin, definition}
    Returns: {sentences_zh: [...], sentences_en: [...]}
    """
    n = len(target_words)
    word_list = "\n".join(
        f"{i+1}. {w['word_zh']} ({w['pinyin']}) — {w['definition']}"
        for i, w in enumerate(target_words)
    )

    prompt = f"""You are helping an HSK 4–5 Mandarin learner (native German speaker) review vocabulary.

Write a single coherent short story in Mandarin Chinese with exactly {n} sentences.
The story should have a clear setting, characters, and flow naturally from sentence to sentence.

Rules:
- Sentence 1 must contain target word 1, sentence 2 must contain target word 2, and so on — in exact order
- Each target word must appear naturally in its sentence
- Keep sentences short and clear (10–30 characters each)
- Use only HSK 1–2 level vocabulary for all non-target words (very common, simple words only)
- German translations must sound natural, not literal
- The story as a whole must make sense and be engaging

Target words (one per sentence, in order):
{word_list}

Return ONLY valid JSON — no markdown, no explanation, nothing else:
{{
  "sentences_zh": ["sentence1", "sentence2", ...],
  "sentences_en": ["german1", "german2", ...]
}}"""

    response = get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    print(f"[AI] raw response (first 300 chars): {text[:300]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError as e:
                print(f"[AI] JSON parse failed after regex: {e}")
        print(f"[AI] FALLBACK triggered — full response:\n{text}")
    return {
        "sentences_zh": [w["word_zh"] for w in target_words],
        "sentences_en": [w["definition"] for w in target_words],
    }


def evaluate_answer(en_sentence: str, zh_sentence: str, target_words: list[str], user_answer: str) -> dict:
    """
    Evaluate a learner's Chinese translation using Sonnet.
    Returns: {word_correct, grammar_correct, score, feedback}
    """
    prompt = f"""You are evaluating a Mandarin Chinese translation by a learner.

Original German: {en_sentence}
Correct Chinese: {zh_sentence}
Target words that must appear: {', '.join(target_words)}
Learner's answer: {user_answer}

Return ONLY valid JSON:
{{
  "word_correct": true/false,
  "grammar_correct": true/false,
  "score": 0-100,
  "feedback": "brief feedback in English, max 1 sentence"
}}"""

    response = get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {"word_correct": False, "grammar_correct": False, "score": 0, "feedback": "Could not evaluate."}

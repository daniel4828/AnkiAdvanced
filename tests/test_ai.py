"""
Tests for ai.py — story generation.

Fast tests (no API key needed):
  Mock the Anthropic client and test that generate_story handles all cases
  correctly: good responses, malformed JSON, wrong sentence count, empty input.

Live tests (require ANTHROPIC_API_KEY):
  Call the real Haiku API and verify structural quality of the output —
  target word appears in each sentence, sentence length ≤15 Chinese chars.
  Run these manually: pytest -m live_api
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

# ai.py does not exist yet — these tests will fail with ImportError until
# we implement it. That is expected TDD behaviour.
import ai

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_CARDS = [
    {"word_id": 1, "word_zh": "担心", "pinyin": "dān xīn", "definition": "to worry",       "pos": "v"},
    {"word_id": 2, "word_zh": "努力", "pinyin": "nǔ lì",   "definition": "to work hard",  "pos": "v"},
    {"word_id": 3, "word_zh": "进步", "pinyin": "jìn bù",  "definition": "progress",       "pos": "n"},
]

VALID_AI_RESPONSE = [
    {"word_id": 1, "sentence_zh": "她很担心考试。",     "sentence_en": "She is worried about the exam."},
    {"word_id": 2, "sentence_zh": "他每天努力学习。",   "sentence_en": "He studies hard every day."},
    {"word_id": 3, "sentence_zh": "她看到了他的进步。", "sentence_en": "She saw his progress."},
]


def _mock_anthropic(response_text: str):
    """
    Patches anthropic.Anthropic so that messages.create() returns a fake
    response containing response_text as its first content block.
    Returns a context manager — use with `with _mock_anthropic(...):`.
    """
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=response_text)]
    mock_client.messages.create.return_value = mock_msg
    return patch("anthropic.Anthropic", return_value=mock_client)


# ---------------------------------------------------------------------------
# Fast (mocked) tests
# ---------------------------------------------------------------------------

class TestGenerateStory:

    def test_returns_one_sentence_per_card(self):
        """
        generate_story must return exactly N sentences for N input cards.
        If this breaks, the frontend can't match sentences to cards.
        """
        with _mock_anthropic(json.dumps(VALID_AI_RESPONSE)):
            result = ai.generate_story(SAMPLE_CARDS)
        assert len(result) == len(SAMPLE_CARDS)

    def test_word_ids_match_input_cards(self):
        """
        Each returned sentence must carry the word_id of its corresponding
        input card (in order). The frontend uses word_id to find the right
        sentence for each card.
        """
        with _mock_anthropic(json.dumps(VALID_AI_RESPONSE)):
            result = ai.generate_story(SAMPLE_CARDS)
        for sentence, card in zip(result, SAMPLE_CARDS):
            assert sentence["word_id"] == card["word_id"], (
                f"Expected word_id={card['word_id']}, got {sentence['word_id']}"
            )

    def test_each_sentence_has_zh_and_en(self):
        """
        Every sentence must have both a Chinese text and an English
        translation — the frontend displays both.
        """
        with _mock_anthropic(json.dumps(VALID_AI_RESPONSE)):
            result = ai.generate_story(SAMPLE_CARDS)
        for s in result:
            assert s.get("sentence_zh"), f"Missing sentence_zh in {s}"
            assert s.get("sentence_en"), f"Missing sentence_en in {s}"

    def test_empty_cards_returns_empty_list_without_api_call(self):
        """
        generate_story([]) must return [] immediately without ever calling
        the Anthropic API (no point generating a story for zero words, and
        it would waste tokens).
        """
        with patch("anthropic.Anthropic") as mock_cls:
            result = ai.generate_story([])
        assert result == []
        mock_cls.assert_not_called()

    def test_malformed_json_falls_back_to_placeholder_sentences(self):
        """
        The AI sometimes returns prose, markdown, or broken JSON instead of
        valid JSON. generate_story must catch this and return one fallback
        sentence per card so the session can still continue.
        """
        with _mock_anthropic("Here is your story: she worried a lot. Very nice."):
            result = ai.generate_story(SAMPLE_CARDS)
        # Must still return one sentence per card
        assert len(result) == len(SAMPLE_CARDS)
        for sentence, card in zip(result, SAMPLE_CARDS):
            assert sentence["word_id"] == card["word_id"]
            assert sentence["sentence_zh"]
            assert sentence["sentence_en"]

    def test_wrong_sentence_count_falls_back(self):
        """
        If the AI returns fewer sentences than cards (e.g. it skipped a word),
        generate_story must detect the mismatch and fall back rather than
        silently misaligning sentences with cards.
        """
        too_few = json.dumps(VALID_AI_RESPONSE[:1])  # 1 sentence for 3 cards
        with _mock_anthropic(too_few):
            result = ai.generate_story(SAMPLE_CARDS)
        assert len(result) == len(SAMPLE_CARDS)

    def test_markdown_code_fence_stripped(self):
        """
        The AI frequently wraps JSON in ```json ... ``` code fences.
        generate_story must strip them before parsing.
        """
        fenced = f"```json\n{json.dumps(VALID_AI_RESPONSE)}\n```"
        with _mock_anthropic(fenced):
            result = ai.generate_story(SAMPLE_CARDS)
        assert len(result) == len(SAMPLE_CARDS)
        for sentence, card in zip(result, SAMPLE_CARDS):
            assert sentence["word_id"] == card["word_id"]


# ---------------------------------------------------------------------------
# Live (real API) tests
# Run with: pytest -m live_api
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="requires ANTHROPIC_API_KEY — run manually to verify real story quality",
)
class TestGenerateStoryLive:
    """
    These tests call the real Haiku API and check structural quality of the
    output. They are slow (~3-5s) and cost API tokens, so they are skipped
    by default and only run when ANTHROPIC_API_KEY is set.

    What we check (without needing another AI to evaluate):
      - Target word appears in its sentence
      - Sentence length ≤ 15 Chinese characters (spec requirement)
      - English translation is non-empty
    """

    LIVE_CARDS = [
        {"word_id": 10, "word_zh": "担心", "pinyin": "dān xīn", "definition": "to worry",      "pos": "v"},
        {"word_id": 11, "word_zh": "努力", "pinyin": "nǔ lì",   "definition": "to work hard", "pos": "v"},
        {"word_id": 12, "word_zh": "进步", "pinyin": "jìn bù",  "definition": "progress",      "pos": "n"},
    ]

    @pytest.fixture(scope="class")
    def story(self):
        """Generate one real story, shared across all live tests in this class."""
        return ai.generate_story(self.LIVE_CARDS)

    def test_live_correct_sentence_count(self, story):
        assert len(story) == len(self.LIVE_CARDS)

    def test_live_each_sentence_contains_target_word(self, story):
        """
        The most important structural check: the target word must actually
        appear in its sentence. Without this the review makes no sense.
        """
        for sentence, card in zip(story, self.LIVE_CARDS):
            assert card["word_zh"] in sentence["sentence_zh"], (
                f"Target word '{card['word_zh']}' missing from: {sentence['sentence_zh']}"
            )

    def test_live_sentence_length_at_most_15_chinese_chars(self, story):
        """
        Per spec: each sentence ≤ 15 Chinese characters.
        We count only characters in the CJK Unified Ideographs block
        (U+4E00–U+9FFF) so punctuation doesn't count against the limit.
        """
        for sentence, card in zip(story, self.LIVE_CARDS):
            chinese_chars = [
                c for c in sentence["sentence_zh"]
                if "\u4e00" <= c <= "\u9fff"
            ]
            assert len(chinese_chars) <= 15, (
                f"Sentence for '{card['word_zh']}' is {len(chinese_chars)} chars "
                f"(limit 15): {sentence['sentence_zh']}"
            )

    def test_live_english_translations_present(self, story):
        for sentence in story:
            assert sentence.get("sentence_en"), (
                f"Missing English translation for: {sentence['sentence_zh']}"
            )

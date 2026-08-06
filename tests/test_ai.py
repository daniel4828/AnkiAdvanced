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

# What the model is asked to return: a numbered list of Chinese sentences,
# each containing exactly one target word verbatim. No JSON, no translations.
VALID_AI_RESPONSE = """1. 她很担心考试。
2. 他每天努力学习。
3. 她看到了他的进步。"""

# 21 words so that one missing word is under the 5%-patch threshold.
_MANY_WORDS = [
    "担心", "努力", "进步", "学习", "老师", "学生", "朋友", "工作", "问题", "办法",
    "时间", "机会", "结果", "经验", "习惯", "态度", "计划", "目标", "方向", "选择",
    "决定",
]


def _mock_api(response_text: str):
    """
    Patch ai._call_api so the provider layer returns response_text verbatim.
    Returns a context manager — use with `with _mock_api(...):`.

    This used to patch anthropic.Anthropic. That stopped working the day the
    default model moved to DeepSeek: generate_story went through the
    OpenAI-compatible client instead, the mock never applied, and the tests
    died on a missing DEEPSEEK_API_KEY (issue #615). _call_api is the one
    choke point every provider goes through, so patching it here survives
    future changes of default model.
    """
    return patch("ai._call_api", return_value=response_text)


# Kept under the old name so the live-API tests below read unchanged.
_mock_anthropic = _mock_api


# ---------------------------------------------------------------------------
# Fast (mocked) tests
# ---------------------------------------------------------------------------

class TestGenerateStory:
    """The AI contract changed twice since these tests were written (issue #615).

    It no longer returns JSON: the prompt asks for a numbered list of Chinese
    sentences, and generate_story matches each target word into a sentence by
    string search. English/German translations are added afterwards by
    _fill_translations (a translator call, stubbed here), not by the model.
    Each result item is {word_ids: [...], sentence_zh, tokens}, and the return
    value is a (sentences, prompt) tuple.

    There is also no "placeholder fallback" anymore: an unusable response is
    retried three times and then raises, because silently reviewing invented
    sentences was worse than failing loudly.
    """

    @pytest.fixture(autouse=True)
    def _no_translation_calls(self, monkeypatch):
        """_fill_translations hits the translation service — stub it out."""
        def _fake(parsed, progress_key=None, lang="zh"):
            for s in parsed:
                s["sentence_en"] = f"[en] {s['sentence_zh']}"
        monkeypatch.setattr(ai, "_fill_translations", _fake)

    def test_returns_one_sentence_per_card(self):
        """N input cards → N sentences, so the frontend can map 1:1."""
        with _mock_api(VALID_AI_RESPONSE):
            sentences, _prompt = ai.generate_story(SAMPLE_CARDS)
        assert len(sentences) == len(SAMPLE_CARDS)

    def test_word_ids_match_input_cards(self):
        """Each card's word_id must land in exactly one sentence."""
        with _mock_api(VALID_AI_RESPONSE):
            sentences, _prompt = ai.generate_story(SAMPLE_CARDS)

        found = [wid for s in sentences for wid in s["word_ids"]]
        assert sorted(found) == sorted(c["word_id"] for c in SAMPLE_CARDS)
        assert len(found) == len(set(found)), "a word_id was assigned twice"

    def test_returns_the_prompt_it_sent(self):
        """The caller stores the prompt with the story for later inspection."""
        with _mock_api(VALID_AI_RESPONSE):
            _sentences, prompt = ai.generate_story(SAMPLE_CARDS)
        assert "担心" in prompt, "the target words must appear in the prompt"

    def test_each_sentence_has_zh_and_en(self):
        """zh comes from the model, en from _fill_translations afterwards."""
        with _mock_api(VALID_AI_RESPONSE):
            sentences, _prompt = ai.generate_story(SAMPLE_CARDS)
        for s in sentences:
            assert s.get("sentence_zh")
            assert s.get("sentence_en")

    def test_empty_cards_returns_empty_without_api_call(self):
        """No cards → no tokens spent."""
        with patch("ai._call_api") as mock_api:
            sentences, prompt = ai.generate_story([])
        assert sentences == []
        assert prompt == ""
        mock_api.assert_not_called()

    def test_response_without_numbered_lines_is_retried_then_raises(self):
        """Prose instead of a numbered list is unusable — fail loudly.

        Returning invented placeholder sentences (the old behaviour) meant
        reviewing sentences that never contained the target word.
        """
        with _mock_api("Here is your story: she worried a lot. Very nice.") as mock_api:
            with pytest.raises(RuntimeError, match="failed after 3 attempts"):
                ai.generate_story(SAMPLE_CARDS)
        assert mock_api.call_count == 3, "should retry twice before giving up"

    def test_missing_words_are_retried(self):
        """A response covering only some target words triggers a retry."""
        too_few = "1. 她很担心考试。"
        with _mock_api(too_few) as mock_api:
            with pytest.raises(RuntimeError, match="2 word"):
                ai.generate_story(SAMPLE_CARDS)
        assert mock_api.call_count == 3

    def test_a_single_missing_word_is_patched_instead_of_failing(self):
        """Below the 5% threshold the missing word gets a patched sentence,
        so one stubborn word can't sink a whole 40-word story."""
        cards = [
            {"word_id": i, "word_zh": w, "pinyin": "", "definition": "x", "pos": "n"}
            for i, w in enumerate(_MANY_WORDS, start=1)
        ]
        # Every word but the last one appears in the response.
        response = "\n".join(f"{i}. 这里有{w}。" for i, w in enumerate(_MANY_WORDS[:-1], start=1))

        with _mock_api(response):
            sentences, _prompt = ai.generate_story(cards)

        covered = {wid for s in sentences for wid in s["word_ids"]}
        assert covered == {c["word_id"] for c in cards}, "every word must be covered"


# ---------------------------------------------------------------------------
# Live (real API) tests
# Run with: pytest -m live_api
# ---------------------------------------------------------------------------

# Opt-in via RUN_LIVE_AI_TESTS=1, not via the mere presence of an API key:
# main.py loads .env at import time, so any test module that imports the app
# would silently arm these — and whether that happened depended on collection
# order (issue #627 tripped exactly that). Skipping must not be an accident.
@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_AI_TESTS"),
    reason="set RUN_LIVE_AI_TESTS=1 to verify real story quality against the live API",
)
class TestGenerateStoryLive:
    """
    These tests call the real API and check structural quality of the
    output. They are slow (~3-5s) and cost API tokens, so they only run when
    RUN_LIVE_AI_TESTS is set.

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

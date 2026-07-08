"""
Tests for the briefing mode rework (issue #444):
  - validate_briefing_items: Python-only (no AI) validation of raw AI output
  - _dedupe_consecutive_briefing_context: fallback repair for consecutive context runs
  - generate_briefing_sentences: single validation retry is triggered on violations
  - resolve_briefing_model: env var + OpenAI models-API verification + fallback chain
"""

import json
from unittest.mock import MagicMock, patch

import ai

CARDS = [
    {"word_id": 1, "word_zh": "担心", "pinyin": "dān xīn", "definition": "to worry"},
    {"word_id": 2, "word_zh": "努力", "pinyin": "nǔ lì", "definition": "to work hard"},
    {"word_id": 3, "word_zh": "进步", "pinyin": "jìn bù", "definition": "progress"},
]

ARTICLES = [{"url": "https://example.com/a", "title": "标题", "text": "正文内容"}]


# ---------------------------------------------------------------------------
# validate_briefing_items
# ---------------------------------------------------------------------------

class TestValidateBriefingItems:

    def test_valid_output_has_no_issues(self):
        items = [
            {"sentence_zh": "今天天气很好。", "target_word": None},
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
            {"sentence_zh": "他每天努力学习。", "target_word": "努力"},
            {"sentence_zh": "她看到了他的进步。", "target_word": "进步"},
        ]
        assert ai.validate_briefing_items(items, CARDS) == []

    def test_missing_word_detected(self):
        items = [
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
            {"sentence_zh": "他每天努力学习。", "target_word": "努力"},
        ]
        issues = ai.validate_briefing_items(items, CARDS)
        assert any("进步" in i for i in issues)

    def test_duplicated_word_detected(self):
        items = [
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
            {"sentence_zh": "他也很担心结果。", "target_word": "担心"},
            {"sentence_zh": "他每天努力学习。", "target_word": "努力"},
            {"sentence_zh": "她看到了他的进步。", "target_word": "进步"},
        ]
        issues = ai.validate_briefing_items(items, CARDS)
        assert any("重复" in i and "担心" in i for i in issues)

    def test_consecutive_context_sentences_detected(self):
        items = [
            {"sentence_zh": "今天天气很好。", "target_word": None},
            {"sentence_zh": "很多人出门散步。", "target_word": None},
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
            {"sentence_zh": "他每天努力学习。", "target_word": "努力"},
            {"sentence_zh": "她看到了他的进步。", "target_word": "进步"},
        ]
        issues = ai.validate_briefing_items(items, CARDS)
        assert any("连续" in i for i in issues)

    def test_single_context_sentence_before_target_is_fine(self):
        items = [
            {"sentence_zh": "今天天气很好。", "target_word": None},
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
            {"sentence_zh": "他每天努力学习。", "target_word": "努力"},
            {"sentence_zh": "她看到了他的进步。", "target_word": "进步"},
        ]
        issues = ai.validate_briefing_items(items, CARDS)
        assert not any("连续" in i for i in issues)

    def test_overlong_target_sentence_detected(self):
        long_sentence = "她" * 20 + "担心考试的结果会很不理想"
        items = [
            {"sentence_zh": long_sentence, "target_word": "担心"},
            {"sentence_zh": "他每天努力学习。", "target_word": "努力"},
            {"sentence_zh": "她看到了他的进步。", "target_word": "进步"},
        ]
        issues = ai.validate_briefing_items(items, CARDS)
        assert any("18" in i for i in issues)

    def test_too_many_sentences_detected(self):
        # 2 * 3 target words = 6 max; this has 7.
        items = [{"sentence_zh": f"上下文句{i}。", "target_word": None} for i in range(4)]
        items += [
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
            {"sentence_zh": "他每天努力学习。", "target_word": "努力"},
            {"sentence_zh": "她看到了他的进步。", "target_word": "进步"},
        ]
        issues = ai.validate_briefing_items(items, CARDS)
        assert any("超过" in i for i in issues)


# ---------------------------------------------------------------------------
# _dedupe_consecutive_briefing_context
# ---------------------------------------------------------------------------

class TestDedupeConsecutiveContext:

    def test_collapses_run_to_last_sentence(self):
        items = [
            {"sentence_zh": "上下文一。", "target_word": None},
            {"sentence_zh": "上下文二。", "target_word": None},
            {"sentence_zh": "上下文三。", "target_word": None},
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
        ]
        fixed = ai._dedupe_consecutive_briefing_context(items, CARDS)
        context_sentences = [it["sentence_zh"] for it in fixed if it["sentence_zh"] != "她很担心考试。"]
        assert context_sentences == ["上下文三。"]
        assert len(fixed) == 2

    def test_no_op_when_already_valid(self):
        items = [
            {"sentence_zh": "上下文。", "target_word": None},
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
            {"sentence_zh": "他每天努力学习。", "target_word": "努力"},
        ]
        fixed = ai._dedupe_consecutive_briefing_context(items, CARDS)
        assert fixed == items

    def test_trailing_context_run_is_kept_as_last_sentence(self):
        items = [
            {"sentence_zh": "她很担心考试。", "target_word": "担心"},
            {"sentence_zh": "尾部上下文一。", "target_word": None},
            {"sentence_zh": "尾部上下文二。", "target_word": None},
        ]
        fixed = ai._dedupe_consecutive_briefing_context(items, CARDS)
        assert [it["sentence_zh"] for it in fixed] == ["她很担心考试。", "尾部上下文二。"]


# ---------------------------------------------------------------------------
# generate_briefing_sentences — validation retry is triggered exactly once
# ---------------------------------------------------------------------------

VALID_ITEMS = [
    {"sentence_zh": "今天天气很好。", "target_word": None, "article_idx": 0},
    {"sentence_zh": "她很担心考试。", "target_word": "担心", "article_idx": 0},
    {"sentence_zh": "他每天努力学习。", "target_word": "努力", "article_idx": 0},
    {"sentence_zh": "她看到了他的进步。", "target_word": "进步", "article_idx": 0},
]

INVALID_ITEMS_CONSECUTIVE_CONTEXT = [
    {"sentence_zh": "今天天气很好。", "target_word": None, "article_idx": 0},
    {"sentence_zh": "很多人出门散步。", "target_word": None, "article_idx": 0},
    {"sentence_zh": "她很担心考试。", "target_word": "担心", "article_idx": 0},
    {"sentence_zh": "他每天努力学习。", "target_word": "努力", "article_idx": 0},
    {"sentence_zh": "她看到了他的进步。", "target_word": "进步", "article_idx": 0},
]


class TestGenerateBriefingSentencesRetry:

    def test_retries_once_on_validation_violation_then_accepts_fixed_result(self):
        """First AI response has consecutive context sentences; the retry
        response is valid. generate_briefing_sentences must call the AI
        exactly twice (initial + one validation retry) and return sentences
        built from the corrected (retry) response."""
        responses = [
            json.dumps(INVALID_ITEMS_CONSECUTIVE_CONTEXT),
            json.dumps(VALID_ITEMS),
        ]
        with patch("ai._call_api", side_effect=responses) as mock_call, \
             patch("ai._fill_translations", lambda sentences, **kw: None):
            result = ai.generate_briefing_sentences(CARDS, ARTICLES, model="gpt-5.1")

        assert mock_call.call_count == 2
        assert len(result) == len(CARDS)
        matched_words = {s["word_ids"][0] for s in result}
        assert matched_words == {c["word_id"] for c in CARDS}

    def test_no_retry_call_when_first_response_already_valid(self):
        """A valid first response must not trigger the extra validation-retry
        API call — only the (separate) missing-word retry loop may still run,
        and here there are no missing words so the loop exits after attempt 1."""
        with patch("ai._call_api", return_value=json.dumps(VALID_ITEMS)) as mock_call, \
             patch("ai._fill_translations", lambda sentences, **kw: None):
            result = ai.generate_briefing_sentences(CARDS, ARTICLES, model="gpt-5.1")

        assert mock_call.call_count == 1
        assert len(result) == len(CARDS)

    def test_persistent_violation_falls_back_to_dedupe_repair(self):
        """If the retry response STILL has consecutive context sentences, the
        dedupe fallback collapses them (dropping extras) instead of failing —
        every card must still end up with exactly one sentence."""
        responses = [
            json.dumps(INVALID_ITEMS_CONSECUTIVE_CONTEXT),
            json.dumps(INVALID_ITEMS_CONSECUTIVE_CONTEXT),
        ]
        with patch("ai._call_api", side_effect=responses) as mock_call, \
             patch("ai._fill_translations", lambda sentences, **kw: None):
            result = ai.generate_briefing_sentences(CARDS, ARTICLES, model="gpt-5.1")

        assert mock_call.call_count == 2
        assert len(result) == len(CARDS)
        matched_words = {s["word_ids"][0] for s in result}
        assert matched_words == {c["word_id"] for c in CARDS}


# ---------------------------------------------------------------------------
# resolve_briefing_model
# ---------------------------------------------------------------------------

class TestResolveBriefingModel:

    def setup_method(self):
        ai._briefing_model_cache = None

    def teardown_method(self):
        ai._briefing_model_cache = None

    def _mock_models_client(self, ids):
        mock_client = MagicMock()
        mock_client.models.list.return_value = MagicMock(
            data=[MagicMock(id=i) for i in ids]
        )
        return mock_client

    def test_uses_requested_model_when_available(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_MODEL", "gpt-5.1")
        with patch("openai.OpenAI", return_value=self._mock_models_client(
            ["gpt-5.1", "gpt-5", "gpt-5-mini"])):
            assert ai.resolve_briefing_model() == "gpt-5.1"

    def test_falls_back_when_requested_model_missing(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_MODEL", "gpt-5.1")
        with patch("openai.OpenAI", return_value=self._mock_models_client(
            ["gpt-5", "gpt-5-mini"])):
            assert ai.resolve_briefing_model() == "gpt-5"

    def test_falls_back_to_mini_when_only_mini_available(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_MODEL", "gpt-5.1")
        with patch("openai.OpenAI", return_value=self._mock_models_client(["gpt-5-mini"])):
            assert ai.resolve_briefing_model() == "gpt-5-mini"

    def test_falls_back_to_last_resort_when_nothing_matches(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_MODEL", "gpt-5.1")
        with patch("openai.OpenAI", return_value=self._mock_models_client(["some-other-model"])):
            assert ai.resolve_briefing_model() == "gpt-5-mini"

    def test_falls_back_to_last_resort_on_api_error(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_MODEL", "gpt-5.1")
        mock_client = MagicMock()
        mock_client.models.list.side_effect = RuntimeError("network down")
        with patch("openai.OpenAI", return_value=mock_client):
            assert ai.resolve_briefing_model() == "gpt-5-mini"

    def test_result_is_cached_across_calls(self, monkeypatch):
        monkeypatch.setenv("BRIEFING_MODEL", "gpt-5.1")
        with patch("openai.OpenAI", return_value=self._mock_models_client(
            ["gpt-5.1"])) as mock_cls:
            first = ai.resolve_briefing_model()
            second = ai.resolve_briefing_model()
        assert first == second == "gpt-5.1"
        mock_cls.assert_called_once()

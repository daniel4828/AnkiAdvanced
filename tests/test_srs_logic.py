"""
Tests for SM-2 scheduling logic in srs.py.

These tests cover pure dict-manipulation functions only — no DB is touched.
We call _handle_learning / _handle_review / _handle_relearn directly with
a minimal card dict so we can assert exact state transitions.
"""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import srs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_PRESET = {
    "learning_steps":      "1 10",
    "graduating_interval": 1,
    "easy_interval":       4,
    "relearning_steps":    "10",
    "minimum_interval":    1,
    "leech_threshold":     8,
    "leech_action":        "suspend",
}

def make_card(state="new", step_index=0, interval=1, ease=2.5, lapses=0,
              repetitions=0):
    return {
        "id": 1,
        "state":        state,
        "step_index":   step_index,
        "interval":     interval,
        "ease":         ease,
        "lapses":       lapses,
        "repetitions":  repetitions,
        "due":          "2026-01-01",
    }


# ---------------------------------------------------------------------------
# calc_review — pure math
# ---------------------------------------------------------------------------

class TestCalcReview:
    def test_again_reduces_ease_and_halves_interval(self):
        interval, ease, lapses = srs.calc_review(10, 2.5, 0, rating=1)
        assert interval == 5          # floor(10 * 0.5)
        assert ease == 2.3            # 2.5 - 0.20
        assert lapses == 1

    def test_again_ease_floor_at_1_3(self):
        _, ease, _ = srs.calc_review(10, 1.4, 0, rating=1)
        assert ease == pytest_approx(1.3)

    def test_hard_reduces_ease_multiplies_1_2(self):
        interval, ease, lapses = srs.calc_review(10, 2.5, 0, rating=2)
        assert interval == 12         # floor(10 * 1.2)
        assert ease == pytest_approx(2.35)  # 2.5 - 0.15
        assert lapses == 0

    def test_good_keeps_ease_multiplies_by_ease(self):
        interval, ease, lapses = srs.calc_review(10, 2.5, 0, rating=3)
        assert interval == 25         # floor(10 * 2.5)
        assert ease == 2.5
        assert lapses == 0

    def test_easy_increases_ease_multiplies_extra(self):
        interval, ease, lapses = srs.calc_review(10, 2.5, 0, rating=4)
        new_ease = 2.5 + 0.15         # = 2.65
        expected_interval = math.floor(10 * new_ease * 1.3)
        assert interval == expected_interval
        assert ease == pytest_approx(new_ease)
        assert lapses == 0

    def test_interval_minimum_1(self):
        # Very short interval should never go below 1
        interval, _, _ = srs.calc_review(1, 1.3, 0, rating=1)
        assert interval >= 1


# ---------------------------------------------------------------------------
# _handle_learning — new card through learning steps
# ---------------------------------------------------------------------------

class TestHandleLearning:
    def test_new_card_good_advances_to_step_1(self):
        card = make_card(state="new", step_index=0)
        result = srs._handle_learning(card, DEFAULT_PRESET, rating=3)
        assert result["state"] == "learning"
        assert result["step_index"] == 1

    def test_learning_step_1_good_graduates(self):
        # step_index=1 is the last step in "1 10", so Good should graduate
        card = make_card(state="learning", step_index=1)
        result = srs._handle_learning(card, DEFAULT_PRESET, rating=3)
        assert result["state"] == "review"
        assert result["interval"] == DEFAULT_PRESET["graduating_interval"]
        assert result["step_index"] == 0
        assert result["repetitions"] == 1

    def test_again_resets_to_step_0(self):
        card = make_card(state="learning", step_index=1)
        result = srs._handle_learning(card, DEFAULT_PRESET, rating=1)
        assert result["state"] == "learning"
        assert result["step_index"] == 0

    def test_easy_graduates_immediately(self):
        card = make_card(state="new", step_index=0)
        result = srs._handle_learning(card, DEFAULT_PRESET, rating=4)
        assert result["state"] == "review"
        assert result["interval"] == DEFAULT_PRESET["easy_interval"]
        assert result["repetitions"] == 1

    def test_hard_at_step_0_uses_avg_delay(self):
        card = make_card(state="new", step_index=0)
        result = srs._handle_learning(card, DEFAULT_PRESET, rating=2)
        assert result["state"] == "learning"
        assert result["step_index"] == 0   # Hard doesn't advance the step


# ---------------------------------------------------------------------------
# _handle_review — review-phase card
# ---------------------------------------------------------------------------

class TestHandleReview:
    def test_good_increases_interval(self):
        card = make_card(state="review", interval=10, ease=2.5)
        result = srs._handle_review(card, DEFAULT_PRESET, rating=3)
        assert result["state"] == "review"
        assert result["interval"] == 25   # floor(10 * 2.5)

    def test_again_lapses_and_enters_relearn(self):
        card = make_card(state="review", interval=10, ease=2.5, lapses=0)
        result = srs._handle_review(card, DEFAULT_PRESET, rating=1)
        assert result["state"] == "relearn"
        assert result["lapses"] == 1
        assert result["step_index"] == 0

    def test_hard_does_not_lapse(self):
        card = make_card(state="review", interval=10, ease=2.5, lapses=0)
        result = srs._handle_review(card, DEFAULT_PRESET, rating=2)
        assert result["state"] == "review"
        assert result["lapses"] == 0

    def test_easy_increases_ease_and_interval(self):
        card = make_card(state="review", interval=10, ease=2.5)
        result = srs._handle_review(card, DEFAULT_PRESET, rating=4)
        assert result["ease"] == pytest_approx(2.65)
        assert result["interval"] > 10

    def test_minimum_interval_respected(self):
        preset = {**DEFAULT_PRESET, "minimum_interval": 3}
        card = make_card(state="review", interval=1, ease=1.3, lapses=0)
        result = srs._handle_review(card, preset, rating=1)
        assert result["interval"] >= 3


# ---------------------------------------------------------------------------
# _handle_relearn — lapsed card relearning
# ---------------------------------------------------------------------------

class TestHandleRelearn:
    def test_good_at_last_step_returns_to_review(self):
        # "10" is a single-step relearn; step_index=0 is the last step
        card = make_card(state="relearn", step_index=0, interval=5)
        result = srs._handle_relearn(card, DEFAULT_PRESET, rating=3)
        assert result["state"] == "review"
        assert result["interval"] == 5   # preserved from before
        assert result["repetitions"] == 1

    def test_again_stays_in_relearn_at_step_0(self):
        card = make_card(state="relearn", step_index=0, interval=5)
        result = srs._handle_relearn(card, DEFAULT_PRESET, rating=1)
        assert result["state"] == "relearn"
        assert result["step_index"] == 0

    def test_easy_returns_to_review_immediately(self):
        card = make_card(state="relearn", step_index=0, interval=5)
        result = srs._handle_relearn(card, DEFAULT_PRESET, rating=4)
        assert result["state"] == "review"


# ---------------------------------------------------------------------------
# _kouyu_hsk_to_int — importer helper
# ---------------------------------------------------------------------------

from importer import _kouyu_hsk_to_int

class TestKouyuHskToInt:
    def test_plain_number(self):
        assert _kouyu_hsk_to_int("4") == 4

    def test_slash_takes_higher(self):
        assert _kouyu_hsk_to_int("4/5") == 5

    def test_chaogong_returns_none(self):
        assert _kouyu_hsk_to_int("超纲") is None

    def test_empty_string_returns_none(self):
        assert _kouyu_hsk_to_int("") is None

    def test_none_input_returns_none(self):
        assert _kouyu_hsk_to_int(None) is None


# ---------------------------------------------------------------------------
# pytest_approx shorthand
# ---------------------------------------------------------------------------

def pytest_approx(val, rel=1e-6):
    """Thin wrapper so tests read naturally."""
    import pytest
    return pytest.approx(val, rel=rel)

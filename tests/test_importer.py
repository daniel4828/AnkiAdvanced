"""
Integration tests for the Kouyu importer and card queue logic.

Each test gets its own isolated in-memory-backed temp DB by monkeypatching
database.DB_PATH before calling any database functions.
"""
import os
import sys
import tempfile
import textwrap
from datetime import date, datetime, timedelta

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database
import importer


# ---------------------------------------------------------------------------
# Fixture: fresh temp DB for each test
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point database.DB_PATH at a temp file and initialise the schema."""
    db_file = str(tmp_path / "test_srs.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    # init_db also creates the data/ dir and default preset+deck
    database.init_db()
    return db_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_yaml(tmp_path, filename, entries):
    kouyu_dir = tmp_path / "Kouyu"
    kouyu_dir.mkdir(parents=True, exist_ok=True)
    path = kouyu_dir / filename
    path.write_text(yaml.dump({"entries": entries}, allow_unicode=True))
    return str(tmp_path)


ENTRY_你好 = {
    "type":        "vocabulary",
    "simplified":  "你好",
    "traditional": "你好",
    "pinyin":      "nǐ hǎo",
    "english":     "hello",
    "pos":         "phrase",
    "hsk":         "1",
    "examples":    [{"zh": "你好！", "pinyin": "nǐ hǎo!"}],
    "characters":  [
        {"char": "你", "pinyin": "nǐ", "hsk": "1", "detailed_analysis": False},
        {"char": "好", "pinyin": "hǎo", "hsk": "1", "detailed_analysis": False},
    ],
}

ENTRY_谢谢 = {
    "type":       "vocabulary",
    "simplified": "谢谢",
    "pinyin":     "xièxie",
    "english":    "thank you",
    "hsk":        "1",
}


# ---------------------------------------------------------------------------
# Importer tests
# ---------------------------------------------------------------------------

class TestImportKouyuYaml:
    def test_imports_word_and_cards(self, tmp_db, tmp_path):
        imports_dir = write_yaml(tmp_path, "1_1.yaml", [ENTRY_你好])
        result = importer.import_all(imports_dir)

        assert result["imported"] == 1
        assert result["skipped_duplicate"] == 0

        word = database.get_word_by_zh("你好")
        assert word is not None
        assert word["pinyin"] == "nǐ hǎo"
        assert word["definition"] == "hello"
        assert word["hsk_level"] == 1

    def test_creates_three_cards_per_word(self, tmp_db, tmp_path):
        imports_dir = write_yaml(tmp_path, "1_1.yaml", [ENTRY_你好])
        importer.import_all(imports_dir)

        word = database.get_word_by_zh("你好")
        cards = database.get_all_cards_for_browse({"deck_id": word["deck_id"]})
        categories = {c["category"] for c in cards}
        assert categories == {"listening", "reading", "creating"}

    def test_duplicate_word_is_skipped(self, tmp_db, tmp_path):
        imports_dir = write_yaml(tmp_path, "1_1.yaml", [ENTRY_你好, ENTRY_你好])
        result = importer.import_all(imports_dir)

        assert result["imported"] == 1
        assert result["skipped_duplicate"] == 1

    def test_imports_examples(self, tmp_db, tmp_path):
        imports_dir = write_yaml(tmp_path, "1_1.yaml", [ENTRY_你好])
        importer.import_all(imports_dir)

        word = database.get_word_by_zh("你好")
        examples = database.get_word_examples(word["id"])
        assert len(examples) == 1
        assert examples[0]["example_zh"] == "你好！"

    def test_imports_characters(self, tmp_db, tmp_path):
        imports_dir = write_yaml(tmp_path, "1_1.yaml", [ENTRY_你好])
        importer.import_all(imports_dir)

        word = database.get_word_by_zh("你好")
        chars = database.get_word_characters(word["id"])
        assert len(chars) == 2
        assert chars[0]["char"] == "你"
        assert chars[1]["char"] == "好"

    def test_non_vocabulary_entries_are_skipped(self, tmp_db, tmp_path):
        entries = [
            {"type": "grammar", "simplified": "把", "english": "BA construction"},
            ENTRY_谢谢,
        ]
        imports_dir = write_yaml(tmp_path, "1_1.yaml", entries)
        result = importer.import_all(imports_dir)

        assert result["imported"] == 1

    def test_import_across_multiple_files(self, tmp_db, tmp_path):
        write_yaml(tmp_path, "1_1.yaml", [ENTRY_你好])
        write_yaml(tmp_path, "1_2.yaml", [ENTRY_谢谢])
        result = importer.import_all(str(tmp_path))

        assert result["imported"] == 2


# ---------------------------------------------------------------------------
# Card queue / due-count tests
# ---------------------------------------------------------------------------

class TestCardQueue:
    def _import_word(self, tmp_path, entry):
        imports_dir = write_yaml(tmp_path, "q.yaml", [entry])
        importer.import_all(imports_dir)
        return database.get_word_by_zh(entry["simplified"])

    def test_new_card_appears_in_due_list(self, tmp_db, tmp_path):
        word = self._import_word(tmp_path, ENTRY_你好)
        cards = database.get_due_cards(word["deck_id"], "listening")
        assert len(cards) == 1
        assert cards[0]["word_zh"] == "你好"
        assert cards[0]["state"] == "new"

    def test_count_due_shows_new_card(self, tmp_db, tmp_path):
        word = self._import_word(tmp_path, ENTRY_你好)
        counts = database.count_due(word["deck_id"], "listening")
        assert counts["new"] == 1
        assert counts["learning"] == 0
        assert counts["review"] == 0

    def test_get_next_card_returns_top_priority(self, tmp_db, tmp_path):
        word = self._import_word(tmp_path, ENTRY_你好)
        card = database.get_next_card(word["deck_id"], "listening")
        assert card is not None
        assert card["word_zh"] == "你好"

    def test_no_cards_due_returns_none(self, tmp_db, tmp_path):
        # No words imported → queue is empty
        deck_id = database.get_default_deck_id()
        card = database.get_next_card(deck_id, "listening")
        assert card is None


# ---------------------------------------------------------------------------
# Queue priority ordering tests
# The spec requires this exact ordering:
#   1. learning/relearn cards with due <= NOW  (intraday)
#   2. review cards with due <= today
#   3. new cards (up to daily limit)
# ---------------------------------------------------------------------------

ENTRY_再见 = {
    "type": "vocabulary", "simplified": "再见", "pinyin": "zàijiàn",
    "english": "goodbye", "hsk": "1",
}
ENTRY_老师 = {
    "type": "vocabulary", "simplified": "老师", "pinyin": "lǎoshī",
    "english": "teacher", "hsk": "1",
}


def _force_card_state(card_id, state, due, step_index=0, interval=1,
                      ease=2.5, repetitions=0, lapses=0):
    """Directly write card state into the DB — lets us simulate time passing."""
    database.update_card(
        card_id, state=state, due=due,
        step_index=step_index, interval=interval,
        ease=ease, repetitions=repetitions, lapses=lapses,
    )


def _get_listening_card_id(word_zh):
    word = database.get_word_by_zh(word_zh)
    cards = database.get_all_cards_for_browse({"deck_id": word["deck_id"],
                                               "category": "listening"})
    return next(c["id"] for c in cards if c["word_zh"] == word_zh)


class TestQueuePriority:
    """
    Tests that get_next_card() respects the priority order defined in CLAUDE.md.
    We import multiple words and manually set their card states to create
    scenarios with a mix of learning/review/new cards in the same queue.
    """

    def _setup_three_words(self, tmp_path):
        """Import 你好, 再见, 老师 into the same deck and return their deck_id."""
        write_yaml(tmp_path, "p.yaml", [ENTRY_你好, ENTRY_再见, ENTRY_老师])
        importer.import_all(str(tmp_path))
        word = database.get_word_by_zh("你好")
        return word["deck_id"]

    def test_learning_card_beats_new_card(self, tmp_db, tmp_path):
        """A learning card that is now overdue should come before a new card."""
        deck_id = self._setup_three_words(tmp_path)

        # Force 你好's card into learning state with a due time 1 second ago
        card_id = _get_listening_card_id("你好")
        past = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        _force_card_state(card_id, state="learning", due=past)

        # 再见 and 老师 remain new
        next_card = database.get_next_card(deck_id, "listening")
        assert next_card["id"] == card_id, \
            "Overdue learning card should come before new cards"
        assert next_card["state"] == "learning"

    def test_review_card_beats_new_card(self, tmp_db, tmp_path):
        """A review card due today should come before a new card."""
        deck_id = self._setup_three_words(tmp_path)

        card_id = _get_listening_card_id("你好")
        today = date.today().isoformat()
        _force_card_state(card_id, state="review", due=today, interval=1)

        next_card = database.get_next_card(deck_id, "listening")
        assert next_card["id"] == card_id, \
            "Review card due today should come before new cards"
        assert next_card["state"] == "review"

    def test_learning_card_beats_review_card(self, tmp_db, tmp_path):
        """An overdue learning card should come before a review card due today."""
        deck_id = self._setup_three_words(tmp_path)

        # 你好 → overdue learning
        learning_id = _get_listening_card_id("你好")
        past = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        _force_card_state(learning_id, state="learning", due=past)

        # 再见 → review due today
        review_id = _get_listening_card_id("再见")
        today = date.today().isoformat()
        _force_card_state(review_id, state="review", due=today, interval=1)

        # 老师 stays new
        next_card = database.get_next_card(deck_id, "listening")
        assert next_card["id"] == learning_id, \
            "Overdue learning card should beat review card"

    def test_future_learning_card_does_not_appear(self, tmp_db, tmp_path):
        """
        A card rated Again gets due=now+1min (future).
        It must NOT appear in the queue yet — the user should see other cards first.
        """
        deck_id = self._setup_three_words(tmp_path)

        # Put 你好 in learning but due 2 minutes in the FUTURE
        card_id = _get_listening_card_id("你好")
        future = (datetime.now() + timedelta(minutes=2)).isoformat(timespec="seconds")
        _force_card_state(card_id, state="learning", due=future)

        # Queue should only contain 再见 and 老师 (new), not 你好
        due_cards = database.get_due_cards(deck_id, "listening")
        due_ids = [c["id"] for c in due_cards]
        assert card_id not in due_ids, \
            "Future-due learning card must not appear in queue yet"

    def test_again_card_reappears_at_top_after_delay(self, tmp_db, tmp_path):
        """
        Full Again flow:
        1. Card A is rated Again → goes to learning, due = now+1min (not visible)
        2. Time passes (simulated by setting due to past)
        3. Card A reappears and is first in queue, even with new cards still waiting
        """
        deck_id = self._setup_three_words(tmp_path)

        card_a_id = _get_listening_card_id("你好")

        # Step 1: simulate rating Again — card goes to learning, due is in the future
        future = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
        _force_card_state(card_a_id, state="learning", due=future, step_index=0)

        # At this point card A should NOT be in the queue
        due_now = database.get_due_cards(deck_id, "listening")
        assert card_a_id not in [c["id"] for c in due_now], \
            "Card should not appear while its due time is still in the future"

        # Step 2: simulate 1 minute passing — set due to 1 second ago
        past = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        _force_card_state(card_a_id, state="learning", due=past, step_index=0)

        # Step 3: card A should now be first in queue (priority 1 beats new cards)
        next_card = database.get_next_card(deck_id, "listening")
        assert next_card["id"] == card_a_id, \
            "After 1 min delay, Again card should jump to front of queue"
        assert next_card["state"] == "learning"

    def test_relearn_card_also_gets_top_priority(self, tmp_db, tmp_path):
        """
        A review card rated Again enters 'relearn' state.
        Once its relearn step time has passed, it should have the same
        top priority as a learning card.
        """
        deck_id = self._setup_three_words(tmp_path)

        card_id = _get_listening_card_id("你好")
        past = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        _force_card_state(card_id, state="relearn", due=past, interval=5, lapses=1)

        next_card = database.get_next_card(deck_id, "listening")
        assert next_card["id"] == card_id
        assert next_card["state"] == "relearn"

    def test_suspended_card_never_appears(self, tmp_db, tmp_path):
        """Suspended cards must never enter the queue regardless of due date."""
        deck_id = self._setup_three_words(tmp_path)

        card_id = _get_listening_card_id("你好")
        # Suspend the card
        database.suspend_card(card_id)

        due_cards = database.get_due_cards(deck_id, "listening")
        assert card_id not in [c["id"] for c in due_cards], \
            "Suspended card must never appear in queue"


# ---------------------------------------------------------------------------
# Multiple Again / relearn cards in the queue simultaneously
#
# Key rule from the SQL in get_due_cards():
#   - 'learning' and 'relearn' share priority bucket 0
#   - Within that bucket, cards are ordered by due ASC (earliest first)
#   - So the card whose timer expired first always comes back first
# ---------------------------------------------------------------------------

class TestMultipleAgainCards:
    """
    Tests for when more than one card has been clicked Again (or has lapsed)
    and multiple learning/relearn cards are waiting in the queue at the same time.
    """

    def _setup_three_words(self, tmp_path):
        write_yaml(tmp_path, "m.yaml", [ENTRY_你好, ENTRY_再见, ENTRY_老师])
        importer.import_all(str(tmp_path))
        return database.get_word_by_zh("你好")["deck_id"]

    def test_two_again_cards_returned_in_due_order(self, tmp_db, tmp_path):
        """
        You click Again on two cards. Both 1-minute timers expire.
        The card whose timer expired FIRST (earlier due timestamp) comes back first.
        """
        deck_id = self._setup_three_words(tmp_path)

        id_a = _get_listening_card_id("你好")
        id_b = _get_listening_card_id("再见")

        # 你好 expired 2 seconds ago, 再见 expired 1 second ago
        # → 你好 has the earlier due time, so it comes first
        due_a = (datetime.now() - timedelta(seconds=2)).isoformat(timespec="seconds")
        due_b = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        _force_card_state(id_a, state="learning", due=due_a)
        _force_card_state(id_b, state="learning", due=due_b)

        cards = database.get_due_cards(deck_id, "listening")
        learning_ids = [c["id"] for c in cards if c["state"] == "learning"]

        assert learning_ids[0] == id_a, "Earlier-due Again card should come first"
        assert learning_ids[1] == id_b

    def test_two_relearn_cards_returned_in_due_order(self, tmp_db, tmp_path):
        """
        Same as above but both cards are in 'relearn' state (they had already
        graduated to review before lapsing). Earlier due still comes first.
        """
        deck_id = self._setup_three_words(tmp_path)

        id_a = _get_listening_card_id("你好")
        id_b = _get_listening_card_id("再见")

        due_a = (datetime.now() - timedelta(seconds=2)).isoformat(timespec="seconds")
        due_b = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        _force_card_state(id_a, state="relearn", due=due_a, interval=5, lapses=1)
        _force_card_state(id_b, state="relearn", due=due_b, interval=3, lapses=1)

        cards = database.get_due_cards(deck_id, "listening")
        relearn_ids = [c["id"] for c in cards if c["state"] == "relearn"]

        assert relearn_ids[0] == id_a
        assert relearn_ids[1] == id_b

    def test_learning_and_relearn_share_same_priority_bucket(self, tmp_db, tmp_path):
        """
        A 'relearn' card due BEFORE a 'learning' card should come first —
        they are in the same priority bucket, ordered purely by due time.
        """
        deck_id = self._setup_three_words(tmp_path)

        learning_id = _get_listening_card_id("你好")   # learning, due 1s ago
        relearn_id  = _get_listening_card_id("再见")   # relearn,  due 2s ago

        due_later   = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        due_earlier = (datetime.now() - timedelta(seconds=2)).isoformat(timespec="seconds")
        _force_card_state(learning_id, state="learning", due=due_later)
        _force_card_state(relearn_id,  state="relearn",  due=due_earlier, interval=5, lapses=1)

        next_card = database.get_next_card(deck_id, "listening")
        assert next_card["id"] == relearn_id, \
            "Relearn card due earlier should beat learning card due later"

    def test_again_cards_interleaved_with_new_cards(self, tmp_db, tmp_path):
        """
        Realistic session: one Again card (timer now expired) and two new cards
        still waiting. The Again card must always come before the new ones,
        no matter how many new cards are in the queue.
        """
        deck_id = self._setup_three_words(tmp_path)

        again_id = _get_listening_card_id("你好")
        past = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        _force_card_state(again_id, state="learning", due=past)

        # 再见 and 老师 are still new
        cards = database.get_due_cards(deck_id, "listening")
        assert cards[0]["id"] == again_id, \
            "Again card must be at position 0, before all new cards"
        assert cards[0]["state"] == "learning"
        # New cards follow behind
        remaining_states = [c["state"] for c in cards[1:]]
        assert all(s == "new" for s in remaining_states)

    def test_only_expired_again_cards_are_visible(self, tmp_db, tmp_path):
        """
        You click Again on two cards at different times.
        Only the card whose timer has already expired is visible;
        the other is still hidden (due in the future).
        """
        deck_id = self._setup_three_words(tmp_path)

        id_expired = _get_listening_card_id("你好")
        id_waiting  = _get_listening_card_id("再见")

        past   = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
        future = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
        _force_card_state(id_expired, state="learning", due=past)
        _force_card_state(id_waiting,  state="learning", due=future)

        due_cards = database.get_due_cards(deck_id, "listening")
        due_ids = [c["id"] for c in due_cards]

        assert id_expired in due_ids,  "Expired Again card should be visible"
        assert id_waiting  not in due_ids, "Not-yet-due Again card should be hidden"

"""
Integration tests for the Kouyu importer and card queue logic.

Each test gets its own isolated in-memory-backed temp DB by monkeypatching
database.DB_PATH before calling any database functions.
"""
import os
import sys
import tempfile
import textwrap

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

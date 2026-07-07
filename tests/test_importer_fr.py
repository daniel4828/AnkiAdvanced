"""
Integration tests for the French YAML import format (issue #430).

Each test gets its own isolated temp DB. Note: unlike tests/test_importer.py's
`tmp_db` fixture (which monkeypatches the `database` package attribute —
a no-op, since database.get_db() actually reads the module-level DB_PATH
bound inside database/core.py's own namespace), this file monkeypatches
`database.core.DB_PATH` directly so each test truly gets a fresh file.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database
import database.core as db_core
import importer

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "francais_test.yaml")

SCRATCHPAD = ("/private/tmp/claude-501/-Users-daniel-Documents-AnkiAdvanced/"
              "30a5ad06-70ac-41c2-bff5-48df913a3af3/scratchpad")


@pytest.fixture
def tmp_db(monkeypatch):
    """Point database.core.DB_PATH at a fresh scratchpad file and init the schema."""
    os.makedirs(SCRATCHPAD, exist_ok=True)
    db_file = os.path.join(SCRATCHPAD, f"test_importer_fr_{uuid.uuid4().hex}.db")
    monkeypatch.setattr(db_core, "DB_PATH", db_file)
    database.init_db()
    yield db_file
    if os.path.exists(db_file):
        os.remove(db_file)


class TestFrenchImport:
    def test_import_produces_three_entries_with_fr_lang(self, tmp_db):
        result = importer.import_yaml_file(FIXTURE_PATH, ["Francais"])
        assert result["imported"] == 3, result

        for word_zh in ("le boulanger", "la confiture", "Je n'en reviens pas."):
            entry = database.get_word_by_zh(word_zh)
            assert entry is not None, f"missing entry: {word_zh!r}"
            assert entry["lang"] == "fr"

    def test_decks_are_created_with_fr_lang(self, tmp_db):
        importer.import_yaml_file(FIXTURE_PATH, ["Francais"])

        conn = database.get_db()
        rows = conn.execute("SELECT name, lang FROM decks WHERE deleted_at IS NULL").fetchall()
        conn.close()
        by_name = {r["name"]: r["lang"] for r in rows}

        assert by_name.get("Francais") == "fr"
        assert by_name.get("Francais · Listening") == "fr"
        assert by_name.get("Francais · Reading") == "fr"
        assert by_name.get("Francais · Creating") == "fr"

    def test_three_cards_per_entry(self, tmp_db):
        importer.import_yaml_file(FIXTURE_PATH, ["Francais"])

        entry = database.get_word_by_zh("le boulanger")
        cards = database.get_all_cards_for_browse({})
        my_cards = [c for c in cards if c["word_id"] == entry["id"]]
        assert len(my_cards) == 3
        categories = {c["category"] for c in my_cards}
        assert categories == {"listening", "reading", "creating"}
        # Reading is suspended by default, listening/creating are not
        reading_card = next(c for c in my_cards if c["category"] == "reading")
        assert reading_card["state"] == "suspended"

    def test_examples_hold_french_and_german_text(self, tmp_db):
        importer.import_yaml_file(FIXTURE_PATH, ["Francais"])

        entry = database.get_word_by_zh("le boulanger")
        examples = database.get_word_examples(entry["id"])
        assert len(examples) == 2
        assert examples[0]["example_zh"] == "Le boulanger ouvre à sept heures."
        assert examples[0]["example_de"] == "Der Bäcker öffnet um sieben Uhr."
        assert examples[0]["example_en"] == "The baker opens at seven o'clock."

    def test_sentence_keeps_trailing_period(self, tmp_db):
        importer.import_yaml_file(FIXTURE_PATH, ["Francais"])

        entry = database.get_word_by_zh("Je n'en reviens pas.")
        assert entry is not None
        assert entry["word_zh"] == "Je n'en reviens pas."
        assert entry["note_type"] == "sentence"

    def test_reimport_is_idempotent(self, tmp_db):
        first = importer.import_yaml_file(FIXTURE_PATH, ["Francais"])
        assert first["imported"] == 3

        second = importer.import_yaml_file(FIXTURE_PATH, ["Francais"])
        assert second["imported"] == 0
        assert second["skipped_duplicate"] == 3


class TestChineseImportUnchanged:
    """Importing a YAML file with no `lang:` header must still behave as zh."""

    def test_no_lang_header_defaults_to_zh_everywhere(self, tmp_db, tmp_path):
        entries = [
            {
                "type": "vocabulary",
                "simplified": "你好",
                "traditional": "你好",
                "pinyin": "nǐ hǎo",
                "english": "hello",
                "hsk": "1",
            },
            {
                "type": "sentence",
                "simplified": "你好吗……",
                "english": "how are you",
            },
        ]
        import yaml
        kouyu_dir = tmp_path / "Kouyu"
        kouyu_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = kouyu_dir / "1_1.yaml"
        yaml_path.write_text(yaml.dump({"entries": entries}, allow_unicode=True))

        result = importer.import_yaml_file(str(yaml_path), ["Kouyu"])
        assert result["imported"] == 2, result

        entry = database.get_word_by_zh("你好")
        assert entry is not None
        assert entry["lang"] == "zh"

        # Chinese ellipsis-stripping behavior must be unchanged: "你好吗……" → "你好吗"
        stripped_entry = database.get_word_by_zh("你好吗")
        assert stripped_entry is not None
        assert stripped_entry["lang"] == "zh"

        conn = database.get_db()
        rows = conn.execute("SELECT name, lang FROM decks WHERE deleted_at IS NULL").fetchall()
        conn.close()
        by_name = {r["name"]: r["lang"] for r in rows}
        assert by_name.get("Kouyu") == "zh"
        assert by_name.get("Kouyu · Listening") == "zh"

import glob
import json
import os

import yaml

import database


def import_all(imports_dir: str = "imports") -> dict:
    """Scan imports/Kouyu/*.yaml and import each file."""
    pattern = os.path.join(imports_dir, "Kouyu", "*.yaml")
    files = sorted(glob.glob(pattern))
    total_imported = 0
    total_skipped = 0
    for filepath in files:
        result = import_kouyu_yaml(filepath)
        total_imported += result["imported"]
        total_skipped += result["skipped_duplicate"]
    return {"imported": total_imported, "skipped_duplicate": total_skipped}


def import_kouyu_yaml(filepath: str) -> dict:
    """Parse one Kouyu YAML file and import all entries."""
    # Ensure parent deck + sub-decks exist
    parent_id = database.get_or_create_deck("Kouyu")
    deck_ids = {
        "listening": database.get_or_create_deck(
            "Kouyu · Listening", parent_id=parent_id, category="listening"
        ),
        "reading": database.get_or_create_deck(
            "Kouyu · Reading", parent_id=parent_id, category="reading"
        ),
        "creating": database.get_or_create_deck(
            "Kouyu · Creating", parent_id=parent_id, category="creating"
        ),
    }

    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    entries = data.get("entries", [])
    imported = 0
    skipped_duplicate = 0

    for entry in entries:
        if entry.get("type") != "vocabulary":
            continue

        word_zh = entry.get("simplified", "").strip()
        if not word_zh:
            continue

        word = {
            "word_zh":       word_zh,
            "pinyin":        entry.get("pinyin"),
            "definition":    entry.get("english"),
            "pos":           entry.get("pos"),
            "hsk_level":     _kouyu_hsk_to_int(entry.get("hsk", "")),
            "traditional":   entry.get("traditional"),
            "definition_zh": entry.get("definition_zh"),
            "source":        "kouyu",
        }

        existing = database.get_word_by_zh(word_zh)
        word_id = database.insert_word(word)

        if existing:
            skipped_duplicate += 1
            continue

        # Examples
        for i, ex in enumerate(entry.get("examples") or []):
            database.insert_word_example(
                word_id=word_id,
                example_zh=ex.get("zh", ""),
                example_pinyin=ex.get("pinyin"),
                example_de=ex.get("de"),
                position=i,
            )

        # Characters
        for pos, char_entry in enumerate(entry.get("characters") or []):
            char_text = char_entry.get("char", "").strip()
            if not char_text:
                continue

            detailed = char_entry.get("detailed_analysis", False)

            other_meanings = char_entry.get("other_meanings")
            compounds_raw = char_entry.get("compounds")

            other_meanings_json = json.dumps(other_meanings, ensure_ascii=False) \
                if other_meanings else None
            compounds_json = json.dumps(compounds_raw, ensure_ascii=False) \
                if compounds_raw else None

            char_dict = {
                "char":           char_text,
                "traditional":    char_entry.get("traditional"),
                "pinyin":         char_entry.get("pinyin"),
                "hsk_level":      _kouyu_hsk_to_int(str(char_entry.get("hsk", ""))),
                "etymology":      char_entry.get("etymology") if detailed else None,
                "other_meanings": other_meanings_json,
                "compounds":      compounds_json,
            }

            char_id = database.upsert_character(char_dict)
            database.insert_word_character(
                word_id=word_id,
                char_id=char_id,
                position=pos,
                meaning_in_context=char_entry.get("meaning_in_context") if detailed else None,
            )

        _create_cards(word_id, deck_ids)
        imported += 1

    return {"imported": imported, "skipped_duplicate": skipped_duplicate}


def _create_cards(word_id: int, deck_ids: dict) -> None:
    """Create one card per category, each in its respective sub-deck."""
    for category, deck_id in deck_ids.items():
        database.insert_card(word_id, category, deck_id, state="new")


def _kouyu_hsk_to_int(hsk_str: str) -> int | None:
    """Convert HSK string to int.

    '超纲' → None
    '6' → 6
    """
    if not hsk_str:
        return None
    s = str(hsk_str).strip()
    if s == "超纲" or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None

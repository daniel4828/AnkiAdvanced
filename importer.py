import glob
import json
import logging
import os
import re

import yaml

import database

logger = logging.getLogger(__name__)


def import_all(imports_dir: str = "imports") -> dict:
    """Recursively scan imports/<Source>/<optional subdirs>/*.yaml.

    Folder nesting maps directly to deck nesting, e.g.:
      imports/Kouyu/Chapter 1/file.yaml
        → Kouyu > Kouyu · Chapter 1 > Kouyu · Chapter 1 · Listening / Reading / Creating
    """
    total_imported = 0
    total_skipped = 0
    total_invalid = 0
    for source_dir in sorted(os.scandir(imports_dir), key=lambda e: e.name):
        if not source_dir.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(source_dir.path):
            dirnames.sort()
            for filename in sorted(f for f in filenames if f.endswith(".yaml")):
                filepath = os.path.join(dirpath, filename)
                rel = os.path.relpath(dirpath, imports_dir)
                deck_path = rel.replace("\\", "/").split("/")
                result = import_yaml_file(filepath, deck_path)
                total_imported += result["imported"]
                total_skipped += result["skipped_duplicate"]
                total_invalid += result["skipped_invalid"]
    return {"imported": total_imported, "skipped_duplicate": total_skipped,
            "skipped_invalid": total_invalid}


def import_kouyu_yaml(filepath: str) -> dict:
    """Kept for backwards compatibility."""
    return import_yaml_file(filepath, ["Kouyu"])


def import_yaml_file(filepath: str, deck_path: list[str]) -> dict:
    """Parse one YAML vocabulary file. deck_path is the folder hierarchy,
    e.g. ["Kouyu"] or ["Kouyu", "Chapter 1"].

    Intermediate decks are named by joining parts with ' · ':
      ["Kouyu", "Chapter 1"] → creates "Kouyu" then "Kouyu · Chapter 1"
    Category leaf decks are created under the deepest intermediate deck.
    """
    # Build intermediate deck hierarchy — each deck is named by its own segment only
    parent_id = None
    for segment in deck_path:
        parent_id = database.get_or_create_deck(segment, parent_id=parent_id)

    leaf_parent = deck_path[-1]
    deck_ids = {
        "listening": database.get_or_create_deck(
            f"{leaf_parent} · Listening", parent_id=parent_id, category="listening"
        ),
        "reading": database.get_or_create_deck(
            f"{leaf_parent} · Reading", parent_id=parent_id, category="reading"
        ),
        "creating": database.get_or_create_deck(
            f"{leaf_parent} · Creating", parent_id=parent_id, category="creating"
        ),
    }

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error("YAML parse error in %s: %s", filepath, e)
        return {"imported": 0, "skipped_duplicate": 0}

    entries = data.get("entries", [])
    imported = 0
    skipped_duplicate = 0
    skipped_invalid = 0

    for entry in entries:
        if entry.get("type") != "vocabulary":
            continue

        word_zh = entry.get("simplified", "").strip()
        if not word_zh:
            continue

        warning = _validate_word(word_zh, filepath)
        if warning:
            skipped_invalid += 1
            continue

        word = {
            "word_zh":       word_zh,
            "pinyin":        entry.get("pinyin"),
            "definition":    entry.get("english"),
            "pos":           entry.get("pos"),
            "hsk_level":     _hsk_to_int(entry.get("hsk", "")),
            "traditional":   entry.get("traditional"),
            "definition_zh": entry.get("definition_zh"),
            "source":        deck_path[0].lower(),
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
            compounds_raw  = char_entry.get("compounds")

            char_dict = {
                "char":           char_text,
                "traditional":    char_entry.get("traditional"),
                "pinyin":         char_entry.get("pinyin"),
                "hsk_level":      _hsk_to_int(str(char_entry.get("hsk", ""))),
                "etymology":      char_entry.get("etymology") if detailed else None,
                "other_meanings": json.dumps(other_meanings, ensure_ascii=False)
                                  if other_meanings else None,
                "compounds":      json.dumps(compounds_raw, ensure_ascii=False)
                                  if compounds_raw else None,
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

    return {"imported": imported, "skipped_duplicate": skipped_duplicate,
            "skipped_invalid": skipped_invalid}


_HANZI_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')


def _validate_word(word_zh: str, filepath: str) -> str | None:
    """Return a warning string if the entry looks invalid and should be skipped, else None."""
    if '/' in word_zh or '／' in word_zh:
        msg = f"SKIP  {os.path.basename(filepath)}: slash in word (multiple entries combined): {word_zh!r}"
        logger.warning(msg)
        return msg
    if '。' in word_zh or '. ' in word_zh:
        msg = f"SKIP  {os.path.basename(filepath)}: period in word (looks like a sentence): {word_zh!r}"
        logger.warning(msg)
        return msg
    hanzi_count = len(_HANZI_RE.findall(word_zh))
    if hanzi_count > 6:
        msg = f"SKIP  {os.path.basename(filepath)}: {hanzi_count} hanzi (looks like a phrase, max 6): {word_zh!r}"
        logger.warning(msg)
        return msg
    return None


def _create_cards(word_id: int, deck_ids: dict) -> None:
    """Create one card per category, each in its respective sub-deck."""
    for category, deck_id in deck_ids.items():
        database.insert_card(word_id, category, deck_id, state="new")


def _hsk_to_int(hsk_str: str) -> int | None:
    if not hsk_str:
        return None
    s = str(hsk_str).strip()
    if s in ("超纲", ""):
        return None
    try:
        return int(s)
    except ValueError:
        return None


# Keep old name as alias
_kouyu_hsk_to_int = _hsk_to_int

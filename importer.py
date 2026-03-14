"""Importers for known-vocabulary CSVs and new-word YAML files."""
import csv
import json
import re
from pathlib import Path

import database

IMPORTS_DIR = Path(__file__).parent / "imports"


def clean_pinyin(raw: str) -> str:
    return raw.replace("&#8239;", " ").strip()


def clean_example(raw: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", raw).strip()


def import_language_reactor_csv(csv_path: Path) -> dict:
    imported = 0
    skipped = 0

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 5:
                skipped += 1
                continue

            word_zh = row[4].strip()
            if not word_zh:
                skipped += 1
                continue

            pinyin = clean_pinyin(row[12]) if len(row) > 12 else ""
            definition = row[8].strip() if len(row) > 8 else ""
            pos = row[6].strip() if len(row) > 6 else ""
            example_zh = clean_example(row[2]) if len(row) > 2 else ""
            example_en = clean_example(row[3]) if len(row) > 3 else ""
            date_added = row[17].strip() if len(row) > 17 else ""

            try:
                frequency = int(row[14]) if len(row) > 14 and row[14].strip() else 0
            except ValueError:
                frequency = 0

            database.insert_word(
                word_zh=word_zh, pinyin=pinyin, definition=definition, pos=pos,
                frequency=frequency, example_zh=example_zh, example_en=example_en,
                date_added=date_added, known=1,
            )
            imported += 1

    return {"imported": imported, "skipped": skipped}


def import_hsk_csv(csv_path: Path, hsk_level: int) -> dict:
    """Format: word_zh,pinyin,definition (no header)"""
    imported = 0
    skipped = 0

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3:
                skipped += 1
                continue
            word_zh = row[0].strip()
            if not word_zh:
                skipped += 1
                continue
            database.insert_word(
                word_zh=word_zh,
                pinyin=row[1].strip(),
                definition=row[2].strip(),
                pos="", frequency=0, example_zh="", example_en="", date_added="",
                known=1, hsk_level=hsk_level,
            )
            imported += 1

    return {"imported": imported, "skipped": skipped}


def _parse_hsk_level(raw) -> int:
    """Convert HSK string like '3', '5-6', '超纲' to an integer."""
    if raw is None:
        return 5
    s = str(raw).strip()
    if s.isdigit():
        return int(s)
    if "-" in s:
        return int(s.split("-")[0])
    return 7  # 超纲 or unknown → above HSK 6


def import_yaml(yaml_path: Path) -> dict:
    """Import new words to learn from a YAML file. Creates cards for each word."""
    import yaml
    imported = 0
    skipped = 0

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    for entry in data.get("entries", []):
        if entry.get("type") != "vocabulary":
            skipped += 1
            continue

        word_zh = entry.get("simplified", "").strip()
        if not word_zh:
            skipped += 1
            continue

        examples = entry.get("examples", [])
        first_ex = examples[0] if examples else {}
        hsk_level = _parse_hsk_level(entry.get("hsk"))

        word_id = database.insert_word(
            word_zh=word_zh,
            traditional=entry.get("traditional"),
            pinyin=entry.get("pinyin", ""),
            definition=entry.get("english", ""),
            definition_zh=entry.get("definition_zh"),
            cultural_note=entry.get("cultural_note"),
            pos=entry.get("pos", ""),
            frequency=0,
            example_zh=first_ex.get("zh", ""),
            example_en=first_ex.get("de", ""),
            date_added="",
            source="yaml",
            known=0,
            hsk_level=hsk_level,
        )

        # Store all examples
        for ex in examples:
            database.insert_word_example(
                word_id=word_id,
                zh=ex.get("zh", ""),
                pinyin=ex.get("pinyin", ""),
                translation=ex.get("de", ""),
            )

        # Store character breakdowns
        for ch in entry.get("characters", []):
            other_meanings = ch.get("other_meanings", [])
            char_id = database.insert_word_character(
                word_id=word_id,
                char=ch.get("char", ""),
                traditional=ch.get("traditional"),
                pinyin=ch.get("pinyin", ""),
                hsk=str(ch.get("hsk", "")),
                detailed_analysis=ch.get("detailed_analysis", False),
                meaning_in_context=ch.get("meaning_in_context"),
                other_meanings=json.dumps(other_meanings, ensure_ascii=False) if other_meanings else None,
                etymology=ch.get("etymology"),
                etymology_example=json.dumps(ch["etymology_example"], ensure_ascii=False) if isinstance(ch.get("etymology_example"), dict) else ch.get("etymology_example"),
                note=ch.get("note"),
            )
            for compound in ch.get("compounds", []):
                database.insert_character_compound(
                    character_id=char_id,
                    simplified=compound.get("simplified", ""),
                    pinyin=compound.get("pinyin", ""),
                    meaning=compound.get("meaning", ""),
                )

        # Create review cards
        database.insert_card(word_id, "listening")
        database.insert_card(word_id, "reading")
        database.insert_card(word_id, "creating", state="locked")

        imported += 1

    return {"imported": imported, "skipped": skipped}


def run_import() -> dict:
    totals = {"known": 0, "to_learn": 0, "skipped": 0}

    # Language Reactor TSVs (known vocab)
    for csv_path in sorted(IMPORTS_DIR.glob("lln_*.csv")):
        result = import_language_reactor_csv(csv_path)
        totals["known"] += result["imported"]
        totals["skipped"] += result["skipped"]
        print(f"  {csv_path.name}: {result['imported']} known words")

    # HSK CSVs (known vocab)
    for level in range(1, 7):
        csv_path = IMPORTS_DIR / f"hsk{level}.csv"
        if csv_path.exists():
            result = import_hsk_csv(csv_path, hsk_level=level)
            totals["known"] += result["imported"]
            totals["skipped"] += result["skipped"]
            print(f"  hsk{level}.csv: {result['imported']} known words")

    # YAML files (new words to learn)
    for yaml_path in sorted(IMPORTS_DIR.glob("*.yaml")):
        result = import_yaml(yaml_path)
        totals["to_learn"] += result["imported"]
        totals["skipped"] += result["skipped"]
        print(f"  {yaml_path.name}: {result['imported']} words to learn")

    if totals["known"] + totals["to_learn"] == 0:
        return {"error": f"No import files found in {IMPORTS_DIR}"}

    return totals

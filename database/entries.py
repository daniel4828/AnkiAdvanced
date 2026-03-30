import sqlite3
from .core import get_db


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def insert_word(word: dict) -> int:
    """INSERT OR IGNORE. Returns the word id whether inserted or already existed.
    For existing entries, also backfills notes and date_yaml if previously empty."""
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO entries
           (word_zh, pinyin, definition, pos, hsk_level,
            traditional, definition_zh, source, note_type,
            notes, date_yaml, source_sentence, grammar_notes, register, definition_de)
           VALUES (:word_zh, :pinyin, :definition, :pos, :hsk_level,
                   :traditional, :definition_zh, :source, :note_type,
                   :notes, :date_yaml, :source_sentence, :grammar_notes, :register, :definition_de)""",
        {
            **word,
            "note_type":       word.get("note_type", "vocabulary"),
            "notes":           word.get("notes"),
            "date_yaml":       word.get("date_yaml"),
            "source_sentence": word.get("source_sentence"),
            "grammar_notes":   word.get("grammar_notes"),
            "register":        word.get("register"),
            "definition_de":   word.get("definition_de"),
        },
    )
    # Backfill notes / date_yaml for entries that existed before these fields were added
    if word.get("notes"):
        conn.execute(
            "UPDATE entries SET notes = ? WHERE word_zh = ? AND (notes IS NULL OR notes = '')",
            (word["notes"], word["word_zh"]),
        )
    if word.get("date_yaml"):
        conn.execute(
            "UPDATE entries SET date_yaml = ? WHERE word_zh = ? AND date_yaml IS NULL",
            (word["date_yaml"], word["word_zh"]),
        )
    conn.commit()
    row = conn.execute("SELECT id FROM entries WHERE word_zh = ?", (word["word_zh"],)).fetchone()
    conn.close()
    return row["id"]


def get_word(word_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (word_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_word_by_zh(word_zh: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM entries WHERE word_zh = ?", (word_zh,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_words_in_deck(deck_id: int) -> list[dict]:
    """Words that have at least one card in this deck."""
    conn = get_db()
    rows = conn.execute(
        """SELECT DISTINCT w.* FROM entries w
           JOIN cards c ON c.word_id = w.id
           WHERE c.deck_id = ?""",
        (deck_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_word_full(word_id: int) -> dict | None:
    """Returns word + examples + characters + measure_words + relations + components."""
    word = get_word(word_id)
    if not word:
        return None
    word["examples"] = get_word_examples(word_id)
    word["characters"] = get_word_characters(word_id)
    word["measure_words"] = get_word_measure_words(word_id)
    word["relations"] = get_word_relations(word_id)
    word["components"] = get_note_components(word_id)
    return word


def word_has_cards(word_id: int) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM cards WHERE word_id = ? AND deleted_at IS NULL LIMIT 1", (word_id,)
    ).fetchone()
    conn.close()
    return row is not None


def insert_note_component(note_id: int, word_id: int, position: int) -> None:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO entry_components (note_id, word_id, position) VALUES (?, ?, ?)",
        (note_id, word_id, position),
    )
    conn.commit()
    conn.close()


def get_note_components(note_id: int) -> list[dict]:
    """Return component words for a sentence/chengyu note, with their character data."""
    conn = get_db()
    rows = conn.execute(
        """SELECT nc.position, w.*
           FROM entry_components nc
           JOIN entries w ON w.id = nc.word_id
           WHERE nc.note_id = ?
           ORDER BY nc.position""",
        (note_id,),
    ).fetchall()
    conn.close()
    components = []
    for row in rows:
        comp = dict(row)
        comp["characters"] = get_word_characters(comp["id"])
        comp["examples"] = get_word_examples(comp["id"])
        comp["measure_words"] = get_word_measure_words(comp["id"])
        components.append(comp)
    return components


# ---------------------------------------------------------------------------
# Word examples
# ---------------------------------------------------------------------------

def insert_word_example(word_id: int, example_zh: str,
                        example_pinyin: str | None,
                        example_en: str | None,
                        example_de: str | None,
                        position: int,
                        example_type: str = "example") -> int:
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM entry_examples WHERE word_id = ? AND example_zh = ?",
        (word_id, example_zh),
    ).fetchone()
    if existing:
        conn.close()
        return existing["id"]
    cur = conn.execute(
        """INSERT INTO entry_examples
           (word_id, example_zh, example_pinyin, example_en, example_de, position, example_type)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (word_id, example_zh, example_pinyin, example_en, example_de, position, example_type),
    )
    conn.commit()
    ex_id = cur.lastrowid
    conn.close()
    return ex_id


def insert_word_measure_word(word_id: int, measure_zh: str,
                             pinyin: str | None,
                             meaning: str | None,
                             position: int) -> None:
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO entry_measure_words
           (word_id, measure_zh, pinyin, meaning, position)
           VALUES (?, ?, ?, ?, ?)""",
        (word_id, measure_zh, pinyin, meaning, position),
    )
    conn.commit()
    conn.close()


def insert_word_relation(word_id: int, related_zh: str,
                         related_pinyin: str | None,
                         related_de: str | None,
                         relation_type: str) -> None:
    """relation_type: 'synonym' or 'antonym'"""
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO entry_relations
           (word_id, related_zh, related_pinyin, related_de, relation_type)
           VALUES (?, ?, ?, ?, ?)""",
        (word_id, related_zh, related_pinyin, related_de, relation_type),
    )
    conn.commit()
    conn.close()


def get_word_examples(word_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM entry_examples WHERE word_id = ? ORDER BY position",
        (word_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_word_measure_words(word_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM entry_measure_words WHERE word_id = ? ORDER BY position",
        (word_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_word_relations(word_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM entry_relations WHERE word_id = ? ORDER BY relation_type, id",
        (word_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def insert_grammar_structure(word_id: int, structure: str, explanation: str | None,
                              example_zh: str | None, position: int) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO entry_grammar_structures (word_id, structure, explanation, example_zh, position)
           VALUES (?, ?, ?, ?, ?)""",
        (word_id, structure, explanation, example_zh, position),
    )
    conn.commit()
    conn.close()


def get_word_grammar_structures(word_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM entry_grammar_structures WHERE word_id = ? ORDER BY position",
        (word_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Grammar points  (type: grammar — reference only, no SRS cards)
# ---------------------------------------------------------------------------

def insert_grammar_point(name: str, level: str | None, structure: str | None,
                         meaning: str | None, usage: str | None,
                         cultural_note: str | None) -> int:
    """Insert a grammar_points row (INSERT OR IGNORE). Returns the row id."""
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO grammar_points
           (name, level, structure, meaning, usage, cultural_note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, level, structure, meaning, usage, cultural_note),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM grammar_points WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return row["id"]


def insert_grammar_example(grammar_id: int, example_zh: str, pinyin: str | None,
                           example_de: str | None, structure: str | None,
                           position: int) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO grammar_examples
           (grammar_id, example_zh, pinyin, example_de, structure, position)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (grammar_id, example_zh, pinyin, example_de, structure, position),
    )
    conn.commit()
    conn.close()


def insert_grammar_pattern(grammar_id: int, pattern: str, meaning: str | None,
                           example: str | None, position: int) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO grammar_patterns (grammar_id, pattern, meaning, example, position)
           VALUES (?, ?, ?, ?, ?)""",
        (grammar_id, pattern, meaning, example, position),
    )
    conn.commit()
    conn.close()


def insert_grammar_comparison(grammar_id: int, title: str | None,
                              explanation: str | None, position: int) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO grammar_comparisons (grammar_id, title, explanation, position)
           VALUES (?, ?, ?, ?)""",
        (grammar_id, title, explanation, position),
    )
    conn.commit()
    conn.close()


def insert_grammar_expression(grammar_id: int, expression: str,
                              meaning: str | None, position: int) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO grammar_expressions (grammar_id, expression, meaning, position)
           VALUES (?, ?, ?, ?)""",
        (grammar_id, expression, meaning, position),
    )
    conn.commit()
    conn.close()


def get_grammar_point_by_name(name: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM grammar_points WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_grammar_points() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM grammar_points ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

def upsert_character(char: dict) -> int:
    """Insert or update a character row without deleting it (preserves FK refs). Returns char_id."""
    conn = get_db()
    conn.execute(
        """INSERT INTO characters
           (char, traditional, pinyin, hsk_level, etymology, other_meanings)
           VALUES (:char, :traditional, :pinyin, :hsk_level, :etymology, :other_meanings)
           ON CONFLICT(char) DO UPDATE SET
               traditional    = excluded.traditional,
               pinyin         = excluded.pinyin,
               hsk_level      = excluded.hsk_level,
               etymology      = COALESCE(excluded.etymology, etymology),
               other_meanings = COALESCE(excluded.other_meanings, other_meanings)""",
        char,
    )
    conn.commit()
    row = conn.execute("SELECT id FROM characters WHERE char = ?", (char["char"],)).fetchone()
    conn.close()
    return row["id"]


def get_character(char: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM characters WHERE char = ?", (char,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_character_by_id(char_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    comp_rows = conn.execute(
        "SELECT compound_zh, pinyin, meaning FROM character_compounds WHERE char_id = ? ORDER BY position",
        (char_id,),
    ).fetchall()
    d["compounds"] = [dict(c) for c in comp_rows]
    conn.close()
    return d


def get_all_characters() -> list[dict]:
    """Return all characters sorted by their Unicode code point (natural stroke order proxy)."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM characters ORDER BY char").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_words_for_character(char_id: int) -> list[dict]:
    """Return all words that contain this character."""
    conn = get_db()
    rows = conn.execute(
        """SELECT w.id, w.word_zh, w.pinyin, w.definition, w.pos
           FROM entry_characters wc
           JOIN entries w ON w.id = wc.word_id
           WHERE wc.char_id = ?
           ORDER BY w.word_zh""",
        (char_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_character(char_id: int, fields: dict) -> None:
    allowed = {"pinyin", "etymology", "other_meanings", "traditional", "hsk_level"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k}=:{k}" for k in updates)
    conn = get_db()
    conn.execute(f"UPDATE characters SET {set_clause} WHERE id=:id", {**updates, "id": char_id})
    conn.commit()
    conn.close()


def upsert_character_compounds(char_id: int, compounds: list[dict]) -> None:
    """Insert or update normalised compound rows for a character.

    Each compound dict should have keys: zh (required), pinyin, meaning.
    Existing rows for this char_id are replaced on conflict (zh).
    """
    conn = get_db()
    for pos, c in enumerate(compounds):
        zh = (c.get("simplified") or c.get("zh") or c.get("compound") or "").strip()
        if not zh:
            continue
        conn.execute(
            """INSERT INTO character_compounds (char_id, compound_zh, pinyin, meaning, position)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(char_id, compound_zh) DO UPDATE SET
                   pinyin   = excluded.pinyin,
                   meaning  = excluded.meaning,
                   position = excluded.position""",
            (char_id, zh, c.get("pinyin"), c.get("meaning") or c.get("de"), pos),
        )
    conn.commit()
    conn.close()


def insert_word_character(word_id: int, char_id: int,
                          position: int,
                          meaning_in_context: str | None) -> None:
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO entry_characters
           (word_id, char_id, position, meaning_in_context)
           VALUES (?, ?, ?, ?)""",
        (word_id, char_id, position, meaning_in_context),
    )
    conn.commit()
    conn.close()


def get_word_characters(word_id: int) -> list[dict]:
    """Returns characters in position order, joined with full character details.
    Compounds are fetched from the character_compounds relational table."""
    conn = get_db()
    rows = conn.execute(
        """SELECT wc.position, wc.meaning_in_context,
                  c.id as char_id, c.char, c.traditional, c.pinyin,
                  c.hsk_level, c.etymology, c.other_meanings
           FROM entry_characters wc
           JOIN characters c ON c.id = wc.char_id
           WHERE wc.word_id = ?
           ORDER BY wc.position""",
        (word_id,),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        comp_rows = conn.execute(
            """SELECT compound_zh, pinyin, meaning FROM character_compounds
               WHERE char_id = ? ORDER BY position""",
            (d["char_id"],),
        ).fetchall()
        d["compounds"] = [dict(c) for c in comp_rows]
        result.append(d)
    conn.close()
    return result

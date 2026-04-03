import json
import os
import sqlite3
from datetime import date, datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", "data/srs.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")

DAY_CUTOFF_HOUR = 4  # New day starts at 4am, like Anki


def anki_today() -> date:
    """Return today's date using 4am as the day boundary (like Anki).

    Between midnight and 3:59am, returns yesterday's date so that late-night
    review sessions still count as the previous calendar day.
    """
    now = datetime.now()
    if now.hour < DAY_CUTOFF_HOUR:
        return (now - timedelta(days=1)).date()
    return now.date()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _existing_tables(conn) -> set:
    return {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def init_db() -> None:
    os.makedirs("data", exist_ok=True)
    with open(SCHEMA_PATH, "r") as f:
        schema = f.read()
    conn = get_db()

    # ── Phase 1: rename legacy tables BEFORE running schema.sql ────────────────
    # (schema.sql uses CREATE TABLE IF NOT EXISTS, so pre-existing tables survive)
    existing = _existing_tables(conn)

    _TABLE_RENAMES = [
        ("words",             "entries"),
        ("word_examples",     "entry_examples"),
        ("word_characters",   "entry_characters"),
        ("word_measure_words","entry_measure_words"),
        ("word_relations",    "entry_relations"),
        ("note_components",   "entry_components"),
        ("sentences",         "story_sentences"),
    ]
    for old, new in _TABLE_RENAMES:
        if old in existing and new not in existing:
            conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
    conn.commit()

    # ── Phase 2: run schema.sql (creates any tables that don't exist yet) ───────
    conn.executescript(schema)
    conn.commit()

    # ── Phase 3: column migrations on existing databases ────────────────────────
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}
    if "notes" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN notes TEXT")
    if "note_type" not in cols:
        conn.execute(
            "ALTER TABLE entries ADD COLUMN note_type TEXT NOT NULL DEFAULT 'vocabulary'"
        )
    if "register" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN register TEXT CHECK(register IN ('spoken', 'written', 'both', 'spoken_colloquial', 'spoken_neutral', 'neutral', 'formal_written', 'literary'))")
    else:
        # Fix old 3-value CHECK constraint → 6-value (SQLite requires table recreation)
        entries_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'"
        ).fetchone()["sql"]
        if entries_sql and "spoken_neutral" not in entries_sql:
            # SQLite FK tracking: renaming 'entries' makes child tables reference the
            # renamed name.  Instead: create new table, copy data, drop old, rename new.
            col_names = [r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
            cols_csv = ", ".join(col_names)
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.commit()
            conn.execute("""CREATE TABLE _entries_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    word_zh         TEXT NOT NULL UNIQUE,
                    pinyin          TEXT,
                    definition      TEXT,
                    pos             TEXT,
                    hsk_level       INTEGER,
                    traditional     TEXT,
                    definition_zh   TEXT,
                    date_added      TEXT NOT NULL DEFAULT (datetime('now')),
                    source          TEXT NOT NULL DEFAULT 'kouyu',
                    notes           TEXT,
                    note_type       TEXT NOT NULL DEFAULT 'vocabulary',
                    source_sentence TEXT,
                    grammar_notes   TEXT,
                    register        TEXT CHECK(register IN ('spoken', 'written', 'both', 'spoken_colloquial', 'spoken_neutral', 'neutral', 'formal_written', 'literary'))
                )""")
            conn.execute(f"INSERT INTO _entries_new ({cols_csv}) SELECT {cols_csv} FROM entries")
            conn.execute("DROP TABLE entries")
            conn.execute("ALTER TABLE _entries_new RENAME TO entries")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()

    if "date_yaml" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN date_yaml TEXT")
    if "definition_de" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN definition_de TEXT")

    ex_cols = {r["name"] for r in conn.execute("PRAGMA table_info(entry_examples)").fetchall()}
    if "example_type" not in ex_cols:
        conn.execute("ALTER TABLE entry_examples ADD COLUMN example_type TEXT NOT NULL DEFAULT 'example'")
    if "example_en" not in ex_cols:
        conn.execute("ALTER TABLE entry_examples ADD COLUMN example_en TEXT")

    # Remove duplicate examples (keep lowest id per word+text pair)
    conn.execute("""DELETE FROM entry_examples WHERE id NOT IN (
        SELECT MIN(id) FROM entry_examples GROUP BY word_id, example_zh
    )""")

    # Migrate compounds from JSON column → character_compounds relational table
    import json as _json_local
    chars_with_json = conn.execute(
        "SELECT id, compounds FROM characters WHERE compounds IS NOT NULL AND compounds != ''"
    ).fetchall()
    for ch in chars_with_json:
        try:
            clist = _json_local.loads(ch["compounds"])
            for pos, c in enumerate(clist):
                zh = (c.get("simplified") or c.get("zh") or c.get("compound") or "").strip()
                if zh:
                    conn.execute(
                        """INSERT OR IGNORE INTO character_compounds
                           (char_id, compound_zh, pinyin, meaning, position)
                           VALUES (?, ?, ?, ?, ?)""",
                        (ch["id"], zh, c.get("pinyin"), c.get("meaning"), pos),
                    )
        except Exception:
            pass

    conn.execute("""CREATE TABLE IF NOT EXISTS api_call_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        called_at     TEXT NOT NULL DEFAULT (datetime('now')),
        model         TEXT NOT NULL,
        input_tokens  INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        purpose       TEXT NOT NULL DEFAULT 'story'
    )""")

    story_cols = {r["name"] for r in conn.execute("PRAGMA table_info(stories)").fetchall()}
    if "prompt_text" not in story_cols:
        conn.execute("ALTER TABLE stories ADD COLUMN prompt_text TEXT")

    deck_cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)").fetchall()}
    if "deleted_at" not in deck_cols:
        conn.execute("ALTER TABLE decks ADD COLUMN deleted_at TEXT")
    card_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cards)").fetchall()}
    if "deleted_at" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN deleted_at TEXT")

    preset_cols = {r["name"] for r in conn.execute("PRAGMA table_info(deck_presets)").fetchall()}
    if "new_gather_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN new_gather_order TEXT NOT NULL DEFAULT 'ascending_position'")
        # Map legacy insertion_order: random → random_cards, sequential → ascending_position
        if "insertion_order" in preset_cols:
            conn.execute("""UPDATE deck_presets SET new_gather_order =
                CASE insertion_order WHEN 'random' THEN 'random_cards' ELSE 'ascending_position' END""")
    if "new_sort_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN new_sort_order TEXT NOT NULL DEFAULT 'card_type_gathered'")
    if "new_review_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN new_review_order TEXT NOT NULL DEFAULT 'mixed'")
    if "interday_learning_review_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN interday_learning_review_order TEXT NOT NULL DEFAULT 'mixed'")
    if "review_sort_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN review_sort_order TEXT NOT NULL DEFAULT 'due_random'")
    if "bury_new_siblings" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN bury_new_siblings INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE deck_presets ADD COLUMN bury_review_siblings INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE deck_presets ADD COLUMN bury_interday_siblings INTEGER NOT NULL DEFAULT 0")
        # Migrate from legacy bury_siblings
        if "bury_siblings" in preset_cols:
            conn.execute("""UPDATE deck_presets SET
                bury_new_siblings      = bury_siblings,
                bury_review_siblings   = bury_siblings,
                bury_interday_siblings = bury_siblings""")
    if "bury_quick_mode" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN bury_quick_mode TEXT NOT NULL DEFAULT 'all'")
    if "category_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN category_order TEXT NOT NULL DEFAULT 'listening,reading,creating'")

    conn.commit()

    # Ensure presets + default deck exist
    _ensure_presets(conn)
    preset_id = conn.execute("SELECT id FROM deck_presets WHERE is_default = 1 LIMIT 1").fetchone()["id"]
    all_id = _ensure_deck(conn, "All", parent_id=None, preset_id=preset_id)
    # Migrate any pre-existing root decks (other than "All") to be children of "All".
    # Done one-by-one to handle cases where a same-named deck already exists under "All"
    # (which would cause a UNIQUE(name, parent_id) violation on a bulk UPDATE).
    root_decks = conn.execute(
        "SELECT id, name FROM decks WHERE parent_id IS NULL AND id != ? AND deleted_at IS NULL",
        (all_id,),
    ).fetchall()
    for deck in root_decks:
        already_child = conn.execute(
            "SELECT id FROM decks WHERE name = ? AND parent_id = ?",
            (deck["name"], all_id),
        ).fetchone()
        if already_child:
            # A deck with the same name already lives under "All" — re-point any cards
            # that reference this orphaned root deck, then delete it.
            conn.execute(
                "UPDATE cards SET deck_id = ? WHERE deck_id = ?",
                (already_child["id"], deck["id"]),
            )
            conn.execute("DELETE FROM decks WHERE id = ?", (deck["id"],))
        else:
            conn.execute(
                "UPDATE decks SET parent_id = ? WHERE id = ?",
                (all_id, deck["id"]),
            )
    # Remove the unused "Default" deck if it exists and has no cards
    default_row = conn.execute("SELECT id FROM decks WHERE name = 'Default'").fetchone()
    if default_row:
        has_cards = conn.execute(
            "SELECT 1 FROM cards WHERE deck_id = ? LIMIT 1", (default_row["id"],)
        ).fetchone()
        if not has_cards:
            conn.execute("DELETE FROM decks WHERE id = ?", (default_row["id"],))
    conn.commit()

    # Ensure "Sentences" deck exists and migrate any sentence-type cards into it
    _migrate_sentences_deck(conn, all_id, preset_id)
    conn.commit()
    conn.close()


def _ensure_sentences_leaf_decks(conn: sqlite3.Connection, all_id: int,
                                  preset_id: int) -> dict:
    """Create (or get) the Sentences parent deck and its 3 category leaf decks."""
    sent_id = _ensure_deck(conn, "Sentences", parent_id=all_id, preset_id=preset_id)
    return {
        "listening": _ensure_deck(conn, "Sentences · Listening", parent_id=sent_id,
                                   preset_id=preset_id, category="listening"),
        "reading":   _ensure_deck(conn, "Sentences · Reading",   parent_id=sent_id,
                                   preset_id=preset_id, category="reading"),
        "creating":  _ensure_deck(conn, "Sentences · Creating",  parent_id=sent_id,
                                   preset_id=preset_id, category="creating"),
    }


def _migrate_sentences_deck(conn: sqlite3.Connection, all_id: int,
                             preset_id: int) -> None:
    """One-time migration: move all sentence-type word cards into the Sentences deck."""
    leaf = _ensure_sentences_leaf_decks(conn, all_id, preset_id)

    # Find all cards belonging to sentence-type words that are NOT already in the Sentences deck
    sentences_deck_ids = set(leaf.values())
    rows = conn.execute(
        """SELECT c.id, c.category FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE w.note_type = 'sentence'
             AND c.deck_id NOT IN ({})""".format(",".join("?" * len(sentences_deck_ids))),
        list(sentences_deck_ids),
    ).fetchall()

    for row in rows:
        new_deck_id = leaf[row["category"]]
        conn.execute("UPDATE cards SET deck_id = ? WHERE id = ?",
                     (new_deck_id, row["id"]))


def _ensure_presets(conn: sqlite3.Connection) -> None:
    """Seed the two built-in presets if they don't exist yet."""
    existing = {r["name"] for r in conn.execute("SELECT name FROM deck_presets").fetchall()}

    if "Default" not in existing:
        conn.execute(
            """INSERT INTO deck_presets (name, is_default) VALUES ('Default', 0)"""
        )

    if "Anki Default" not in existing:
        conn.execute(
            """INSERT INTO deck_presets
               (name, new_per_day, reviews_per_day,
                learning_steps, graduating_interval, easy_interval,
                relearning_steps, minimum_interval, insertion_order,
                bury_siblings, randomize_story_order, leech_threshold, leech_action, is_default)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            ("Anki Default", 9999, 9999, "11m 10m", 4, 9, "10", 1, "sequential", 1, 0, 8, "suspend"),
        )

    # Guarantee exactly one default
    if not conn.execute("SELECT id FROM deck_presets WHERE is_default = 1").fetchone():
        conn.execute("UPDATE deck_presets SET is_default = 1 WHERE name = 'Anki Default'")


def _ensure_default_preset(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM deck_presets WHERE is_default = 1 LIMIT 1").fetchone()
    if row:
        return row["id"]
    _ensure_presets(conn)
    return conn.execute("SELECT id FROM deck_presets WHERE is_default = 1 LIMIT 1").fetchone()["id"]


def _ensure_deck(conn: sqlite3.Connection, name: str,
                 parent_id: int | None, preset_id: int,
                 category: str | None = None) -> int:
    row = conn.execute("SELECT id FROM decks WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO decks (name, parent_id, preset_id, category) VALUES (?, ?, ?, ?)",
        (name, parent_id, preset_id, category),
    )
    return cur.lastrowid

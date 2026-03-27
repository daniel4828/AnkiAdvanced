import json
import os
import sqlite3
from datetime import date, datetime

DB_PATH = os.environ.get("DB_PATH", "data/srs.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


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


# ---------------------------------------------------------------------------
# Deck presets
# ---------------------------------------------------------------------------

def default_preset() -> dict:
    return {
        "name": "Default",
        "new_per_day": 20,
        "reviews_per_day": 100,
        "learning_steps": "11m 10m",
        "graduating_interval": 1,
        "easy_interval": 4,
        "relearning_steps": "10",
        "minimum_interval": 1,
        "insertion_order": "sequential",
        "bury_siblings": 1,
        "randomize_story_order": 0,
        "leech_threshold": 8,
        "leech_action": "suspend",
        "new_gather_order": "ascending_position",
        "new_sort_order": "card_type_gathered",
        "new_review_order": "mixed",
        "interday_learning_review_order": "mixed",
        "review_sort_order": "due_random",
        "bury_new_siblings": 0,
        "bury_review_siblings": 0,
        "bury_interday_siblings": 0,
        "bury_quick_mode": "all",
    }


def get_default_preset() -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM deck_presets WHERE is_default = 1 LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def set_default_preset(preset_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE deck_presets SET is_default = 0")
    conn.execute("UPDATE deck_presets SET is_default = 1 WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()


def list_presets() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT p.*, COUNT(d.id) AS deck_count
           FROM deck_presets p
           LEFT JOIN decks d ON d.preset_id = p.id
           GROUP BY p.id
           ORDER BY p.is_default DESC, p.name"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_preset(preset_id: int) -> None:
    conn = get_db()
    in_use = conn.execute(
        "SELECT COUNT(*) FROM decks WHERE preset_id = ?", (preset_id,)
    ).fetchone()[0]
    if in_use:
        conn.close()
        raise ValueError("Preset is still assigned to one or more decks")
    conn.execute("DELETE FROM deck_presets WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()


def assign_preset_to_deck(deck_id: int, preset_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE decks SET preset_id = ? WHERE id = ?", (preset_id, deck_id))
    conn.commit()
    conn.close()


def get_preset(preset_id: int) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM deck_presets WHERE id = ?", (preset_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_preset_for_deck(deck_id: int) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT p.* FROM deck_presets p JOIN decks d ON d.preset_id = p.id WHERE d.id = ?",
        (deck_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def insert_preset(preset: dict) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO deck_presets
           (name, new_per_day, reviews_per_day,
            learning_steps, graduating_interval, easy_interval,
            relearning_steps, minimum_interval, insertion_order,
            bury_siblings, randomize_story_order, leech_threshold, leech_action,
            new_gather_order, new_sort_order, new_review_order,
            interday_learning_review_order, review_sort_order,
            bury_new_siblings, bury_review_siblings, bury_interday_siblings,
            bury_quick_mode)
           VALUES (:name, :new_per_day, :reviews_per_day,
                   :learning_steps, :graduating_interval, :easy_interval,
                   :relearning_steps, :minimum_interval, :insertion_order,
                   :bury_siblings, :randomize_story_order, :leech_threshold, :leech_action,
                   :new_gather_order, :new_sort_order, :new_review_order,
                   :interday_learning_review_order, :review_sort_order,
                   :bury_new_siblings, :bury_review_siblings, :bury_interday_siblings,
                   :bury_quick_mode)""",
        preset,
    )
    conn.commit()
    preset_id = cur.lastrowid
    conn.close()
    return preset_id


def update_preset(preset_id: int, fields: dict) -> None:
    allowed = {
        "name", "new_per_day", "reviews_per_day",
        "learning_steps", "graduating_interval", "easy_interval",
        "relearning_steps", "minimum_interval", "insertion_order",
        "bury_siblings", "randomize_story_order", "leech_threshold", "leech_action",
        "new_gather_order", "new_sort_order", "new_review_order",
        "interday_learning_review_order", "review_sort_order",
        "bury_new_siblings", "bury_review_siblings", "bury_interday_siblings",
        "bury_quick_mode",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["_id"] = preset_id
    conn = get_db()
    conn.execute(f"UPDATE deck_presets SET {set_clause} WHERE id = :_id", updates)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Decks
# ---------------------------------------------------------------------------

def insert_deck(name: str, parent_id: int | None, preset_id: int,
                category: str | None = None) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO decks (name, parent_id, preset_id, category) VALUES (?, ?, ?, ?)",
        (name, parent_id, preset_id, category),
    )
    conn.commit()
    deck_id = cur.lastrowid
    conn.close()
    return deck_id


def get_deck(deck_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_decks() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM decks WHERE deleted_at IS NULL ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_deck_tree() -> list[dict]:
    decks = get_all_decks()
    by_id = {d["id"]: {**d, "children": []} for d in decks}
    roots = []
    for d in by_id.values():
        if d["parent_id"] is None:
            d["virtual"] = True  # "All" is treated as a filtered deck, not part of the regular tree
            roots.append(d)
        else:
            parent = by_id.get(d["parent_id"])
            if parent:
                parent["children"].append(d)
    # Mark the Sentences deck and its children as filtered
    for root in roots:
        for child in root.get("children", []):
            if child["name"] == "Sentences":
                child["filtered"] = True
                child["no_story"] = True
                for leaf in child.get("children", []):
                    leaf["filtered"] = True
                    leaf["no_story"] = True
    return roots


def rename_deck(deck_id: int, name: str) -> None:
    conn = get_db()
    conn.execute("UPDATE decks SET name = ? WHERE id = ?", (name, deck_id))
    conn.commit()
    conn.close()


def delete_deck(deck_id: int) -> None:
    """Soft-delete: move to trash."""
    conn = get_db()
    conn.execute("UPDATE decks SET deleted_at = datetime('now') WHERE id = ?", (deck_id,))
    conn.commit()
    conn.close()


def delete_all_deck_cards(deck_id: int) -> int:
    """Soft-delete all cards in a deck and its descendant decks. Returns count deleted."""
    conn = get_db()
    # Collect this deck + all descendant deck IDs via iterative traversal
    all_ids = [deck_id]
    queue = [deck_id]
    while queue:
        parent = queue.pop()
        children = conn.execute(
            "SELECT id FROM decks WHERE parent_id = ? AND deleted_at IS NULL", (parent,)
        ).fetchall()
        for row in children:
            all_ids.append(row["id"])
            queue.append(row["id"])
    placeholders = ",".join("?" * len(all_ids))
    cur = conn.execute(
        f"UPDATE cards SET deleted_at = datetime('now') WHERE deck_id IN ({placeholders}) AND deleted_at IS NULL",
        all_ids,
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def get_cards_in_trash_deck(deck_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT c.id, c.category, c.state, w.word_zh, w.pinyin
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ? AND c.deleted_at IS NULL
           ORDER BY c.category, w.word_zh""",
        (deck_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trash() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM decks WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
    ).fetchall()
    conn.close()
    decks = [dict(r) for r in rows]
    for d in decks:
        d["cards"] = get_cards_in_trash_deck(d["id"])
    return decks


def restore_deck(deck_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE decks SET deleted_at = NULL WHERE id = ?", (deck_id,))
    conn.commit()
    conn.close()


def purge_all_cards_from_deck(deck_id: int) -> int:
    """Hard-delete all cards belonging to a trashed deck (leaves the deck shell). Returns count."""
    conn = get_db()
    cur = conn.execute("DELETE FROM cards WHERE deck_id = ?", (deck_id,))
    conn.commit()
    conn.close()
    return cur.rowcount


def purge_deck(deck_id: int) -> None:
    """Hard-delete a single trashed deck."""
    conn = get_db()
    conn.execute("DELETE FROM decks WHERE id = ? AND deleted_at IS NOT NULL", (deck_id,))
    conn.commit()
    conn.close()


def purge_all_trash() -> int:
    """Hard-delete all trashed decks and cards immediately. Returns total count deleted."""
    conn = get_db()
    deck_cur = conn.execute("DELETE FROM decks WHERE deleted_at IS NOT NULL")
    card_cur = conn.execute("DELETE FROM cards WHERE deleted_at IS NOT NULL")
    conn.commit()
    conn.close()
    return deck_cur.rowcount + card_cur.rowcount


def purge_old_trash(days: int = 30) -> int:
    """Hard-delete trashed decks and cards older than `days`. Returns total count deleted."""
    conn = get_db()
    threshold = f"-{days} days"
    deck_cur = conn.execute(
        "DELETE FROM decks WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)",
        (threshold,),
    )
    card_cur = conn.execute(
        "DELETE FROM cards WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)",
        (threshold,),
    )
    conn.commit()
    conn.close()
    return deck_cur.rowcount + card_cur.rowcount



def get_or_create_deck(name: str, parent_id: int | None = None,
                       category: str | None = None) -> int:
    """Get deck id by (name, parent_id), creating it if it doesn't exist."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM decks WHERE name = ? AND parent_id IS ?", (name, parent_id)
    ).fetchone()
    if row:
        conn.close()
        return row["id"]
    preset_id = _ensure_default_preset(conn)
    cur = conn.execute(
        "INSERT INTO decks (name, parent_id, preset_id, category) VALUES (?, ?, ?, ?)",
        (name, parent_id, preset_id, category),
    )
    conn.commit()
    deck_id = cur.lastrowid
    conn.close()
    return deck_id


def get_sentences_deck_ids() -> dict:
    """Return {category: deck_id} for the three Sentences leaf decks, creating them if needed."""
    conn = get_db()
    all_id = conn.execute(
        "SELECT id FROM decks WHERE name = 'All' AND parent_id IS NULL LIMIT 1"
    ).fetchone()["id"]
    preset_id = _ensure_default_preset(conn)
    leaf = _ensure_sentences_leaf_decks(conn, all_id, preset_id)
    conn.commit()
    conn.close()
    return leaf


def is_sentences_deck(deck_id: int) -> bool:
    """Return True if deck_id is the Sentences parent or one of its leaf decks."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM decks WHERE name = 'Sentences' AND parent_id IN "
        "(SELECT id FROM decks WHERE parent_id IS NULL) LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        return False
    sent_id = row["id"]
    children = {r["id"] for r in conn.execute(
        "SELECT id FROM decks WHERE parent_id = ?", (sent_id,)
    ).fetchall()}
    conn.close()
    return deck_id == sent_id or deck_id in children


def get_all_deck_id() -> int | None:
    """Return the id of the top-level 'All' deck, or None if it doesn't exist yet."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM decks WHERE name = 'All' AND parent_id IS NULL AND deleted_at IS NULL LIMIT 1"
    ).fetchone()
    conn.close()
    return row["id"] if row else None


def get_or_create_deck_path(path: str) -> int:
    """Parse an Anki-style 'Parent::Child::Leaf' path and ensure all decks exist.

    Returns the id of the deepest (leaf) deck. Roots are placed under 'All'.
    """
    segments = [s.strip() for s in path.split("::") if s.strip()]
    if not segments:
        raise ValueError(f"Empty deck path: {path!r}")
    parent_id = get_all_deck_id()
    for segment in segments:
        parent_id = get_or_create_deck(segment, parent_id=parent_id)
    return parent_id


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
            notes, date_yaml, source_sentence, grammar_notes, register)
           VALUES (:word_zh, :pinyin, :definition, :pos, :hsk_level,
                   :traditional, :definition_zh, :source, :note_type,
                   :notes, :date_yaml, :source_sentence, :grammar_notes, :register)""",
        {
            **word,
            "note_type":       word.get("note_type", "vocabulary"),
            "notes":           word.get("notes"),
            "date_yaml":       word.get("date_yaml"),
            "source_sentence": word.get("source_sentence"),
            "grammar_notes":   word.get("grammar_notes"),
            "register":        word.get("register"),
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


def update_word(word_id: int, word: dict) -> None:
    """Update mutable fields of an existing word row."""
    conn = get_db()
    conn.execute(
        """UPDATE entries SET
               pinyin=:pinyin, definition=:definition, pos=:pos, hsk_level=:hsk_level,
               traditional=:traditional, definition_zh=:definition_zh,
               source_sentence=:source_sentence, grammar_notes=:grammar_notes
           WHERE id=:id""",
        {
            **word,
            "id":              word_id,
            "source_sentence": word.get("source_sentence"),
            "grammar_notes":   word.get("grammar_notes"),
        },
    )
    conn.commit()
    conn.close()


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


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def insert_card(word_id: int, category: str, deck_id: int,
                state: str = "new") -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT OR IGNORE INTO cards (word_id, deck_id, category, state)
           VALUES (?, ?, ?, ?)""",
        (word_id, deck_id, category, state),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM cards WHERE word_id = ? AND category = ?",
        (word_id, category),
    ).fetchone()
    conn.close()
    return row["id"]


def get_card(card_id: int) -> dict | None:
    """Joined with word, deck, and preset — everything srs.py needs."""
    conn = get_db()
    row = conn.execute(
        """SELECT c.*,
                  w.word_zh, w.pinyin, w.definition, w.pos, w.hsk_level,
                  w.traditional, w.definition_zh, w.note_type,
                  p.learning_steps, p.graduating_interval, p.easy_interval,
                  p.relearning_steps, p.minimum_interval,
                  p.leech_threshold, p.leech_action,
                  p.new_per_day, p.reviews_per_day
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           JOIN decks d ON d.id = c.deck_id
           JOIN deck_presets p ON p.id = d.preset_id
           WHERE c.id = ?""",
        (card_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _count_new_introduced_today(conn, deck_id: int, category: str, today: str) -> int:
    """Cards whose very first review log entry is today (introduced as new today)."""
    return conn.execute(
        """SELECT COUNT(DISTINCT c.id) FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND EXISTS (
               SELECT 1 FROM review_log rl
               WHERE rl.card_id = c.id AND date(rl.reviewed_at) = ?
             )
             AND NOT EXISTS (
               SELECT 1 FROM review_log rl
               WHERE rl.card_id = c.id AND date(rl.reviewed_at) < ?
             )""",
        (deck_id, category, today, today),
    ).fetchone()[0]


def _get_virtually_buried_word_ids(
    word_ids: set[int], category: str, conn, today: str, now: str
) -> set[int]:
    """Return the subset of word_ids suppressed by a higher-priority sibling card due today.

    Each word is "owned" by the due card with the best combined rank:
      state rank  : learning/relearn=0, review=1, new=2
      category rank: listening=0, reading=1, creating=2
    A card is suppressed if a sibling with a strictly lower combined rank exists and is due.
    """
    if not word_ids:
        return set()
    placeholders = ",".join("?" * len(word_ids))
    rows = conn.execute(
        f"""SELECT DISTINCT c_mine.word_id
            FROM cards c_mine
            JOIN cards c_sib ON c_sib.word_id = c_mine.word_id AND c_sib.id != c_mine.id
            WHERE c_mine.word_id IN ({placeholders})
              AND c_mine.category = ?
              AND c_mine.deleted_at IS NULL
              AND c_sib.category != ?
              AND c_sib.deleted_at IS NULL
              AND c_sib.state != 'suspended'
              AND (c_sib.buried_until IS NULL OR c_sib.buried_until < ?)
              AND (
                (c_sib.state IN ('learning', 'relearn') AND c_sib.due <= ?)
                OR (c_sib.state IN ('review', 'new') AND c_sib.due <= ?)
              )
              AND (
                CASE c_sib.state
                  WHEN 'learning' THEN 0 WHEN 'relearn' THEN 0 WHEN 'review' THEN 1 ELSE 2
                END <
                CASE c_mine.state
                  WHEN 'learning' THEN 0 WHEN 'relearn' THEN 0 WHEN 'review' THEN 1 ELSE 2
                END
                OR (
                  CASE c_sib.state
                    WHEN 'learning' THEN 0 WHEN 'relearn' THEN 0 WHEN 'review' THEN 1 ELSE 2
                  END =
                  CASE c_mine.state
                    WHEN 'learning' THEN 0 WHEN 'relearn' THEN 0 WHEN 'review' THEN 1 ELSE 2
                  END
                  AND
                  CASE c_sib.category WHEN 'listening' THEN 0 WHEN 'reading' THEN 1 ELSE 2 END <
                  CASE c_mine.category WHEN 'listening' THEN 0 WHEN 'reading' THEN 1 ELSE 2 END
                )
              )""",
        (*word_ids, category, category, today, now, today),
    ).fetchall()
    return {r["word_id"] for r in rows}


def resolve_bury_flags(preset: dict) -> tuple[bool, bool, bool]:
    """Return (bury_new, bury_review, bury_learning) based on bury_quick_mode."""
    mode = preset.get("bury_quick_mode", "all")
    if mode == "all":
        return True, True, True
    if mode == "none":
        return False, False, False
    # custom: use the individual fields
    return (
        bool(preset.get("bury_new_siblings", 0)),
        bool(preset.get("bury_review_siblings", 0)),
        bool(preset.get("bury_interday_siblings", 0)),
    )


def _interleave_cards(base: list, inserts: list) -> list:
    """Distribute inserts evenly throughout base."""
    if not inserts:
        return base
    if not base:
        return inserts
    result = list(base)
    step = max(1, len(base) // (len(inserts) + 1))
    for i, card in enumerate(inserts):
        pos = min(step * (i + 1) + i, len(result))
        result.insert(pos, card)
    return result


def get_due_cards(deck_id: int, category: str, *, sibling_suppression: bool = False) -> list[dict]:
    """All due cards for a category, ordered per preset display-order settings."""
    import random
    from itertools import groupby

    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()

    preset = get_preset_for_deck(deck_id)
    new_limit = preset["new_per_day"]
    new_done_today = _count_new_introduced_today(conn, deck_id, category, today)
    new_remaining = max(0, new_limit - new_done_today)

    rows = conn.execute(
        """SELECT c.*, w.word_zh, w.pinyin, w.definition, w.pos,
                  w.hsk_level, w.traditional, w.definition_zh,
                  w.note_type, w.source_sentence, w.notes
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ?
             AND c.category = ?
             AND c.state != 'suspended'
             AND c.deleted_at IS NULL
             AND (c.buried_until IS NULL OR c.buried_until < ?)
             AND (
               (c.state IN ('learning', 'relearn') AND c.due <= ?)
               OR (c.state = 'review' AND c.due <= ?)
               OR (c.state = 'new' AND c.due <= ?)
             )""",
        (deck_id, category, today, now, today, today),
    ).fetchall()

    all_cards = [dict(r) for r in rows]
    learning_cards = [c for c in all_cards if c["state"] in ("learning", "relearn")]
    review_cards   = [c for c in all_cards if c["state"] == "review"]
    new_cards_raw  = [c for c in all_cards if c["state"] == "new"]

    # ── 1. Gather & sort new cards ────────────────────────────────────────────
    gather = preset.get("new_gather_order", "ascending_position")
    if gather == "ascending_position":
        new_cards_raw.sort(key=lambda c: c["id"])
    elif gather == "descending_position":
        new_cards_raw.sort(key=lambda c: c["id"], reverse=True)
    elif gather == "deck":
        new_cards_raw.sort(key=lambda c: (c["deck_id"], c["id"]))
    elif gather == "deck_random_notes":
        by_deck: dict = {}
        for c in new_cards_raw:
            by_deck.setdefault(c["deck_id"], []).append(c)
        gathered = []
        for dk in sorted(by_deck):
            grp = by_deck[dk]
            random.shuffle(grp)
            gathered.extend(grp)
        new_cards_raw = gathered
    elif gather in ("random_notes", "random_cards"):
        random.shuffle(new_cards_raw)

    sort_o = preset.get("new_sort_order", "card_type_gathered")
    if sort_o in ("random", "card_type_random", "random_note_card_type"):
        random.shuffle(new_cards_raw)
    # else: card_type_gathered / gathered → keep gather order

    new_cards = new_cards_raw[:new_remaining]

    # ── 2. Sort review cards ──────────────────────────────────────────────────
    rev_o = preset.get("review_sort_order", "due_random")
    if rev_o == "due_random":
        review_cards.sort(key=lambda c: c["due"])
        shuffled: list = []
        for _, grp in groupby(review_cards, key=lambda c: c["due"]):
            g = list(grp)
            random.shuffle(g)
            shuffled.extend(g)
        review_cards = shuffled
    elif rev_o == "due_deck":
        review_cards.sort(key=lambda c: (c["due"], c["deck_id"]))
    elif rev_o == "deck_due":
        review_cards.sort(key=lambda c: (c["deck_id"], c["due"]))
    elif rev_o == "ascending_intervals":
        review_cards.sort(key=lambda c: c["interval"])
    elif rev_o == "descending_intervals":
        review_cards.sort(key=lambda c: c["interval"], reverse=True)
    elif rev_o == "ascending_ease":
        review_cards.sort(key=lambda c: c["ease"])
    elif rev_o == "descending_ease":
        review_cards.sort(key=lambda c: c["ease"], reverse=True)
    elif rev_o == "relative_overdueness":
        today_d = date.fromisoformat(today)
        def _overdueness(c: dict) -> float:
            if c["interval"] <= 0:
                return 0.0
            try:
                overdue = (today_d - date.fromisoformat(c["due"][:10])).days
                return overdue / c["interval"]
            except Exception:
                return 0.0
        review_cards.sort(key=_overdueness, reverse=True)

    # ── 3. Learning cards always sorted by due time ───────────────────────────
    learning_cards.sort(key=lambda c: c["due"])

    # ── 4. Assemble queue ─────────────────────────────────────────────────────
    il_o = preset.get("interday_learning_review_order", "mixed")
    if il_o == "learning_first":
        lr = learning_cards + review_cards
    elif il_o == "reviews_first":
        lr = review_cards + learning_cards
    else:  # mixed: merge by due time
        lr = sorted(learning_cards + review_cards, key=lambda c: c["due"])

    nr_o = preset.get("new_review_order", "mixed")
    if nr_o == "new_first":
        cards = new_cards + lr
    elif nr_o == "reviews_first":
        cards = lr + new_cards
    else:  # mixed: distribute new cards evenly throughout lr
        cards = _interleave_cards(lr, new_cards)

    # ── 5. Sibling suppression (for story word-list building) ─────────────────
    if sibling_suppression and any(resolve_bury_flags(preset)):
        word_ids = {c["word_id"] for c in cards}
        suppressed = _get_virtually_buried_word_ids(word_ids, category, conn, today, now)
        if suppressed:
            cards = [c for c in cards if c["word_id"] not in suppressed]

    conn.close()
    return cards


def get_next_card(deck_id: int, category: str) -> dict | None:
    """Top-priority card for the review session, ordered by today's story position."""
    cards = get_due_cards(deck_id, category)
    if not cards:
        return None

    # Reorder by story sentence position if a story exists for today
    today = date.today().isoformat()
    story = get_active_story(today, category, deck_id)
    if story:
        sentences = get_story_sentences(story["id"])
        # word_id → story position
        story_pos = {s["word_id"]: s["position"] for s in sentences}
        NO_POS = len(sentences)  # cards not in story go last
        cards.sort(key=lambda c: story_pos.get(c["word_id"], NO_POS))

    return cards[0]


def count_due(deck_id: int, category: str) -> dict:
    """Returns {new, learning, review} counts for deck badge display."""
    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    preset = get_preset_for_deck(deck_id)
    new_limit = preset["new_per_day"]

    conn = get_db()
    new_done_today = _count_new_introduced_today(conn, deck_id, category, today)
    new_remaining = max(0, new_limit - new_done_today)

    rows = conn.execute(
        """SELECT c.word_id, c.state FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND c.state != 'suspended'
             AND c.deleted_at IS NULL
             AND (c.buried_until IS NULL OR c.buried_until < ?)
             AND (
               (c.state IN ('learning', 'relearn') AND c.due <= ?)
               OR (c.state = 'review' AND c.due <= ?)
               OR (c.state = 'new' AND c.due <= ?)
             )""",
        (deck_id, category, today, now, today, today),
    ).fetchall()

    learning = sum(1 for r in rows if r["state"] in ("learning", "relearn"))
    review   = sum(1 for r in rows if r["state"] == "review")
    new_avail = sum(1 for r in rows if r["state"] == "new")

    conn.close()
    return {
        "new": min(new_avail, new_remaining),
        "learning": learning,
        "review": review,
    }


def update_word(word_id: int, fields: dict) -> None:
    allowed = {"word_zh", "pinyin", "definition", "pos", "traditional", "definition_zh", "notes", "hsk_level"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE entries SET {sets} WHERE id=?", (*updates.values(), word_id))
    conn.commit()
    conn.close()


def update_card(card_id: int, *, state: str, due: str,
                step_index: int, interval: int,
                ease: float, repetitions: int, lapses: int) -> None:
    conn = get_db()
    conn.execute(
        """UPDATE cards SET state=?, due=?, step_index=?, interval=?,
                            ease=?, repetitions=?, lapses=?
           WHERE id=?""",
        (state, due, step_index, interval, ease, repetitions, lapses, card_id),
    )
    conn.commit()
    conn.close()


def bury_card(card_id: int) -> None:
    """Bury a card until tomorrow (hidden for the rest of today)."""
    today = date.today().isoformat()
    conn = get_db()
    conn.execute("UPDATE cards SET buried_until = ? WHERE id = ?", (today, card_id))
    conn.commit()
    conn.close()


def unbury_card(card_id: int) -> None:
    """Remove burial — card becomes available immediately."""
    conn = get_db()
    conn.execute("UPDATE cards SET buried_until = NULL WHERE id = ?", (card_id,))
    conn.commit()
    conn.close()


def set_card_buried_until(card_id: int, buried_until: str | None) -> None:
    """Restore buried_until to an exact value (used by undo)."""
    conn = get_db()
    conn.execute("UPDATE cards SET buried_until = ? WHERE id = ?", (buried_until, card_id))
    conn.commit()
    conn.close()


def get_descendant_leaf_deck_ids(deck_id: int, category: str | None = None) -> list[int]:
    """Return all category-leaf deck IDs under deck_id (depth-first). Optionally filter by category."""
    conn = get_db()
    rows = conn.execute("SELECT id, parent_id, category FROM decks WHERE deleted_at IS NULL").fetchall()
    conn.close()

    children_map: dict = {}
    deck_cat: dict = {}
    for row in rows:
        deck_cat[row["id"]] = row["category"]
        pid = row["parent_id"]
        children_map.setdefault(pid, []).append(row["id"])

    result = []
    stack = [deck_id]
    while stack:
        current = stack.pop()
        cat = deck_cat.get(current)
        kids = children_map.get(current, [])
        if cat is not None:  # category leaf
            if category is None or cat == category:
                result.append(current)
        for kid in kids:
            stack.append(kid)
    return result


def _leaf_decks_with_category(root_deck_id: int) -> list[tuple[int, str]]:
    """Return [(deck_id, category)] for all category leaves under root_deck_id."""
    all_leaf_ids = get_descendant_leaf_deck_ids(root_deck_id)
    if not all_leaf_ids:
        deck = get_deck(root_deck_id)
        if deck and deck["category"]:
            return [(root_deck_id, deck["category"])]
        return []
    conn = get_db()
    placeholders = ','.join('?' * len(all_leaf_ids))
    rows = conn.execute(
        f"SELECT id, category FROM decks WHERE id IN ({placeholders})", all_leaf_ids
    ).fetchall()
    conn.close()
    return [(r["id"], r["category"]) for r in rows if r["category"]]


def get_next_card_any_cat(root_deck_id: int) -> dict | None:
    """Highest-priority card across all categories under root_deck_id."""
    leaf_pairs = _leaf_decks_with_category(root_deck_id)
    all_cards = []
    for deck_id, cat in leaf_pairs:
        all_cards.extend(get_due_cards(deck_id, cat))
    if not all_cards:
        return None

    # Reorder by story sentence position (same as get_next_card for single-cat)
    today = date.today().isoformat()
    story_pos: dict = {}
    for deck_id, cat in leaf_pairs:
        story = get_active_story(today, cat, deck_id)
        if story:
            for s in get_story_sentences(story["id"]):
                story_pos[s["word_id"]] = s["position"]

    NO_POS = 9999
    all_cards.sort(key=lambda c: (
        0 if c["state"] in ("learning", "relearn") else
        1 if c["state"] == "review" else 2,
        story_pos.get(c["word_id"], NO_POS),
        c["due"],
    ))
    return all_cards[0]


def count_due_any_cat(root_deck_id: int) -> dict:
    """Total due counts across all categories under root_deck_id."""
    leaf_pairs = _leaf_decks_with_category(root_deck_id)
    total = {"new": 0, "learning": 0, "review": 0}
    for deck_id, cat in leaf_pairs:
        c = count_due(deck_id, cat)
        for k in total:
            total[k] += c[k]
    return total


def get_due_cards_multi(deck_ids: list[int], category: str, *, sibling_suppression: bool = False) -> list[dict]:
    """Due cards across multiple decks, merged and priority-sorted."""
    all_cards = []
    for deck_id in deck_ids:
        all_cards.extend(get_due_cards(deck_id, category, sibling_suppression=sibling_suppression))
    all_cards.sort(key=lambda c: (
        0 if c["state"] in ("learning", "relearn") else
        1 if c["state"] == "review" else 2,
        c["due"]
    ))
    return all_cards


def get_next_card_multi(deck_ids: list[int], category: str) -> dict | None:
    """Highest-priority card across multiple decks."""
    cards = get_due_cards_multi(deck_ids, category)
    return cards[0] if cards else None


def count_due_multi(deck_ids: list[int], category: str) -> dict:
    """Aggregate due counts across multiple decks."""
    total = {"new": 0, "learning": 0, "review": 0}
    for deck_id in deck_ids:
        c = count_due(deck_id, category)
        for k in total:
            total[k] += c[k]
    return total


def count_due_deduped(leaf_pairs: list[tuple[int, str]]) -> dict:
    """Count unique due words across multiple category leaf decks for parent badge display.

    Each word is counted once, in the category of its highest-priority due card:
      state rank  : learning/relearn=0, review=1, new=2  (lower = better)
      category rank: listening=0, reading=1, creating=2  (lower = better)

    Respects the bury_siblings setting. Falls back to a simple sum if disabled.
    """
    if not leaf_pairs:
        return {"new": 0, "learning": 0, "review": 0}

    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()

    preset = get_preset_for_deck(leaf_pairs[0][0])
    if not preset.get("bury_siblings", 1):
        conn.close()
        total = {"new": 0, "learning": 0, "review": 0}
        for deck_id, cat in leaf_pairs:
            for k, v in count_due(deck_id, cat).items():
                total[k] += v
        return total

    cat_rank_map = {"listening": 0, "reading": 1, "creating": 2}
    # word_id -> (state_rank, cat_rank, state, deck_id, category)
    best: dict[int, tuple] = {}
    new_remaining_map: dict[tuple, int] = {}

    for deck_id, category in leaf_pairs:
        pr = get_preset_for_deck(deck_id)
        new_done = _count_new_introduced_today(conn, deck_id, category, today)
        new_remaining_map[(deck_id, category)] = max(0, pr["new_per_day"] - new_done)

        rows = conn.execute(
            """SELECT c.word_id, c.state FROM cards c
               WHERE c.deck_id = ? AND c.category = ?
                 AND c.state != 'suspended'
                 AND c.deleted_at IS NULL
                 AND (c.buried_until IS NULL OR c.buried_until < ?)
                 AND (
                   (c.state IN ('learning', 'relearn') AND c.due <= ?)
                   OR (c.state = 'review' AND c.due <= ?)
                   OR (c.state = 'new' AND c.due <= ?)
                 )""",
            (deck_id, category, today, now, today, today),
        ).fetchall()

        for r in rows:
            sr = 0 if r["state"] in ("learning", "relearn") else 1 if r["state"] == "review" else 2
            cr = cat_rank_map[category]
            if r["word_id"] not in best or (sr, cr) < best[r["word_id"]][:2]:
                best[r["word_id"]] = (sr, cr, r["state"], deck_id, category)

    conn.close()

    learning_count = 0
    review_count = 0
    new_by_deck: dict[tuple, int] = {}

    for sr, cr, state, deck_id, category in best.values():
        if sr == 0:
            learning_count += 1
        elif sr == 1:
            review_count += 1
        else:
            key = (deck_id, category)
            new_by_deck[key] = new_by_deck.get(key, 0) + 1

    new_count = sum(
        min(count, new_remaining_map.get(key, 0))
        for key, count in new_by_deck.items()
    )
    return {"new": new_count, "learning": learning_count, "review": review_count}


def count_unfinished() -> dict:
    """Count learning/relearn cards due right now across all decks and categories."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    learning = conn.execute(
        """SELECT COUNT(*) FROM cards
           WHERE state IN ('learning', 'relearn') AND due <= ?
             AND (buried_until IS NULL OR buried_until < date('now'))""",
        (now,),
    ).fetchone()[0]
    conn.close()
    return {"new": 0, "learning": learning, "review": 0}


def get_unfinished_deck_categories() -> list[dict]:
    """Return distinct (deck_id, category) pairs that have unfinished cards due now."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    rows = conn.execute(
        """SELECT DISTINCT deck_id, category FROM cards
           WHERE state IN ('learning', 'relearn') AND due <= ?
             AND (buried_until IS NULL OR buried_until < date('now'))""",
        (now,),
    ).fetchall()
    conn.close()
    return [{"deck_id": r["deck_id"], "category": r["category"]} for r in rows]


def get_next_unfinished_card() -> dict | None:
    """Highest-priority learning/relearn card due right now across all decks/categories."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    row = conn.execute(
        """SELECT id FROM cards
           WHERE state IN ('learning', 'relearn') AND due <= ?
             AND (buried_until IS NULL OR buried_until < date('now'))
           ORDER BY due ASC LIMIT 1""",
        (now,),
    ).fetchone()
    conn.close()
    return get_card(row["id"]) if row else None


def bury_siblings(word_id: int, reviewed_category: str, *,
                  bury_new: bool = False, bury_review: bool = False,
                  bury_learning: bool = False) -> None:
    """Bury other-category cards for this word based on which states should be buried."""
    states = []
    if bury_new:
        states.append("'new'")
    if bury_review:
        states.append("'review'")
    if bury_learning:
        states.extend(["'learning'", "'relearn'"])
    if not states:
        return
    today = date.today().isoformat()
    conn = get_db()
    conn.execute(
        f"UPDATE cards SET buried_until = ? WHERE word_id = ? AND category != ?"
        f" AND state IN ({','.join(states)})",
        (today, word_id, reviewed_category),
    )
    conn.commit()
    conn.close()


def get_sibling_cards(card_id: int) -> list[dict]:
    """The other 2 cards for the same word."""
    conn = get_db()
    card = conn.execute("SELECT word_id FROM cards WHERE id = ?", (card_id,)).fetchone()
    if not card:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT * FROM cards WHERE word_id = ? AND id != ?",
        (card["word_id"], card_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def suspend_card(card_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE cards SET state='suspended' WHERE id=?", (card_id,))
    conn.commit()
    conn.close()


def unsuspend_card(card_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE cards SET state='new' WHERE id=? AND state='suspended'", (card_id,))
    conn.commit()
    conn.close()


def get_creating_all_suspended(deck_id: int) -> bool:
    """Return True if all non-sentence creating cards in the deck are suspended (and at least one exists)."""
    conn = get_db()
    row = conn.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN c.state = 'suspended' THEN 1 ELSE 0 END) as suspended_count
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ? AND c.category = 'creating'
             AND c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id,),
    ).fetchone()
    conn.close()
    total = row["total"] or 0
    suspended = row["suspended_count"] or 0
    return total > 0 and total == suspended


def toggle_deck_creating_suspension(deck_id: int) -> dict:
    """Toggle all creating cards in a deck between suspended and new.

    Sentence notes (words with note_type='sentence') are excluded.
    Logic: if any cards are state='new', suspend all new ones;
           otherwise unsuspend all suspended ones.
    Returns {"all_suspended": bool, "count": int}.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT c.id, c.state FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ? AND c.category = 'creating'
             AND c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id,),
    ).fetchall()

    has_active = any(r["state"] != "suspended" for r in rows)
    if has_active:
        conn.execute(
            """UPDATE cards SET state='suspended'
               WHERE deck_id = ? AND category = 'creating'
                 AND deleted_at IS NULL AND state = 'new'
                 AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            (deck_id,),
        )
        all_suspended = True
    else:
        conn.execute(
            """UPDATE cards SET state='new'
               WHERE deck_id = ? AND category = 'creating'
                 AND deleted_at IS NULL AND state = 'suspended'
                 AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            (deck_id,),
        )
        all_suspended = False

    conn.commit()
    conn.close()
    return {"all_suspended": all_suspended, "count": len(rows)}


def get_category_all_suspended(deck_id: int, category: str) -> bool:
    """Return True if all non-sentence cards of given category in the deck are suspended."""
    conn = get_db()
    row = conn.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN c.state = 'suspended' THEN 1 ELSE 0 END) as suspended_count
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ? AND c.category = ?
             AND c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id, category),
    ).fetchone()
    conn.close()
    total = row["total"] or 0
    suspended = row["suspended_count"] or 0
    return total > 0 and total == suspended


def toggle_category_suspension(deck_id: int, category: str) -> dict:
    """Toggle all non-sentence cards of given category in a deck between suspended and active."""
    conn = get_db()
    rows = conn.execute(
        """SELECT c.id, c.state FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ? AND c.category = ?
             AND c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id, category),
    ).fetchall()
    has_active = any(r["state"] != "suspended" for r in rows)
    if has_active:
        conn.execute(
            """UPDATE cards SET state='suspended'
               WHERE deck_id = ? AND category = ?
                 AND deleted_at IS NULL AND state != 'suspended'
                 AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            (deck_id, category),
        )
        all_suspended = True
    else:
        conn.execute(
            """UPDATE cards SET state='new'
               WHERE deck_id = ? AND category = ?
                 AND deleted_at IS NULL AND state = 'suspended'
                 AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            (deck_id, category),
        )
        all_suspended = False
    conn.commit()
    conn.close()
    return {"all_suspended": all_suspended}


def get_deck_all_suspended(deck_id: int) -> bool:
    """Return True if ALL non-sentence cards in deck and all descendant decks are suspended."""
    conn = get_db()
    row = conn.execute(
        """WITH RECURSIVE descendants AS (
             SELECT id FROM decks WHERE id = ?
             UNION ALL
             SELECT d.id FROM decks d JOIN descendants p ON d.parent_id = p.id
           )
           SELECT COUNT(*) as total,
                  SUM(CASE WHEN c.state = 'suspended' THEN 1 ELSE 0 END) as suspended_count
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           JOIN descendants des ON c.deck_id = des.id
           WHERE c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id,),
    ).fetchone()
    conn.close()
    total = row["total"] or 0
    suspended = row["suspended_count"] or 0
    return total > 0 and total == suspended


def toggle_deck_all_suspension(deck_id: int) -> dict:
    """Toggle ALL non-sentence cards in deck and all descendant decks."""
    conn = get_db()
    deck_rows = conn.execute(
        """WITH RECURSIVE descendants AS (
             SELECT id FROM decks WHERE id = ?
             UNION ALL
             SELECT d.id FROM decks d JOIN descendants p ON d.parent_id = p.id
           )
           SELECT id FROM descendants""",
        (deck_id,),
    ).fetchall()
    deck_ids = [r["id"] for r in deck_rows]
    placeholders = ",".join("?" * len(deck_ids))
    active_row = conn.execute(
        f"""SELECT COUNT(*) as cnt FROM cards c
            JOIN entries w ON w.id = c.word_id
            WHERE c.deck_id IN ({placeholders})
              AND c.state != 'suspended'
              AND c.deleted_at IS NULL
              AND w.note_type != 'sentence'""",
        deck_ids,
    ).fetchone()
    has_active = active_row["cnt"] > 0
    if has_active:
        conn.execute(
            f"""UPDATE cards SET state='suspended'
                WHERE deck_id IN ({placeholders})
                  AND state != 'suspended'
                  AND deleted_at IS NULL
                  AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            deck_ids,
        )
    else:
        conn.execute(
            f"""UPDATE cards SET state='new'
                WHERE deck_id IN ({placeholders})
                  AND state = 'suspended'
                  AND deleted_at IS NULL
                  AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            deck_ids,
        )
    conn.commit()
    conn.close()
    return {"all_suspended": not has_active}


def get_words_for_browse() -> list[dict]:
    """Return all entries (with or without cards), with embedded card states per category."""
    sql = """
        SELECT w.id, w.word_zh, w.pinyin, w.definition, w.pos, w.hsk_level, w.note_type,
               c.id as card_id, c.category, c.state, c.interval, c.ease,
               c.due, c.lapses, c.step_index, c.deck_id,
               d.name as deck_name
        FROM entries w
        LEFT JOIN cards c ON c.word_id = w.id AND c.deleted_at IS NULL
        LEFT JOIN decks d ON d.id = c.deck_id
        ORDER BY w.word_zh, c.category
    """
    conn = get_db()
    rows = conn.execute(sql).fetchall()
    conn.close()
    words: dict = {}
    for r in rows:
        r = dict(r)
        wid = r["id"]
        if wid not in words:
            words[wid] = {
                "id": wid,
                "word_zh": r["word_zh"],
                "pinyin": r["pinyin"],
                "definition": r["definition"],
                "pos": r["pos"],
                "hsk_level": r["hsk_level"],
                "note_type": r["note_type"],
                "cards": [],
            }
        if r["card_id"] is not None:
            words[wid]["cards"].append({
                "id": r["card_id"],
                "category": r["category"],
                "state": r["state"],
                "interval": r["interval"],
                "ease": r["ease"],
                "due": r["due"],
                "lapses": r["lapses"],
                "step_index": r["step_index"],
                "deck_id": r["deck_id"],
                "deck_name": r["deck_name"],
            })
    return list(words.values())


def search_words(q: str) -> dict:
    """Return word IDs split into primary (word/def match) and secondary (example/notes match).
    Includes reference entries (no cards) so Browse search works across the full knowledge base."""
    like = f"%{q}%"
    conn = get_db()
    primary_ids = {r["id"] for r in conn.execute(
        """SELECT DISTINCT w.id FROM entries w
           WHERE (w.word_zh LIKE ? OR w.pinyin LIKE ?
              OR w.definition LIKE ? OR w.definition_zh LIKE ?)""",
        (like, like, like, like),
    ).fetchall()}
    secondary_ids = {r["id"] for r in conn.execute(
        """SELECT DISTINCT w.id FROM entries w
           LEFT JOIN entry_examples we ON we.word_id = w.id
           WHERE (we.example_zh LIKE ? OR we.example_de LIKE ? OR w.notes LIKE ?)""",
        (like, like, like),
    ).fetchall()} - primary_ids
    conn.close()
    return {"primary": list(primary_ids), "secondary": list(secondary_ids)}


def get_cards_for_word(word_id: int) -> list[dict]:
    """Return all cards for a word with full deck path (parent › child)."""
    conn = get_db()
    rows = conn.execute(
        """SELECT c.*, d.name as deck_name, p.name as parent_deck_name
           FROM cards c
           JOIN decks d ON d.id = c.deck_id
           LEFT JOIN decks p ON p.id = d.parent_id
           WHERE c.word_id = ? AND c.deleted_at IS NULL ORDER BY c.category""",
        (word_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        r = dict(r)
        if r.get("parent_deck_name"):
            r["deck_path"] = f"{r['parent_deck_name']} › {r['deck_name']}"
        else:
            r["deck_path"] = r["deck_name"]
        result.append(r)
    return result


def suspend_card(card_id: int) -> dict:
    """Toggle a card between suspended and new."""
    conn = get_db()
    cur = conn.execute("SELECT state FROM cards WHERE id=?", (card_id,)).fetchone()
    new_state = "new" if cur and cur["state"] == "suspended" else "suspended"
    conn.execute("UPDATE cards SET state=? WHERE id=?", (new_state, card_id))
    conn.commit()
    conn.close()
    return get_card(card_id)


def reset_card(card_id: int) -> dict:
    """Reset a card to new state with default scheduling values."""
    conn = get_db()
    conn.execute(
        """UPDATE cards SET state='new', step_index=0, interval=1,
                            ease=2.5, lapses=0, due=date('now'), buried_until=NULL
           WHERE id=?""",
        (card_id,),
    )
    conn.commit()
    conn.close()
    return get_card(card_id)


def delete_card(card_id: int) -> None:
    """Soft-delete: move card to trash."""
    conn = get_db()
    conn.execute("UPDATE cards SET deleted_at = datetime('now') WHERE id=?", (card_id,))
    conn.commit()
    conn.close()


def get_trashed_cards() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT c.*, w.word_zh, w.pinyin,
                  d.name as deck_name, p.name as parent_deck_name
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           JOIN decks d ON d.id = c.deck_id
           LEFT JOIN decks p ON p.id = d.parent_id
           WHERE c.deleted_at IS NOT NULL
           ORDER BY c.deleted_at DESC"""
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        r = dict(r)
        if r.get("parent_deck_name"):
            r["deck_path"] = f"{r['parent_deck_name']} › {r['deck_name']}"
        else:
            r["deck_path"] = r["deck_name"]
        result.append(r)
    return result


def restore_card(card_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE cards SET deleted_at = NULL WHERE id=?", (card_id,))
    conn.commit()
    conn.close()


def purge_card(card_id: int) -> None:
    """Hard-delete a single trashed card."""
    conn = get_db()
    conn.execute("DELETE FROM cards WHERE id=? AND deleted_at IS NOT NULL", (card_id,))
    conn.commit()
    conn.close()


def purge_card_from_deck(card_id: int) -> None:
    """Hard-delete a card that lives inside a trashed deck (not individually soft-deleted)."""
    conn = get_db()
    conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
    conn.commit()
    conn.close()


def bury_card_until_tomorrow(card_id: int) -> dict:
    """Bury a card until tomorrow."""
    conn = get_db()
    conn.execute(
        "UPDATE cards SET buried_until=date('now', '+1 day') WHERE id=?",
        (card_id,),
    )
    conn.commit()
    conn.close()
    return get_card(card_id)


def bulk_bury_cards_by_words(word_ids: list[int]) -> int:
    if not word_ids:
        return 0
    conn = get_db()
    ph = ','.join('?' * len(word_ids))
    cur = conn.execute(
        f"UPDATE cards SET buried_until=date('now', '+1 day') WHERE word_id IN ({ph}) AND deleted_at IS NULL",
        word_ids,
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def bulk_suspend_cards_by_words(word_ids: list[int]) -> int:
    if not word_ids:
        return 0
    conn = get_db()
    ph = ','.join('?' * len(word_ids))
    cur = conn.execute(
        f"UPDATE cards SET state='suspended' WHERE word_id IN ({ph}) AND deleted_at IS NULL",
        word_ids,
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def bulk_delete_cards_by_words(word_ids: list[int]) -> int:
    """Hard-delete words and all their related data (cards, examples, characters)."""
    if not word_ids:
        return 0
    conn = get_db()
    ph = ','.join('?' * len(word_ids))
    cur = conn.execute(f"DELETE FROM entries WHERE id IN ({ph})", word_ids)
    conn.commit()
    conn.close()
    return cur.rowcount


def delete_word(word_id: int) -> None:
    """Hard-delete a single word and all its related data."""
    conn = get_db()
    conn.execute("DELETE FROM entries WHERE id = ?", (word_id,))
    conn.commit()
    conn.close()


def bulk_move_cards_by_words(word_ids: list[int], deck_id: int) -> int:
    if not word_ids:
        return 0
    conn = get_db()
    ph = ','.join('?' * len(word_ids))
    cur = conn.execute(
        f"UPDATE cards SET deck_id=? WHERE word_id IN ({ph}) AND deleted_at IS NULL",
        [deck_id, *word_ids],
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def add_entry_to_deck(entry_id: int, parent_deck_id: int) -> dict:
    """Create cards in all category leaf-decks under parent_deck_id for a reference entry."""
    conn = get_db()
    leaf_decks = conn.execute(
        "SELECT id, category FROM decks WHERE parent_id = ? AND category IS NOT NULL AND deleted_at IS NULL",
        (parent_deck_id,),
    ).fetchall()
    if not leaf_decks:
        conn.close()
        return {"created": 0, "error": "No category decks found under this parent"}
    created = 0
    for ld in leaf_decks:
        cur = conn.execute(
            "INSERT OR IGNORE INTO cards (word_id, deck_id, category, state) VALUES (?, ?, ?, 'new')",
            (entry_id, ld["id"], ld["category"]),
        )
        created += cur.rowcount
    conn.commit()
    conn.close()
    return {"created": created}


def get_all_cards_for_browse(filters: dict | None = None) -> list[dict]:
    """Browse view. Supports filters: deck_id, category, state, search_text."""
    where = ["1=1"]
    params = []
    if filters:
        if filters.get("deck_id"):
            where.append("c.deck_id = ?")
            params.append(filters["deck_id"])
        if filters.get("category"):
            where.append("c.category = ?")
            params.append(filters["category"])
        if filters.get("state"):
            where.append("c.state = ?")
            params.append(filters["state"])
        if filters.get("search_text"):
            where.append("(w.word_zh LIKE ? OR w.definition LIKE ? OR w.pinyin LIKE ?)")
            q = f"%{filters['search_text']}%"
            params.extend([q, q, q])

    sql = f"""SELECT c.*, w.word_zh, w.pinyin, w.definition, w.pos,
                     w.hsk_level, d.name as deck_name
              FROM cards c
              JOIN entries w ON w.id = c.word_id
              JOIN decks d ON d.id = c.deck_id
              WHERE {' AND '.join(where)}
              ORDER BY w.word_zh"""
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Review log
# ---------------------------------------------------------------------------

def insert_review(card_id: int, rating: int,
                  user_response: str | None = None,
                  ai_score: int | None = None) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO review_log (card_id, rating, user_response, ai_score)
           VALUES (?, ?, ?, ?)""",
        (card_id, rating, user_response, ai_score),
    )
    conn.commit()
    log_id = cur.lastrowid
    conn.close()
    return log_id


def delete_review_log(log_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM review_log WHERE id=?", (log_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Stories & sentences
# ---------------------------------------------------------------------------

def get_active_story(date_str: str, category: str, deck_id: int) -> dict | None:
    """Latest story for (date, category, deck_id) or None."""
    conn = get_db()
    row = conn.execute(
        """SELECT * FROM stories
           WHERE date = ? AND category = ? AND deck_id = ?
           ORDER BY generated_at DESC LIMIT 1""",
        (date_str, category, deck_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def has_story_history(deck_id: int, category: str) -> bool:
    """Return True if any story exists for this deck+category."""
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM stories WHERE deck_id = ? AND category = ? LIMIT 1",
        (deck_id, category),
    ).fetchone()
    conn.close()
    return row is not None


def get_latest_story(deck_id: int, category: str) -> dict | None:
    """Most recent story for (deck_id, category), regardless of date."""
    conn = get_db()
    row = conn.execute(
        """SELECT * FROM stories WHERE deck_id = ? AND category = ?
           ORDER BY generated_at DESC LIMIT 1""",
        (deck_id, category),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_story(date_str: str, category: str, deck_id: int,
                 sentences: list[dict]) -> int:
    """Always inserts a new story row. Returns story_id."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO stories (date, category, deck_id) VALUES (?, ?, ?)",
        (date_str, category, deck_id),
    )
    story_id = cur.lastrowid
    for s in sentences:
        conn.execute(
            """INSERT INTO story_sentences (story_id, word_id, position, sentence_zh, sentence_en)
               VALUES (?, ?, ?, ?, ?)""",
            (story_id, s["word_id"], s["position"], s["sentence_zh"], s["sentence_en"]),
        )
    conn.commit()
    conn.close()
    return story_id


def get_sentence_for_word(story_id: int, word_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM story_sentences WHERE story_id = ? AND word_id = ?",
        (story_id, word_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_story_sentences(story_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT s.*, w.word_zh
           FROM story_sentences s JOIN entries w ON w.id = s.word_id
           WHERE s.story_id = ? ORDER BY s.position""",
        (story_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats(deck_id: int | None = None) -> dict:
    today = date.today().isoformat()
    conn = get_db()

    deck_filter = "AND c.deck_id = ?" if deck_id else ""
    params_deck = [deck_id] if deck_id else []

    # Total words: count distinct words that have at least one card in this deck
    if deck_id:
        total_words = conn.execute(
            "SELECT COUNT(DISTINCT c.word_id) FROM cards c WHERE c.deck_id = ?",
            [deck_id],
        ).fetchone()[0]
    else:
        total_words = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    reviews_today = conn.execute(
        f"""SELECT COUNT(*) FROM review_log rl
            JOIN cards c ON c.id = rl.card_id
            WHERE date(rl.reviewed_at) = ? {deck_filter}""",
        [today] + params_deck,
    ).fetchone()[0]

    new_today = conn.execute(
        f"""SELECT COUNT(DISTINCT rl.card_id) FROM review_log rl
            JOIN cards c ON c.id = rl.card_id
            WHERE date(rl.reviewed_at) = ? AND c.state IN ('new','learning') {deck_filter}""",
        [today] + params_deck,
    ).fetchone()[0]

    streak = _calc_streak(conn, deck_id)

    # Reviews per day — last 14 days (oldest first)
    day_rows = conn.execute(
        f"""SELECT date(rl.reviewed_at) as d, COUNT(*) as cnt
            FROM review_log rl
            JOIN cards c ON c.id = rl.card_id
            WHERE 1=1 {deck_filter}
            GROUP BY d ORDER BY d DESC LIMIT 14""",
        params_deck,
    ).fetchall()
    reviews_by_day = [{"date": r["d"], "count": r["cnt"]} for r in reversed(day_rows)]

    # Card state totals
    state_rows = conn.execute(
        f"""SELECT c.state, COUNT(*) as cnt
            FROM cards c
            WHERE 1=1 {deck_filter}
            GROUP BY c.state""",
        params_deck,
    ).fetchall()
    state_counts = {r["state"]: r["cnt"] for r in state_rows}

    conn.close()
    return {
        "total_words": total_words,
        "reviews_today": reviews_today,
        "new_today": new_today,
        "streak_days": streak,
        "reviews_by_day": reviews_by_day,
        "state_counts": state_counts,
    }


# ---------------------------------------------------------------------------
# API cost tracking
# ---------------------------------------------------------------------------

# Prices per million tokens (USD) as of 2026
_MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
    # Zhipu (glm-4-flash is free)
    "glm-4-flash":               {"input": 0.00,  "output": 0.00},
    "glm-4-air":                 {"input": 0.06,  "output": 0.06},
    # DeepSeek
    "deepseek-chat":             {"input": 0.28,  "output": 0.42},
    "deepseek-reasoner":         {"input": 0.50,  "output": 2.18},
    # Qwen / DashScope
    "qwen-turbo":                {"input": 0.065, "output": 0.26},
    "qwen-plus":                 {"input": 0.40,  "output": 1.20},
}


def log_api_call(model: str, input_tokens: int, output_tokens: int,
                 purpose: str = "story") -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO api_call_log (model, input_tokens, output_tokens, purpose) VALUES (?, ?, ?, ?)",
        (model, input_tokens, output_tokens, purpose),
    )
    conn.commit()
    conn.close()


def get_api_costs() -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM api_call_log ORDER BY called_at DESC"
    ).fetchall()
    conn.close()

    calls = []
    total_cost = 0.0
    for r in rows:
        r = dict(r)
        pricing = _MODEL_PRICING.get(r["model"], {"input": 0.0, "output": 0.0})
        cost = (r["input_tokens"] * pricing["input"] +
                r["output_tokens"] * pricing["output"]) / 1_000_000
        r["cost"] = round(cost, 6)
        total_cost += cost
        calls.append(r)

    return {"calls": calls, "total_cost": round(total_cost, 6)}


def _calc_streak(conn: sqlite3.Connection, deck_id: int | None) -> int:
    deck_filter = "AND c.deck_id = ?" if deck_id else ""
    params = [deck_id] if deck_id else []
    rows = conn.execute(
        f"""SELECT DISTINCT date(rl.reviewed_at) as d
            FROM review_log rl
            JOIN cards c ON c.id = rl.card_id
            WHERE 1=1 {deck_filter}
            ORDER BY d DESC""",
        params,
    ).fetchall()
    if not rows:
        return 0
    streak = 0
    today = date.today()
    for i, row in enumerate(rows):
        expected = (today - __import__("datetime").timedelta(days=i)).isoformat()
        if row["d"] == expected:
            streak += 1
        else:
            break
    return streak

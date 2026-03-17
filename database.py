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


def init_db() -> None:
    os.makedirs("data", exist_ok=True)
    with open(SCHEMA_PATH, "r") as f:
        schema = f.read()
    conn = get_db()
    conn.executescript(schema)
    conn.commit()

    # Migrations for existing databases
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(words)").fetchall()}
    if "notes" not in cols:
        conn.execute("ALTER TABLE words ADD COLUMN notes TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_call_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        called_at     TEXT NOT NULL DEFAULT (datetime('now')),
        model         TEXT NOT NULL,
        input_tokens  INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        purpose       TEXT NOT NULL DEFAULT 'story'
    )""")
    conn.commit()

    # Ensure presets + default deck exist
    _ensure_presets(conn)
    preset_id = conn.execute("SELECT id FROM deck_presets WHERE is_default = 1 LIMIT 1").fetchone()["id"]
    _ensure_deck(conn, "Default", parent_id=None, preset_id=preset_id)
    conn.commit()
    conn.close()


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
            bury_siblings, randomize_story_order, leech_threshold, leech_action)
           VALUES (:name, :new_per_day, :reviews_per_day,
                   :learning_steps, :graduating_interval, :easy_interval,
                   :relearning_steps, :minimum_interval, :insertion_order,
                   :bury_siblings, :randomize_story_order, :leech_threshold, :leech_action)""",
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
    rows = conn.execute("SELECT * FROM decks ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_deck_tree() -> list[dict]:
    decks = get_all_decks()
    by_id = {d["id"]: {**d, "children": []} for d in decks}
    roots = []
    for d in by_id.values():
        if d["parent_id"] is None:
            roots.append(d)
        else:
            parent = by_id.get(d["parent_id"])
            if parent:
                parent["children"].append(d)
    return roots


def rename_deck(deck_id: int, name: str) -> None:
    conn = get_db()
    conn.execute("UPDATE decks SET name = ? WHERE id = ?", (name, deck_id))
    conn.commit()
    conn.close()


def delete_deck(deck_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
    conn.commit()
    conn.close()


def get_default_deck_id() -> int:
    conn = get_db()
    row = conn.execute("SELECT id FROM decks WHERE name = 'Default'").fetchone()
    if row:
        conn.close()
        return row["id"]
    preset_id = _ensure_default_preset(conn)
    deck_id = _ensure_deck(conn, "Default", None, preset_id)
    conn.commit()
    conn.close()
    return deck_id


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


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

def insert_word(word: dict) -> int:
    """INSERT OR IGNORE. Returns the word id whether inserted or already existed."""
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO words
           (word_zh, pinyin, definition, pos, hsk_level,
            traditional, definition_zh, source)
           VALUES (:word_zh, :pinyin, :definition, :pos, :hsk_level,
                   :traditional, :definition_zh, :source)""",
        word,
    )
    conn.commit()
    row = conn.execute("SELECT id FROM words WHERE word_zh = ?", (word["word_zh"],)).fetchone()
    conn.close()
    return row["id"]


def get_word(word_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_word_by_zh(word_zh: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM words WHERE word_zh = ?", (word_zh,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_words_in_deck(deck_id: int) -> list[dict]:
    """Words that have at least one card in this deck."""
    conn = get_db()
    rows = conn.execute(
        """SELECT DISTINCT w.* FROM words w
           JOIN cards c ON c.word_id = w.id
           WHERE c.deck_id = ?""",
        (deck_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_word_full(word_id: int) -> dict | None:
    """Returns word + examples list + characters list with full char details."""
    word = get_word(word_id)
    if not word:
        return None
    word["examples"] = get_word_examples(word_id)
    word["characters"] = get_word_characters(word_id)
    return word


# ---------------------------------------------------------------------------
# Word examples
# ---------------------------------------------------------------------------

def insert_word_example(word_id: int, example_zh: str,
                        example_pinyin: str | None,
                        example_de: str | None,
                        position: int) -> int:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO word_examples
           (word_id, example_zh, example_pinyin, example_de, position)
           VALUES (?, ?, ?, ?, ?)""",
        (word_id, example_zh, example_pinyin, example_de, position),
    )
    conn.commit()
    ex_id = cur.lastrowid
    conn.close()
    return ex_id


def get_word_examples(word_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM word_examples WHERE word_id = ? ORDER BY position",
        (word_id,),
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
           (char, traditional, pinyin, hsk_level, etymology, other_meanings, compounds)
           VALUES (:char, :traditional, :pinyin, :hsk_level,
                   :etymology, :other_meanings, :compounds)
           ON CONFLICT(char) DO UPDATE SET
               traditional    = excluded.traditional,
               pinyin         = excluded.pinyin,
               hsk_level      = excluded.hsk_level,
               etymology      = COALESCE(excluded.etymology, etymology),
               other_meanings = COALESCE(excluded.other_meanings, other_meanings),
               compounds      = COALESCE(excluded.compounds, compounds)""",
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


def insert_word_character(word_id: int, char_id: int,
                          position: int,
                          meaning_in_context: str | None) -> None:
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO word_characters
           (word_id, char_id, position, meaning_in_context)
           VALUES (?, ?, ?, ?)""",
        (word_id, char_id, position, meaning_in_context),
    )
    conn.commit()
    conn.close()


def get_word_characters(word_id: int) -> list[dict]:
    """Returns characters in position order, joined with full character details."""
    conn = get_db()
    rows = conn.execute(
        """SELECT wc.position, wc.meaning_in_context,
                  c.id as char_id, c.char, c.traditional, c.pinyin,
                  c.hsk_level, c.etymology, c.other_meanings, c.compounds
           FROM word_characters wc
           JOIN characters c ON c.id = wc.char_id
           WHERE wc.word_id = ?
           ORDER BY wc.position""",
        (word_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
                  w.traditional, w.definition_zh,
                  p.learning_steps, p.graduating_interval, p.easy_interval,
                  p.relearning_steps, p.minimum_interval,
                  p.leech_threshold, p.leech_action,
                  p.new_per_day, p.reviews_per_day
           FROM cards c
           JOIN words w ON w.id = c.word_id
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


def get_due_cards(deck_id: int, category: str) -> list[dict]:
    """All due cards for a category — used for story generation."""
    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()

    # Count new cards already reviewed today to enforce daily limit.
    # We track "introduced today" = first-ever review is today, regardless of
    # current state (a card transitions away from 'new' after the first review).
    preset = get_preset_for_deck(deck_id)
    new_limit = preset["new_per_day"]
    new_done_today = _count_new_introduced_today(conn, deck_id, category, today)
    new_remaining = max(0, new_limit - new_done_today)

    rows = conn.execute(
        """SELECT c.*, w.word_zh, w.pinyin, w.definition, w.pos,
                  w.hsk_level, w.traditional, w.definition_zh
           FROM cards c
           JOIN words w ON w.id = c.word_id
           WHERE c.deck_id = ?
             AND c.category = ?
             AND c.state != 'suspended'
             AND (c.buried_until IS NULL OR c.buried_until < ?)
             AND (
               (c.state IN ('learning', 'relearn') AND c.due <= ?)
               OR (c.state = 'review' AND c.due <= ?)
               OR (c.state = 'new' AND c.due <= ?)
             )
           ORDER BY
             CASE
               WHEN c.state IN ('learning', 'relearn') THEN 0
               WHEN c.state = 'review' THEN 1
               ELSE 2
             END,
             c.due""",
        (deck_id, category, today, now, today, today),
    ).fetchall()

    conn.close()

    insertion_order = preset.get("insertion_order", "sequential")
    prioritized = [dict(r) for r in rows if r["state"] != "new"]
    new_cards = [dict(r) for r in rows if r["state"] == "new"]
    if insertion_order == "random":
        import random
        random.shuffle(new_cards)
    return prioritized + new_cards[:new_remaining]


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

    buried_filter = "AND (c.buried_until IS NULL OR c.buried_until < ?)"

    learning = conn.execute(
        f"""SELECT COUNT(*) FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND c.state IN ('learning', 'relearn') AND c.due <= ?
             {buried_filter}""",
        (deck_id, category, now, today),
    ).fetchone()[0]

    review = conn.execute(
        f"""SELECT COUNT(*) FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND c.state = 'review' AND c.due <= ?
             {buried_filter}""",
        (deck_id, category, today, today),
    ).fetchone()[0]

    new_available = conn.execute(
        f"""SELECT COUNT(*) FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND c.state = 'new' AND c.due <= ?
             {buried_filter}""",
        (deck_id, category, today, today),
    ).fetchone()[0]

    conn.close()
    return {
        "new": min(new_available, new_remaining),
        "learning": learning,
        "review": review,
    }


def update_word(word_id: int, fields: dict) -> None:
    allowed = {"word_zh", "pinyin", "definition", "pos", "traditional", "definition_zh", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE words SET {sets} WHERE id=?", (*updates.values(), word_id))
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


def get_descendant_leaf_deck_ids(deck_id: int, category: str | None = None) -> list[int]:
    """Return all category-leaf deck IDs under deck_id (depth-first). Optionally filter by category."""
    conn = get_db()
    rows = conn.execute("SELECT id, parent_id, category FROM decks").fetchall()
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
    all_cards.sort(key=lambda c: (
        0 if c["state"] in ("learning", "relearn") else
        1 if c["state"] == "review" else 2,
        c["due"]
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


def get_due_cards_multi(deck_ids: list[int], category: str) -> list[dict]:
    """Due cards across multiple decks, merged and priority-sorted."""
    all_cards = []
    for deck_id in deck_ids:
        all_cards.extend(get_due_cards(deck_id, category))
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


def bury_siblings(word_id: int, reviewed_category: str) -> None:
    """Bury all other-category cards for this word for the rest of today."""
    today = date.today().isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE cards SET buried_until = ? WHERE word_id = ? AND category != ?",
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


def get_words_for_browse() -> list[dict]:
    """Return all words that have cards, with embedded card states per category."""
    sql = """
        SELECT w.id, w.word_zh, w.pinyin, w.definition, w.pos, w.hsk_level,
               c.id as card_id, c.category, c.state, c.interval, c.ease,
               c.due, c.lapses, c.step_index, c.deck_id,
               d.name as deck_name
        FROM words w
        JOIN cards c ON c.word_id = w.id
        JOIN decks d ON d.id = c.deck_id
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
                "cards": [],
            }
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
    """Return word IDs split into primary (word/def match) and secondary (example/notes match)."""
    like = f"%{q}%"
    conn = get_db()
    primary_ids = {r["id"] for r in conn.execute(
        """SELECT DISTINCT w.id FROM words w
           JOIN cards c ON c.word_id = w.id
           WHERE w.word_zh LIKE ? OR w.pinyin LIKE ?
              OR w.definition LIKE ? OR w.definition_zh LIKE ?""",
        (like, like, like, like),
    ).fetchall()}
    secondary_ids = {r["id"] for r in conn.execute(
        """SELECT DISTINCT w.id FROM words w
           JOIN cards c ON c.word_id = w.id
           LEFT JOIN word_examples we ON we.word_id = w.id
           WHERE we.example_zh LIKE ? OR we.example_de LIKE ? OR w.notes LIKE ?""",
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
           WHERE c.word_id = ? ORDER BY c.category""",
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
              JOIN words w ON w.id = c.word_id
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
            """INSERT INTO sentences (story_id, word_id, position, sentence_zh, sentence_en)
               VALUES (?, ?, ?, ?, ?)""",
            (story_id, s["word_id"], s["position"], s["sentence_zh"], s["sentence_en"]),
        )
    conn.commit()
    conn.close()
    return story_id


def get_sentence_for_word(story_id: int, word_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sentences WHERE story_id = ? AND word_id = ?",
        (story_id, word_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_story_sentences(story_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT s.*, w.word_zh
           FROM sentences s JOIN words w ON w.id = s.word_id
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
        total_words = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]

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

# Prices per million tokens (USD) as of 2025
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
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

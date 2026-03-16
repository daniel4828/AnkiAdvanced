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
            ("Anki Default", 9999, 9999, "1m 10m", 4, 9, "10", 1, "sequential", 1, 0, 8, "suspend"),
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
        "learning_steps": "1m 10m",
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
    """Get deck id by name, creating it (sharing the default preset) if it doesn't exist."""
    conn = get_db()
    row = conn.execute("SELECT id FROM decks WHERE name = ?", (name,)).fetchone()
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
        (deck_id, category, now, today, today),
    ).fetchall()

    # Bury siblings: exclude cards whose word was already reviewed today in another category
    if preset.get("bury_siblings", 1):
        buried_word_ids = {
            r[0] for r in conn.execute(
                """SELECT DISTINCT c.word_id FROM cards c
                   JOIN review_log rl ON rl.card_id = c.id
                   WHERE c.category != ?
                     AND date(rl.reviewed_at) = ?""",
                (category, today),
            ).fetchall()
        }
        rows = [r for r in rows if r["word_id"] not in buried_word_ids]

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

    learning = conn.execute(
        """SELECT COUNT(*) FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND c.state IN ('learning', 'relearn') AND c.due <= ?""",
        (deck_id, category, now),
    ).fetchone()[0]

    review = conn.execute(
        """SELECT COUNT(*) FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND c.state = 'review' AND c.due <= ?""",
        (deck_id, category, today),
    ).fetchone()[0]

    new_available = conn.execute(
        """SELECT COUNT(*) FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND c.state = 'new' AND c.due <= ?""",
        (deck_id, category, today),
    ).fetchone()[0]

    conn.close()
    return {
        "new": min(new_available, new_remaining),
        "learning": learning,
        "review": review,
    }


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
        "SELECT * FROM sentences WHERE story_id = ? ORDER BY position",
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

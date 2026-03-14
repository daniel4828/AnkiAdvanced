import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "srs.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        # Migrations for existing DBs
        for col, definition in [
            ("learning_step", "INTEGER DEFAULT 0"),
            ("learning_due",  "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE cards ADD COLUMN {col} {definition}")
            except Exception:
                pass


# ── Words ──────────────────────────────────────────────────────────────────────

def insert_word(word_zh, pinyin, definition, pos, frequency, example_zh, example_en,
                date_added, source="language_reactor", known=0, hsk_level=5,
                traditional=None, definition_zh=None, cultural_note=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO words
               (word_zh, traditional, pinyin, definition, definition_zh, cultural_note,
                pos, frequency, example_zh, example_en, date_added, source, known, hsk_level)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (word_zh, traditional, pinyin, definition, definition_zh, cultural_note,
             pos, frequency, example_zh, example_en, date_added, source, known, hsk_level),
        )
        return conn.execute("SELECT id FROM words WHERE word_zh = ?", (word_zh,)).fetchone()["id"]


def insert_word_example(word_id: int, zh: str, pinyin: str, translation: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO word_examples (word_id, zh, pinyin, translation) VALUES (?, ?, ?, ?)",
            (word_id, zh, pinyin, translation),
        )


def insert_word_character(word_id: int, char: str, traditional: str, pinyin: str, hsk: str,
                          detailed_analysis: bool, meaning_in_context: str,
                          other_meanings: str, etymology: str, etymology_example: str, note: str) -> int:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO word_characters
               (word_id, char, traditional, pinyin, hsk, detailed_analysis,
                meaning_in_context, other_meanings, etymology, etymology_example, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (word_id, char, traditional, pinyin, hsk, int(detailed_analysis),
             meaning_in_context, other_meanings, etymology, etymology_example, note),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_character_compound(character_id: int, simplified: str, pinyin: str, meaning: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO character_compounds (character_id, simplified, pinyin, meaning) VALUES (?, ?, ?, ?)",
            (character_id, simplified, pinyin, meaning),
        )


def get_word(word_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()


def get_word_detail(word_id: int) -> dict:
    """Return word with all examples, characters, and compounds."""
    with get_conn() as conn:
        word = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        if not word:
            return {}
        examples = conn.execute(
            "SELECT * FROM word_examples WHERE word_id = ? ORDER BY id", (word_id,)
        ).fetchall()
        characters = conn.execute(
            "SELECT * FROM word_characters WHERE word_id = ? ORDER BY id", (word_id,)
        ).fetchall()
        chars_with_compounds = []
        for ch in characters:
            compounds = conn.execute(
                "SELECT * FROM character_compounds WHERE character_id = ? ORDER BY id", (ch["id"],)
            ).fetchall()
            chars_with_compounds.append({"char": dict(ch), "compounds": [dict(c) for c in compounds]})
        return {
            "word": dict(word),
            "examples": [dict(e) for e in examples],
            "characters": chars_with_compounds,
        }


def get_known_words_by_hsk(max_level: int) -> list[str]:
    """Return word_zh list for known words up to given HSK level."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT word_zh FROM words WHERE known = 1 AND hsk_level <= ? ORDER BY hsk_level, word_zh",
            (max_level,),
        ).fetchall()
        return [r["word_zh"] for r in rows]


def get_all_words() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM words ORDER BY date_added DESC").fetchall()


# ── Cards ──────────────────────────────────────────────────────────────────────

def insert_card(word_id: int, category: str, state: str = "new"):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO cards (word_id, category, state)
               VALUES (?, ?, ?)""",
            (word_id, category, state),
        )


def get_due_cards(category: str, date: str) -> list[sqlite3.Row]:
    """New and review cards due on or before date (excludes learning — those have their own queue)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT c.*, w.word_zh, w.pinyin, w.definition, w.pos, w.frequency
               FROM cards c
               JOIN words w ON w.id = c.word_id
               WHERE c.category = ?
                 AND c.state IN ('new', 'review')
                 AND c.due_date <= ?
               ORDER BY
                 CASE c.state WHEN 'review' THEN 0 ELSE 1 END,
                 c.due_date ASC""",
            (category, date),
        ).fetchall()


def get_due_learning_cards(category: str) -> list[sqlite3.Row]:
    """Learning cards whose minute-timer has expired — these jump the queue."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT c.*, w.word_zh, w.pinyin, w.definition, w.pos, w.frequency
               FROM cards c
               JOIN words w ON w.id = c.word_id
               WHERE c.category = ?
                 AND c.state = 'learning'
                 AND c.learning_due <= datetime('now')
               ORDER BY c.learning_due ASC""",
            (category,),
        ).fetchall()


def get_sibling_cards(word_id: int, exclude_category: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cards WHERE word_id = ? AND category != ?",
            (word_id, exclude_category),
        ).fetchall()


def update_card(card_id: int, state: str, due_date: str, interval: int, ease: float,
                repetitions: int, learning_step: int = 0, learning_due: str = None):
    with get_conn() as conn:
        conn.execute(
            """UPDATE cards
               SET state=?, due_date=?, interval=?, ease=?, repetitions=?,
                   learning_step=?, learning_due=?
               WHERE id=?""",
            (state, due_date, interval, ease, repetitions,
             learning_step, learning_due, card_id),
        )


def push_sibling_due_dates(word_id: int, reviewed_category: str, new_due_date: str):
    """Push siblings forward so only one card per word appears per day."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE cards SET due_date = MAX(due_date, ?)
               WHERE word_id = ? AND category != ? AND state != 'locked'""",
            (new_due_date, word_id, reviewed_category),
        )


def unlock_creating_card(word_id: int):
    """Unlock creating card when both listening and reading have been reviewed at least once."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT category, repetitions FROM cards
               WHERE word_id = ? AND category IN ('listening', 'reading')""",
            (word_id,),
        ).fetchall()
        rep_map = {r["category"]: r["repetitions"] for r in rows}
        if rep_map.get("listening", 0) >= 1 and rep_map.get("reading", 0) >= 1:
            conn.execute(
                """UPDATE cards SET state = 'new', due_date = date('now')
                   WHERE word_id = ? AND category = 'creating' AND state = 'locked'""",
                (word_id,),
            )


def reset_all_cards():
    """Reset all cards to new state, due today. Clears review logs and daily content."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE cards SET
                state = CASE WHEN category = 'creating' THEN 'locked' ELSE 'new' END,
                due_date = date('now'),
                interval = 1, ease = 2.5, repetitions = 0,
                learning_step = 0, learning_due = NULL
        """)
        conn.execute("DELETE FROM review_log")
        conn.execute("DELETE FROM daily_content")


def set_cards_to_review(word_id: int, interval: int = 7):
    with get_conn() as conn:
        conn.execute(
            """UPDATE cards SET state = 'review', interval = ?, repetitions = 1
               WHERE word_id = ?""",
            (interval, word_id),
        )


# ── Daily content ──────────────────────────────────────────────────────────────

def get_daily_content(date: str, category: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM daily_content WHERE date = ? AND category = ?",
            (date, category),
        ).fetchone()


def save_daily_content(date: str, category: str, word_ids: str, sentences_zh: str, sentences_en: str):
    content_zh = " ".join(__import__("json").loads(sentences_zh))
    content_en = " ".join(__import__("json").loads(sentences_en))
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO daily_content
               (date, category, word_ids, content_zh, content_en, sentences_zh, sentences_en)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (date, category, word_ids, content_zh, content_en, sentences_zh, sentences_en),
        )


def get_card(card_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()


# ── Review log ─────────────────────────────────────────────────────────────────

def log_review(card_id: int, rating: int, user_response: str = None, ai_score: int = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO review_log (card_id, rating, user_response, ai_score)
               VALUES (?, ?, ?, ?)""",
            (card_id, rating, user_response, ai_score),
        )


# ── Stats ──────────────────────────────────────────────────────────────────────

def get_stats(today: str) -> dict:
    with get_conn() as conn:
        total_words = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        due_counts = {}
        for cat in ("listening", "reading", "creating"):
            due_counts[cat] = conn.execute(
                """SELECT COUNT(*) FROM cards
                   WHERE category = ? AND state != 'locked' AND due_date <= ?""",
                (cat, today),
            ).fetchone()[0]
        total_cards = conn.execute("SELECT COUNT(*) FROM cards WHERE state != 'locked'").fetchone()[0]
        locked = conn.execute("SELECT COUNT(*) FROM cards WHERE state = 'locked'").fetchone()[0]
        return {
            "total_words": total_words,
            "total_cards": total_cards,
            "locked_creating": locked,
            "due_today": due_counts,
        }

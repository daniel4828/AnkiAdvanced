import sqlite3
from .core import get_db


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
                 sentences: list[dict], prompt_text: str | None = None,
                 topic: str | None = None) -> int:
    """Always inserts a new story row. Returns story_id."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO stories (date, category, deck_id, prompt_text, topic) VALUES (?, ?, ?, ?, ?)",
        (date_str, category, deck_id, prompt_text, topic),
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
        """SELECT s.*, w.word_zh, w.definition
           FROM story_sentences s JOIN entries w ON w.id = s.word_id
           WHERE s.story_id = ? ORDER BY s.position""",
        (story_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_due_cards_unified(deck_id: int) -> list[dict]:
    """Collect due cards from all 3 categories for a unified story, deduplicated by word_id."""
    from .cards import get_due_cards, get_due_cards_multi, get_descendant_leaf_deck_ids
    from .decks import get_deck

    def _leaf_ids(cat: str) -> list[int]:
        deck = get_deck(deck_id)
        if deck["category"] is None:
            return get_descendant_leaf_deck_ids(deck_id, cat)
        return [deck_id]

    seen: set[int] = set()
    result: list[dict] = []
    for cat in ("listening", "reading", "creating"):
        ids = _leaf_ids(cat)
        if not ids:
            continue
        cards = (get_due_cards_multi(ids, cat) if len(ids) > 1
                 else get_due_cards(ids[0], cat))
        for c in cards:
            if c.get("note_type") == "sentence":
                continue
            if c["word_id"] not in seen:
                seen.add(c["word_id"])
                result.append(c)
    return result

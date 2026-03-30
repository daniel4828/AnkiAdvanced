import sqlite3
from .core import get_db
from .cards import get_card


# ---------------------------------------------------------------------------
# Browse / search
# ---------------------------------------------------------------------------

def get_words_for_browse() -> list[dict]:
    """Return all entries (with or without cards), with embedded card states per category."""
    sql = """
        SELECT w.id, w.word_zh, w.pinyin, w.definition, w.definition_de, w.pos, w.hsk_level, w.note_type,
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
                "definition_de": r["definition_de"],
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
              OR w.definition LIKE ? OR w.definition_zh LIKE ?
              OR w.definition_de LIKE ?)""",
        (like, like, like, like, like),
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


def suspend_card(card_id: int) -> None:
    """Unconditionally suspend a card (used by leech detection)."""
    conn = get_db()
    conn.execute("UPDATE cards SET state='suspended' WHERE id=?", (card_id,))
    conn.commit()
    conn.close()


def toggle_card_suspension(card_id: int) -> dict:
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

    sql = f"""SELECT c.*, w.word_zh, w.pinyin, w.definition, w.definition_de, w.pos,
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

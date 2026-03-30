import sqlite3
from .core import get_db, _ensure_default_preset, _ensure_sentences_leaf_decks


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

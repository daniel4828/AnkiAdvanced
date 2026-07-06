import re
import sqlite3
from datetime import date

from .core import anki_today, get_db, _ensure_default_preset


# ---------------------------------------------------------------------------
# Decks
# ---------------------------------------------------------------------------

def _resolve_deck_lang(conn, parent_id: int | None, lang: str | None) -> str:
    """Deck language: explicit value wins, else inherit from parent, else 'zh'."""
    if lang:
        return lang
    if parent_id is not None:
        row = conn.execute("SELECT lang FROM decks WHERE id = ?", (parent_id,)).fetchone()
        if row and row["lang"]:
            return row["lang"]
    return "zh"


def insert_deck(name: str, parent_id: int | None, preset_id: int,
                category: str | None = None, lang: str | None = None) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO decks (name, parent_id, preset_id, category, lang) VALUES (?, ?, ?, ?, ?)",
        (name, parent_id, preset_id, category, _resolve_deck_lang(conn, parent_id, lang)),
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


def get_deck_lang(deck_id: int) -> str:
    """Language of a deck; falls back to 'zh' for missing decks/legacy rows."""
    conn = get_db()
    row = conn.execute("SELECT lang FROM decks WHERE id = ?", (deck_id,)).fetchone()
    conn.close()
    return row["lang"] if row and row["lang"] else "zh"


def get_available_langs() -> list[str]:
    """Distinct langs among non-deleted, non-root decks (used for the frontend tab bar).

    The root 'All' deck is excluded since its own lang is a historical artifact
    (always 'zh', regardless of what languages actually live under it).
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT lang FROM decks WHERE deleted_at IS NULL AND parent_id IS NOT NULL"
    ).fetchall()
    conn.close()
    return sorted({r["lang"] or "zh" for r in rows})


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
    return roots


def set_deck_new_review_order_override(deck_id: int, value: str | None) -> None:
    conn = get_db()
    conn.execute("UPDATE decks SET new_review_order_override = ? WHERE id = ?", (value, deck_id))
    conn.commit()
    conn.close()


def set_deck_bury_quick_mode(deck_id: int, value: str) -> None:
    conn = get_db()
    conn.execute("UPDATE decks SET bury_quick_mode = ? WHERE id = ?", (value, deck_id))
    conn.commit()
    conn.close()


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
                       category: str | None = None, lang: str | None = None) -> int:
    """Get deck id by (name, parent_id), creating it if it doesn't exist."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM decks WHERE name = ? AND parent_id IS ? AND deleted_at IS NULL",
        (name, parent_id),
    ).fetchone()
    if row:
        conn.close()
        return row["id"]
    preset_id = _ensure_default_preset(conn)
    cur = conn.execute(
        "INSERT INTO decks (name, parent_id, preset_id, category, lang) VALUES (?, ?, ?, ?, ?)",
        (name, parent_id, preset_id, category, _resolve_deck_lang(conn, parent_id, lang)),
    )
    conn.commit()
    deck_id = cur.lastrowid
    conn.close()
    return deck_id




def get_all_deck_id() -> int | None:
    """Return the id of the top-level 'All' deck, or None if it doesn't exist yet."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM decks WHERE name = 'All' AND parent_id IS NULL AND deleted_at IS NULL LIMIT 1"
    ).fetchone()
    conn.close()
    return row["id"] if row else None


def get_or_create_deck_path(path: str, lang: str | None = None) -> int:
    """Parse an Anki-style 'Parent::Child::Leaf' path and ensure all decks exist.

    Returns the id of the deepest (leaf) deck. Roots are placed under 'All'.
    `lang`, if given, is applied to every newly-created segment (existing decks
    keep their own lang); `None` keeps the normal parent-inheritance behavior.
    """
    segments = [s.strip() for s in path.split("::") if s.strip()]
    if not segments:
        raise ValueError(f"Empty deck path: {path!r}")
    parent_id = get_all_deck_id()
    for segment in segments:
        parent_id = get_or_create_deck(segment, parent_id=parent_id, lang=lang)
    return parent_id


def get_or_create_category_decks(parent_deck_id: int, parent_name: str) -> dict:
    """Ensure the three per-category leaf decks exist under a date/daily parent deck,
    and return {category: deck_id}.

    The rest of the app expects cards to live in these category leaf decks
    ('<name> · Listening/Reading/Creating', each carrying a `category`), not directly
    in the category-less parent — due counts and review queues are keyed by
    (deck_id, category). This is the runtime twin of importer._make_leaf_decks.
    """
    return {
        "listening": get_or_create_deck(
            f"{parent_name} · Listening", parent_id=parent_deck_id, category="listening"
        ),
        "reading": get_or_create_deck(
            f"{parent_name} · Reading", parent_id=parent_deck_id, category="reading"
        ),
        "creating": get_or_create_deck(
            f"{parent_name} · Creating", parent_id=parent_deck_id, category="creating"
        ),
    }


def get_or_create_saved_deck() -> int:
    """The fixed 'Saved' staging deck: holds suspended compound words the user
    set aside for later (see /api/save-word). Promoting moves them to a Daily deck."""
    return get_or_create_deck_path("Saved")


# ---------------------------------------------------------------------------
# Future-dated daily deck locking
#
# Daily decks (the date-named children of a 'daily'/'Daily' root) are special:
# a deck dated in the future must not be reviewable until its date arrives. We
# detect them by parsing the date out of the deck name and comparing to today.
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_MD_DATE_RE = re.compile(r"^(\d{1,2})[-_](\d{1,2})")


def parse_daily_deck_date(name: str, today: date | None = None) -> date | None:
    """Parse the date a daily deck name encodes, or None if it isn't date-like.

    Handles ISO 'YYYY-MM-DD' (quick-add decks) and 'MM-DD' / 'MM_DD' (legacy daily
    decks), each optionally followed by a suffix (e.g. '05-08_kouyu'). For the
    year-less MM-DD form the year is inferred as the occurrence nearest to today,
    so dates near a year boundary resolve sensibly.
    """
    if not name:
        return None
    name = name.strip()
    m = _ISO_DATE_RE.match(name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _MD_DATE_RE.match(name)
    if m:
        today = today or anki_today()
        month, day = int(m.group(1)), int(m.group(2))
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if abs((candidate - today).days) <= 183:
                return candidate
        return None
    return None


def get_locked_deck_ids() -> dict[int, str]:
    """Return {deck_id: unlock_date_iso} for every deck locked because it is a
    future-dated daily deck (a date-named child of a 'daily' root) or a descendant
    of one. Locked decks contribute no due cards and cannot be reviewed until then.
    """
    today = anki_today()
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, parent_id FROM decks WHERE deleted_at IS NULL"
    ).fetchall()
    conn.close()

    children: dict[int | None, list] = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r)
    daily_root_ids = {
        r["id"] for r in rows if (r["name"] or "").strip().lower() == "daily"
    }

    locked: dict[int, str] = {}
    for r in rows:
        if r["parent_id"] not in daily_root_ids:
            continue
        d = parse_daily_deck_date(r["name"], today)
        if not d or d <= today:
            continue
        iso = d.isoformat()
        # Lock this date deck and every descendant (its category leaf decks).
        stack = [r["id"]]
        while stack:
            cur = stack.pop()
            locked[cur] = iso
            for kid in children.get(cur, []):
                stack.append(kid["id"])
    return locked


def get_word_deck_names(word_id: int) -> list[str]:
    """Return the unique deck names (leaf only) where cards for this word live."""
    conn = get_db()
    rows = conn.execute(
        """SELECT DISTINCT d.name FROM cards c
           JOIN decks d ON d.id = c.deck_id
           WHERE c.word_id = ? AND c.deleted_at IS NULL AND d.deleted_at IS NULL""",
        (word_id,),
    ).fetchall()
    conn.close()
    return [r["name"] for r in rows]

import sqlite3
from .core import get_db


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
        "category_order": "listening,reading,creating",
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
            bury_quick_mode, category_order)
           VALUES (:name, :new_per_day, :reviews_per_day,
                   :learning_steps, :graduating_interval, :easy_interval,
                   :relearning_steps, :minimum_interval, :insertion_order,
                   :bury_siblings, :randomize_story_order, :leech_threshold, :leech_action,
                   :new_gather_order, :new_sort_order, :new_review_order,
                   :interday_learning_review_order, :review_sort_order,
                   :bury_new_siblings, :bury_review_siblings, :bury_interday_siblings,
                   :bury_quick_mode, :category_order)""",
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
        "bury_quick_mode", "category_order",
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

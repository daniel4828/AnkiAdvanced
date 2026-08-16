"""In-app AI dictionary storage (#746): every lookup's full structured result
is saved so the /dict page can show a searchable history below the query
box. All SQL for this feature lives here — routes/dictionary.py only calls
into this module.
"""
from .core import get_db


def save_dict_query(
    query: str,
    lang: str,
    input_lang: str | None,
    kind: str | None,
    headline: str | None,
    result_json: str,
    model: str | None,
) -> int:
    """Insert a completed lookup and return its new row id."""
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO dict_queries (query, lang, input_lang, kind, headline, result_json, model)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (query, lang, input_lang, kind, headline, result_json, model),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_dict_query(
    qid: int,
    input_lang: str | None,
    kind: str | None,
    headline: str | None,
    result_json: str,
    model: str | None,
) -> bool:
    """Overwrite one stored lookup in place (#777's Repeat button) and return
    whether the row existed. `query`/`lang` are deliberately not touched — a
    repeat re-asks the *same* question, so only the answer may change.
    created_at is refreshed so the history list still sorts by "last answered".
    """
    conn = get_db()
    cur = conn.execute(
        """UPDATE dict_queries
           SET input_lang = ?, kind = ?, headline = ?, result_json = ?, model = ?,
               created_at = datetime('now','localtime')
           WHERE id = ?""",
        (input_lang, kind, headline, result_json, model, qid),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def get_dict_query(qid: int) -> dict | None:
    """Full row for one lookup, including the raw result_json string — the
    caller (routes/dictionary.py) is responsible for json.loads-ing it."""
    conn = get_db()
    row = conn.execute("SELECT * FROM dict_queries WHERE id = ?", (qid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_dict_queries(q: str | None = None, limit: int = 50) -> list[dict]:
    """History list: only the columns the list view needs (not result_json —
    parsing 50 JSON blobs just to show a headline would be wasteful, which is
    why headline is denormalized onto the row at save time)."""
    conn = get_db()
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """SELECT id, query, input_lang, kind, headline, created_at
               FROM dict_queries
               WHERE query LIKE ? OR headline LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (like, like, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, query, input_lang, kind, headline, created_at
               FROM dict_queries ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_dict_query(qid: int) -> bool:
    """Returns whether a row was actually deleted, so the route can report a
    404 instead of pretending success on an id that never existed."""
    conn = get_db()
    cur = conn.execute("DELETE FROM dict_queries WHERE id = ?", (qid,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted

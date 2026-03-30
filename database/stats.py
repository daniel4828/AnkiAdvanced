import sqlite3
from datetime import date, datetime
from .core import get_db, anki_today


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats(deck_id: int | None = None) -> dict:
    today = anki_today().isoformat()
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
    today = anki_today()
    for i, row in enumerate(rows):
        expected = (today - __import__("datetime").timedelta(days=i)).isoformat()
        if row["d"] == expected:
            streak += 1
        else:
            break
    return streak

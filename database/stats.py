import sqlite3
import datetime as _dt
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


def get_retention_bulk(days: int = 30) -> dict:
    """Return retention rate data for all decks, grouped by leaf deck_id.

    Returns:
        {
          "by_deck": {deck_id: {"correct": int, "total": int, "category": str|None}},
          "all":     {"correct": int, "total": int},
          "days":    int
        }
    Rating > 1 (Hard/Good/Easy) counts as correct; rating == 1 (Again) counts as wrong.

    Only counts reviews of *learned* cards — those answered in the 'review' phase
    (state='review'). Learning/relearning/new-card steps are excluded, matching
    Anki's "true retention". Legacy rows with no recorded state are excluded too.
    """
    conn = get_db()
    since = (anki_today() - _dt.timedelta(days=days)).isoformat()

    rows = conn.execute(
        """SELECT c.deck_id, d.category,
                  COUNT(*) AS total,
                  SUM(CASE WHEN rl.rating > 1 THEN 1 ELSE 0 END) AS correct
           FROM review_log rl
           JOIN cards c ON c.id = rl.card_id
           JOIN decks d ON d.id = c.deck_id
           WHERE date(datetime(rl.reviewed_at, 'localtime')) >= ?
             AND rl.state = 'review'
           GROUP BY c.deck_id""",
        [since],
    ).fetchall()
    conn.close()

    by_deck: dict = {}
    total_all = 0
    correct_all = 0
    for r in rows:
        correct = r["correct"] or 0
        total   = r["total"]   or 0
        by_deck[r["deck_id"]] = {
            "correct":  correct,
            "total":    total,
            "category": r["category"],
        }
        total_all   += total
        correct_all += correct

    return {
        "by_deck": by_deck,
        "all":     {"correct": correct_all, "total": total_all},
        "days":    days,
    }


def get_calendar_stats(days: int = 365, deck_id: int | None = None) -> dict:
    """Per-day review statistics for the home-page calendar heatmap.

    Days are bucketed by the Anki day boundary (local time, 4am cutoff) so they
    line up with the rest of the app. Returns, for each day in the window:
      - reviews / cards (distinct) studied, overall + per category
      - retention (correct = rating > 1) overall, per category, and split by
        phase (learning vs review) — both overall and per category
      - total study time (duration_ms) and the count of timed reviews (for avg)
    Plus `future`: cards scheduled for review from today onward (from cards.due).

    Legacy review rows have NULL duration_ms / state: they still count toward
    review/retention totals but are excluded from time and phase splits.
    """
    conn = get_db()
    today = anki_today()
    since = (today - _dt.timedelta(days=days)).isoformat()

    deck_filter = "AND c.deck_id = ?" if deck_id else ""
    params = [since] + ([deck_id] if deck_id else [])

    # Anki-day bucket: UTC timestamp → local → shift back past the 4am cutoff.
    day_expr = "date(datetime(rl.reviewed_at, 'localtime', '-4 hours'))"
    rows = conn.execute(
        f"""SELECT {day_expr} AS d, c.category AS cat, rl.rating AS rating,
                   rl.state AS state, rl.duration_ms AS dur, rl.card_id AS card_id
            FROM review_log rl
            JOIN cards c ON c.id = rl.card_id
            WHERE {day_expr} >= ? {deck_filter}""",
        params,
    ).fetchall()

    def _new_cat() -> dict:
        return {
            "reviews": 0, "correct": 0, "total": 0,
            "duration_ms": 0, "timed_count": 0, "_cards": set(),
            "learning": {"correct": 0, "total": 0},
            "review":   {"correct": 0, "total": 0},
        }

    by_date: dict[str, dict] = {}
    for r in rows:
        d = r["d"]
        day = by_date.get(d)
        if day is None:
            day = by_date[d] = {
                "reviews": 0, "correct": 0, "total": 0,
                "duration_ms": 0, "timed_count": 0, "_cards": set(),
                "learning": {"correct": 0, "total": 0},
                "review":   {"correct": 0, "total": 0},
                "by_cat": {},
            }
        cat = r["cat"]
        c = day["by_cat"].get(cat)
        if c is None:
            c = day["by_cat"][cat] = _new_cat()

        correct = 1 if r["rating"] > 1 else 0
        for bucket in (day, c):
            bucket["reviews"] += 1
            bucket["total"]   += 1
            bucket["correct"] += correct
            bucket["_cards"].add(r["card_id"])
            if r["dur"] is not None:
                bucket["duration_ms"] += r["dur"]
                bucket["timed_count"] += 1

        # Phase split (learning/relearn/new = "learning"; review = "review")
        phase = None
        if r["state"] in ("new", "learning", "relearn"):
            phase = "learning"
        elif r["state"] == "review":
            phase = "review"
        if phase:
            for bucket in (day, c):
                bucket[phase]["total"]   += 1
                bucket[phase]["correct"] += correct

    # Finalize: replace card sets with counts
    for day in by_date.values():
        day["cards"] = len(day.pop("_cards"))
        for c in day["by_cat"].values():
            c["cards"] = len(c.pop("_cards"))

    # Future scheduled reviews (today onward), grouped by due date + category
    future_filter = "AND deck_id = ?" if deck_id else ""
    future_params = [today.isoformat()] + ([deck_id] if deck_id else [])
    future_rows = conn.execute(
        f"""SELECT date(due) AS d, category AS cat, COUNT(*) AS cnt
            FROM cards
            WHERE state IN ('review', 'learning', 'relearn')
              AND deleted_at IS NULL
              AND date(due) >= ? {future_filter}
            GROUP BY date(due), category""",
        future_params,
    ).fetchall()
    conn.close()

    future: dict[str, dict] = {}
    for r in future_rows:
        d = r["d"]
        f = future.get(d)
        if f is None:
            f = future[d] = {"total": 0, "by_cat": {}}
        f["total"] += r["cnt"]
        f["by_cat"][r["cat"]] = r["cnt"]

    return {
        "days": days,
        "today": today.isoformat(),
        "by_date": by_date,
        "future": future,
    }


_EVOLUTION_STATES = ("new", "learning", "review", "relearn")


def _next_state(state: str, step: int, rating: int,
                n_learn: int, n_relearn: int) -> tuple[str, int]:
    """SM-2 state transition for one review (states only, no intervals)."""
    if state in ("new", "learning"):
        if rating == 1:
            return ("learning", 0)
        if rating == 2:
            return ("learning", step)
        if rating == 3:
            if step + 1 >= n_learn:
                return ("review", 0)
            return ("learning", step + 1)
        return ("review", 0)  # Easy graduates immediately
    if state == "review":
        return ("relearn", 0) if rating == 1 else ("review", 0)
    if state == "relearn":
        if rating == 1:
            return ("relearn", 0)
        if rating == 2:
            return ("relearn", step)
        if rating == 3:
            if step + 1 >= n_relearn:
                return ("review", 0)
            return ("relearn", step + 1)
        return ("review", 0)
    return (state, step)


def get_card_evolution(days: int = 365, deck_id: int | None = None) -> dict:
    """Per-day card-state counts over time, reconstructed from review_log.

    Most legacy review rows have NULL state, so the history is rebuilt by
    replaying the SM-2 state machine over each card's rating sequence (using
    the deck preset's learning/relearning step counts). Rows that *do* record
    a state recalibrate the replay, and the card's current state pins the end
    of the timeline. Before the first review a card is 'new' (from the
    entry's date_added). Suspended cards count under their pre_suspend_state
    (or stay in their last replayed state when that is unset).

    Days use the Anki boundary (local time, 4am cutoff) like get_calendar_stats.

    Returns:
        {
          "days": int, "today": "YYYY-MM-DD",
          "dates": ["YYYY-MM-DD", ...],            # oldest → today, len == days
          "series": {category: {state: [int, ...]}}  # aligned with dates
        }
    """
    conn = get_db()
    today = anki_today()
    dates = [(today - _dt.timedelta(days=days - 1 - i)).isoformat()
             for i in range(days)]

    deck_filter = "AND c.deck_id = ?" if deck_id else ""
    params = [deck_id] if deck_id else []
    day_of = "date(datetime({col}, 'localtime', '-4 hours'))"

    cards = conn.execute(
        f"""SELECT c.id, c.deck_id, c.category, c.state, c.pre_suspend_state,
                   {day_of.format(col='e.date_added')} AS created
            FROM cards c JOIN entries e ON e.id = c.word_id
            WHERE c.deleted_at IS NULL {deck_filter}""",
        params,
    ).fetchall()

    reviews = conn.execute(
        f"""SELECT rl.card_id, {day_of.format(col='rl.reviewed_at')} AS d,
                   rl.state, rl.rating
            FROM review_log rl
            JOIN cards c ON c.id = rl.card_id
            WHERE c.deleted_at IS NULL {deck_filter}
            ORDER BY rl.card_id, rl.reviewed_at""",
        params,
    ).fetchall()

    # Learning/relearning step counts per deck (category override wins)
    preset_rows = conn.execute(
        """SELECT d.id AS deck_id,
                  p.learning_steps, p.relearning_steps,
                  o.learning_steps AS o_ls, o.relearning_steps AS o_rs
           FROM decks d
           JOIN deck_presets p ON p.id = d.preset_id
           LEFT JOIN preset_category_overrides o
                  ON o.preset_id = d.preset_id AND o.category = d.category"""
    ).fetchall()
    conn.close()

    steps_by_deck = {
        r["deck_id"]: (
            max(1, len((r["o_ls"] or r["learning_steps"]).split())),
            max(1, len((r["o_rs"] or r["relearning_steps"]).split())),
        )
        for r in preset_rows
    }

    revs_by_card: dict[int, list] = {}
    for r in reviews:
        revs_by_card.setdefault(r["card_id"], []).append(
            (r["d"], r["state"], r["rating"]))

    # deltas[category][day][state] — state-count changes taking effect that day
    deltas: dict[str, dict[str, dict[str, int]]] = {}
    for c in cards:
        final = c["state"]
        if final == "suspended":
            final = c["pre_suspend_state"]
        n_learn, n_relearn = steps_by_deck.get(c["deck_id"], (2, 1))
        rl = revs_by_card.get(c["id"], [])

        # Checkpoints: (day, state the card holds from the end of that day on)
        seq = [(c["created"], "new")]
        state, step = "new", 0
        for day, recorded, rating in rl:
            if recorded in _EVOLUTION_STATES and recorded != state:
                state, step = recorded, 0  # recalibrate from recorded truth
            state, step = _next_state(state, step, rating, n_learn, n_relearn)
            seq.append((day, state))
        if rl and final in _EVOLUTION_STATES:
            seq[-1] = (seq[-1][0], final)  # pin the end to the card's real state

        # Several checkpoints on one day: the last one wins
        last_for_day: dict[str, str] = {}
        for day, st in seq:
            last_for_day[day] = st

        cat_deltas = deltas.setdefault(c["category"], {})
        prev = None
        for day in sorted(last_for_day):
            st = last_for_day[day]
            if st == prev:
                continue
            d = cat_deltas.setdefault(day, {})
            if prev is not None:
                d[prev] = d.get(prev, 0) - 1
            d[st] = d.get(st, 0) + 1
            prev = st

    # Accumulate deltas into daily series (deltas before the window roll into day 0)
    series: dict[str, dict[str, list[int]]] = {}
    for cat, cat_deltas in deltas.items():
        running = dict.fromkeys(_EVOLUTION_STATES, 0)
        out = {s: [] for s in _EVOLUTION_STATES}
        delta_days = sorted(cat_deltas)
        idx = 0
        for date_str in dates:
            while idx < len(delta_days) and delta_days[idx] <= date_str:
                for s, n in cat_deltas[delta_days[idx]].items():
                    running[s] += n
                idx += 1
            for s in _EVOLUTION_STATES:
                out[s].append(running[s])
        series[cat] = out

    return {
        "days": days,
        "today": today.isoformat(),
        "dates": dates,
        "series": series,
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
    today = anki_today()
    for i, row in enumerate(rows):
        expected = (today - __import__("datetime").timedelta(days=i)).isoformat()
        if row["d"] == expected:
            streak += 1
        else:
            break
    return streak

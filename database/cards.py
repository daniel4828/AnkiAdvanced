import sqlite3
from datetime import date, datetime
from .core import get_db
from .presets import get_preset_for_deck
from .decks import get_deck


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
                  w.traditional, w.definition_zh, w.note_type, w.notes, w.definition_de,
                  p.learning_steps, p.graduating_interval, p.easy_interval,
                  p.relearning_steps, p.minimum_interval,
                  p.leech_threshold, p.leech_action,
                  p.new_per_day, p.reviews_per_day
           FROM cards c
           JOIN entries w ON w.id = c.word_id
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


def _get_virtually_buried_word_ids(
    word_ids: set[int], category: str, conn, today: str, now: str
) -> set[int]:
    """Return the subset of word_ids suppressed by a higher-priority sibling card due today.

    Each word is "owned" by the due card with the best combined rank:
      state rank  : learning/relearn=0, review=1, new=2
      category rank: listening=0, reading=1, creating=2
    A card is suppressed if a sibling with a strictly lower combined rank exists and is due.
    """
    if not word_ids:
        return set()
    placeholders = ",".join("?" * len(word_ids))
    rows = conn.execute(
        f"""SELECT DISTINCT c_mine.word_id
            FROM cards c_mine
            JOIN cards c_sib ON c_sib.word_id = c_mine.word_id AND c_sib.id != c_mine.id
            WHERE c_mine.word_id IN ({placeholders})
              AND c_mine.category = ?
              AND c_mine.deleted_at IS NULL
              AND c_sib.category != ?
              AND c_sib.deleted_at IS NULL
              AND c_sib.state != 'suspended'
              AND (c_sib.buried_until IS NULL OR c_sib.buried_until < ?)
              AND (
                (c_sib.state IN ('learning', 'relearn') AND c_sib.due <= ?)
                OR (c_sib.state IN ('review', 'new') AND c_sib.due <= ?)
              )
              AND (
                CASE c_sib.state
                  WHEN 'learning' THEN 0 WHEN 'relearn' THEN 0 WHEN 'review' THEN 1 ELSE 2
                END <
                CASE c_mine.state
                  WHEN 'learning' THEN 0 WHEN 'relearn' THEN 0 WHEN 'review' THEN 1 ELSE 2
                END
                OR (
                  CASE c_sib.state
                    WHEN 'learning' THEN 0 WHEN 'relearn' THEN 0 WHEN 'review' THEN 1 ELSE 2
                  END =
                  CASE c_mine.state
                    WHEN 'learning' THEN 0 WHEN 'relearn' THEN 0 WHEN 'review' THEN 1 ELSE 2
                  END
                  AND
                  CASE c_sib.category WHEN 'listening' THEN 0 WHEN 'reading' THEN 1 ELSE 2 END <
                  CASE c_mine.category WHEN 'listening' THEN 0 WHEN 'reading' THEN 1 ELSE 2 END
                )
              )""",
        (*word_ids, category, category, today, now, today),
    ).fetchall()
    return {r["word_id"] for r in rows}


def resolve_bury_flags(preset: dict) -> tuple[bool, bool, bool]:
    """Return (bury_new, bury_review, bury_learning) based on bury_quick_mode."""
    mode = preset.get("bury_quick_mode", "all")
    if mode == "all":
        return True, True, True
    if mode == "none":
        return False, False, False
    # custom: use the individual fields
    return (
        bool(preset.get("bury_new_siblings", 0)),
        bool(preset.get("bury_review_siblings", 0)),
        bool(preset.get("bury_interday_siblings", 0)),
    )


def _interleave_cards(base: list, inserts: list) -> list:
    """Distribute inserts evenly throughout base."""
    if not inserts:
        return base
    if not base:
        return inserts
    result = list(base)
    step = max(1, len(base) // (len(inserts) + 1))
    for i, card in enumerate(inserts):
        pos = min(step * (i + 1) + i, len(result))
        result.insert(pos, card)
    return result


def get_due_cards(deck_id: int, category: str, *, sibling_suppression: bool = False) -> list[dict]:
    """All due cards for a category, ordered per preset display-order settings."""
    import random
    from itertools import groupby

    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()

    preset = get_preset_for_deck(deck_id)
    new_limit = preset["new_per_day"]
    new_done_today = _count_new_introduced_today(conn, deck_id, category, today)
    new_remaining = max(0, new_limit - new_done_today)

    rows = conn.execute(
        """SELECT c.*, w.word_zh, w.pinyin, w.definition, w.pos,
                  w.hsk_level, w.traditional, w.definition_zh,
                  w.note_type, w.source_sentence, w.notes, w.definition_de
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ?
             AND c.category = ?
             AND c.state != 'suspended'
             AND c.deleted_at IS NULL
             AND (c.buried_until IS NULL OR c.buried_until < ?)
             AND (
               (c.state IN ('learning', 'relearn') AND c.due <= ?)
               OR (c.state = 'review' AND c.due <= ?)
               OR (c.state = 'new' AND c.due <= ?)
             )""",
        (deck_id, category, today, now, today, today),
    ).fetchall()

    all_cards = [dict(r) for r in rows]
    learning_cards = [c for c in all_cards if c["state"] in ("learning", "relearn")]
    review_cards   = [c for c in all_cards if c["state"] == "review"]
    new_cards_raw  = [c for c in all_cards if c["state"] == "new"]

    # ── 1. Gather & sort new cards ────────────────────────────────────────────
    gather = preset.get("new_gather_order", "ascending_position")
    if gather == "ascending_position":
        new_cards_raw.sort(key=lambda c: c["id"])
    elif gather == "descending_position":
        new_cards_raw.sort(key=lambda c: c["id"], reverse=True)
    elif gather == "deck":
        new_cards_raw.sort(key=lambda c: (c["deck_id"], c["id"]))
    elif gather == "deck_random_notes":
        by_deck: dict = {}
        for c in new_cards_raw:
            by_deck.setdefault(c["deck_id"], []).append(c)
        gathered = []
        for dk in sorted(by_deck):
            grp = by_deck[dk]
            random.shuffle(grp)
            gathered.extend(grp)
        new_cards_raw = gathered
    elif gather in ("random_notes", "random_cards"):
        random.shuffle(new_cards_raw)

    sort_o = preset.get("new_sort_order", "card_type_gathered")
    if sort_o in ("random", "card_type_random", "random_note_card_type"):
        random.shuffle(new_cards_raw)
    # else: card_type_gathered / gathered → keep gather order

    new_cards = new_cards_raw[:new_remaining]

    # ── 2. Sort review cards ──────────────────────────────────────────────────
    rev_o = preset.get("review_sort_order", "due_random")
    if rev_o == "due_random":
        review_cards.sort(key=lambda c: c["due"])
        shuffled: list = []
        for _, grp in groupby(review_cards, key=lambda c: c["due"]):
            g = list(grp)
            random.shuffle(g)
            shuffled.extend(g)
        review_cards = shuffled
    elif rev_o == "due_deck":
        review_cards.sort(key=lambda c: (c["due"], c["deck_id"]))
    elif rev_o == "deck_due":
        review_cards.sort(key=lambda c: (c["deck_id"], c["due"]))
    elif rev_o == "ascending_intervals":
        review_cards.sort(key=lambda c: c["interval"])
    elif rev_o == "descending_intervals":
        review_cards.sort(key=lambda c: c["interval"], reverse=True)
    elif rev_o == "ascending_ease":
        review_cards.sort(key=lambda c: c["ease"])
    elif rev_o == "descending_ease":
        review_cards.sort(key=lambda c: c["ease"], reverse=True)
    elif rev_o == "relative_overdueness":
        today_d = date.fromisoformat(today)
        def _overdueness(c: dict) -> float:
            if c["interval"] <= 0:
                return 0.0
            try:
                overdue = (today_d - date.fromisoformat(c["due"][:10])).days
                return overdue / c["interval"]
            except Exception:
                return 0.0
        review_cards.sort(key=_overdueness, reverse=True)

    # ── 3. Learning cards always sorted by due time ───────────────────────────
    learning_cards.sort(key=lambda c: c["due"])

    # ── 4. Assemble queue ─────────────────────────────────────────────────────
    il_o = preset.get("interday_learning_review_order", "mixed")
    if il_o == "learning_first":
        lr = learning_cards + review_cards
    elif il_o == "reviews_first":
        lr = review_cards + learning_cards
    else:  # mixed: merge by due time
        lr = sorted(learning_cards + review_cards, key=lambda c: c["due"])

    nr_o = preset.get("new_review_order", "mixed")
    if nr_o == "new_first":
        cards = new_cards + lr
    elif nr_o == "reviews_first":
        cards = lr + new_cards
    else:  # mixed: distribute new cards evenly throughout lr
        cards = _interleave_cards(lr, new_cards)

    # ── 5. Sibling suppression (for story word-list building) ─────────────────
    if sibling_suppression and any(resolve_bury_flags(preset)):
        word_ids = {c["word_id"] for c in cards}
        suppressed = _get_virtually_buried_word_ids(word_ids, category, conn, today, now)
        if suppressed:
            cards = [c for c in cards if c["word_id"] not in suppressed]

    conn.close()
    return cards


def get_next_card(deck_id: int, category: str) -> dict | None:
    """Top-priority card for the review session, ordered by today's story position."""
    cards = get_due_cards(deck_id, category)
    if not cards:
        return None

    # Reorder by story sentence position if a story exists for today
    today = date.today().isoformat()
    # Import here to avoid circular import at module level
    from .stories import get_active_story, get_story_sentences
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

    rows = conn.execute(
        """SELECT c.word_id, c.state FROM cards c
           WHERE c.deck_id = ? AND c.category = ?
             AND c.state != 'suspended'
             AND c.deleted_at IS NULL
             AND (c.buried_until IS NULL OR c.buried_until < ?)
             AND (
               (c.state IN ('learning', 'relearn') AND c.due <= ?)
               OR (c.state = 'review' AND c.due <= ?)
               OR (c.state = 'new' AND c.due <= ?)
             )""",
        (deck_id, category, today, now, today, today),
    ).fetchall()

    learning = sum(1 for r in rows if r["state"] in ("learning", "relearn"))
    review   = sum(1 for r in rows if r["state"] == "review")
    new_avail = sum(1 for r in rows if r["state"] == "new")

    conn.close()
    return {
        "new": min(new_avail, new_remaining),
        "learning": learning,
        "review": review,
    }


def update_word(word_id: int, fields: dict) -> None:
    allowed = {"word_zh", "pinyin", "definition", "pos", "traditional", "definition_zh", "notes", "hsk_level", "definition_de"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE entries SET {sets} WHERE id=?", (*updates.values(), word_id))
    conn.commit()
    conn.close()


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


def bury_card(card_id: int) -> None:
    """Bury a card until tomorrow (hidden for the rest of today)."""
    today = date.today().isoformat()
    conn = get_db()
    conn.execute("UPDATE cards SET buried_until = ? WHERE id = ?", (today, card_id))
    conn.commit()
    conn.close()


def unbury_card(card_id: int) -> None:
    """Remove burial — card becomes available immediately."""
    conn = get_db()
    conn.execute("UPDATE cards SET buried_until = NULL WHERE id = ?", (card_id,))
    conn.commit()
    conn.close()


def set_card_buried_until(card_id: int, buried_until: str | None) -> None:
    """Restore buried_until to an exact value (used by undo)."""
    conn = get_db()
    conn.execute("UPDATE cards SET buried_until = ? WHERE id = ?", (buried_until, card_id))
    conn.commit()
    conn.close()


def get_descendant_leaf_deck_ids(deck_id: int, category: str | None = None) -> list[int]:
    """Return all category-leaf deck IDs under deck_id (depth-first). Optionally filter by category."""
    conn = get_db()
    rows = conn.execute("SELECT id, parent_id, category FROM decks WHERE deleted_at IS NULL").fetchall()
    conn.close()

    children_map: dict = {}
    deck_cat: dict = {}
    for row in rows:
        deck_cat[row["id"]] = row["category"]
        pid = row["parent_id"]
        children_map.setdefault(pid, []).append(row["id"])

    result = []
    stack = [deck_id]
    while stack:
        current = stack.pop()
        cat = deck_cat.get(current)
        kids = children_map.get(current, [])
        if cat is not None:  # category leaf
            if category is None or cat == category:
                result.append(current)
        for kid in kids:
            stack.append(kid)
    return result


def _leaf_decks_with_category(root_deck_id: int) -> list[tuple[int, str]]:
    """Return [(deck_id, category)] for all category leaves under root_deck_id."""
    all_leaf_ids = get_descendant_leaf_deck_ids(root_deck_id)
    if not all_leaf_ids:
        deck = get_deck(root_deck_id)
        if deck and deck["category"]:
            return [(root_deck_id, deck["category"])]
        return []
    conn = get_db()
    placeholders = ','.join('?' * len(all_leaf_ids))
    rows = conn.execute(
        f"SELECT id, category FROM decks WHERE id IN ({placeholders})", all_leaf_ids
    ).fetchall()
    conn.close()
    return [(r["id"], r["category"]) for r in rows if r["category"]]


def get_next_card_any_cat(root_deck_id: int) -> dict | None:
    """Highest-priority card across all categories under root_deck_id."""
    leaf_pairs = _leaf_decks_with_category(root_deck_id)
    all_cards = []
    for deck_id, cat in leaf_pairs:
        all_cards.extend(get_due_cards(deck_id, cat))
    if not all_cards:
        return None

    # Reorder by story sentence position (same as get_next_card for single-cat)
    today = date.today().isoformat()
    from .stories import get_active_story, get_story_sentences
    story_pos: dict = {}
    for deck_id, cat in leaf_pairs:
        story = get_active_story(today, cat, deck_id)
        if story:
            for s in get_story_sentences(story["id"]):
                story_pos[s["word_id"]] = s["position"]

    NO_POS = 9999
    all_cards.sort(key=lambda c: (
        0 if c["state"] in ("learning", "relearn") else
        1 if c["state"] == "review" else 2,
        story_pos.get(c["word_id"], NO_POS),
        c["due"],
    ))
    return all_cards[0]


def count_due_any_cat(root_deck_id: int) -> dict:
    """Total due counts across all categories under root_deck_id."""
    leaf_pairs = _leaf_decks_with_category(root_deck_id)
    total = {"new": 0, "learning": 0, "review": 0}
    for deck_id, cat in leaf_pairs:
        c = count_due(deck_id, cat)
        for k in total:
            total[k] += c[k]
    return total


def get_due_cards_multi(deck_ids: list[int], category: str, *, sibling_suppression: bool = False) -> list[dict]:
    """Due cards across multiple decks, merged and priority-sorted."""
    all_cards = []
    for deck_id in deck_ids:
        all_cards.extend(get_due_cards(deck_id, category, sibling_suppression=sibling_suppression))
    all_cards.sort(key=lambda c: (
        0 if c["state"] in ("learning", "relearn") else
        1 if c["state"] == "review" else 2,
        c["due"]
    ))
    return all_cards


def get_next_card_multi(deck_ids: list[int], category: str) -> dict | None:
    """Highest-priority card across multiple decks."""
    cards = get_due_cards_multi(deck_ids, category)
    return cards[0] if cards else None


def count_due_multi(deck_ids: list[int], category: str) -> dict:
    """Aggregate due counts across multiple decks."""
    total = {"new": 0, "learning": 0, "review": 0}
    for deck_id in deck_ids:
        c = count_due(deck_id, category)
        for k in total:
            total[k] += c[k]
    return total


def count_due_deduped(leaf_pairs: list[tuple[int, str]]) -> dict:
    """Count unique due words across multiple category leaf decks for parent badge display.

    Each word is counted once, in the category of its highest-priority due card:
      state rank  : learning/relearn=0, review=1, new=2  (lower = better)
      category rank: listening=0, reading=1, creating=2  (lower = better)

    Respects the bury_siblings setting. Falls back to a simple sum if disabled.
    """
    if not leaf_pairs:
        return {"new": 0, "learning": 0, "review": 0}

    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()

    preset = get_preset_for_deck(leaf_pairs[0][0])
    if not preset.get("bury_siblings", 1):
        conn.close()
        total = {"new": 0, "learning": 0, "review": 0}
        for deck_id, cat in leaf_pairs:
            for k, v in count_due(deck_id, cat).items():
                total[k] += v
        return total

    cat_rank_map = {"listening": 0, "reading": 1, "creating": 2}
    # word_id -> (state_rank, cat_rank, state, deck_id, category)
    best: dict[int, tuple] = {}
    new_remaining_map: dict[tuple, int] = {}

    for deck_id, category in leaf_pairs:
        pr = get_preset_for_deck(deck_id)
        new_done = _count_new_introduced_today(conn, deck_id, category, today)
        new_remaining_map[(deck_id, category)] = max(0, pr["new_per_day"] - new_done)

        rows = conn.execute(
            """SELECT c.word_id, c.state FROM cards c
               WHERE c.deck_id = ? AND c.category = ?
                 AND c.state != 'suspended'
                 AND c.deleted_at IS NULL
                 AND (c.buried_until IS NULL OR c.buried_until < ?)
                 AND (
                   (c.state IN ('learning', 'relearn') AND c.due <= ?)
                   OR (c.state = 'review' AND c.due <= ?)
                   OR (c.state = 'new' AND c.due <= ?)
                 )""",
            (deck_id, category, today, now, today, today),
        ).fetchall()

        for r in rows:
            sr = 0 if r["state"] in ("learning", "relearn") else 1 if r["state"] == "review" else 2
            cr = cat_rank_map[category]
            if r["word_id"] not in best or (sr, cr) < best[r["word_id"]][:2]:
                best[r["word_id"]] = (sr, cr, r["state"], deck_id, category)

    conn.close()

    learning_count = 0
    review_count = 0
    new_by_deck: dict[tuple, int] = {}

    for sr, cr, state, deck_id, category in best.values():
        if sr == 0:
            learning_count += 1
        elif sr == 1:
            review_count += 1
        else:
            key = (deck_id, category)
            new_by_deck[key] = new_by_deck.get(key, 0) + 1

    new_count = sum(
        min(count, new_remaining_map.get(key, 0))
        for key, count in new_by_deck.items()
    )
    return {"new": new_count, "learning": learning_count, "review": review_count}


def count_unfinished() -> dict:
    """Count learning/relearn cards due right now across all decks and categories."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    learning = conn.execute(
        """SELECT COUNT(*) FROM cards
           WHERE state IN ('learning', 'relearn') AND due <= ?
             AND (buried_until IS NULL OR buried_until < date('now'))""",
        (now,),
    ).fetchone()[0]
    conn.close()
    return {"new": 0, "learning": learning, "review": 0}


def get_unfinished_deck_categories() -> list[dict]:
    """Return distinct (deck_id, category) pairs that have unfinished cards due now."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    rows = conn.execute(
        """SELECT DISTINCT deck_id, category FROM cards
           WHERE state IN ('learning', 'relearn') AND due <= ?
             AND (buried_until IS NULL OR buried_until < date('now'))""",
        (now,),
    ).fetchall()
    conn.close()
    return [{"deck_id": r["deck_id"], "category": r["category"]} for r in rows]


def get_next_unfinished_card() -> dict | None:
    """Highest-priority learning/relearn card due right now across all decks/categories."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    row = conn.execute(
        """SELECT id FROM cards
           WHERE state IN ('learning', 'relearn') AND due <= ?
             AND (buried_until IS NULL OR buried_until < date('now'))
           ORDER BY due ASC LIMIT 1""",
        (now,),
    ).fetchone()
    conn.close()
    return get_card(row["id"]) if row else None


def bury_siblings(word_id: int, reviewed_category: str, *,
                  bury_new: bool = False, bury_review: bool = False,
                  bury_learning: bool = False) -> None:
    """Bury other-category cards for this word based on which states should be buried."""
    states = []
    if bury_new:
        states.append("'new'")
    if bury_review:
        states.append("'review'")
    if bury_learning:
        states.extend(["'learning'", "'relearn'"])
    if not states:
        return
    today = date.today().isoformat()
    conn = get_db()
    conn.execute(
        f"UPDATE cards SET buried_until = ? WHERE word_id = ? AND category != ?"
        f" AND state IN ({','.join(states)})",
        (today, word_id, reviewed_category),
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


def get_creating_all_suspended(deck_id: int) -> bool:
    """Return True if all non-sentence creating cards in the deck are suspended (and at least one exists)."""
    conn = get_db()
    row = conn.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN c.state = 'suspended' THEN 1 ELSE 0 END) as suspended_count
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ? AND c.category = 'creating'
             AND c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id,),
    ).fetchone()
    conn.close()
    total = row["total"] or 0
    suspended = row["suspended_count"] or 0
    return total > 0 and total == suspended


def toggle_deck_creating_suspension(deck_id: int) -> dict:
    """Toggle all creating cards in a deck between suspended and new.

    Sentence notes (words with note_type='sentence') are excluded.
    Logic: if any cards are state='new', suspend all new ones;
           otherwise unsuspend all suspended ones.
    Returns {"all_suspended": bool, "count": int}.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT c.id, c.state FROM cards c
           JOIN entries w ON w.id = c.word_id
           WHERE c.deck_id = ? AND c.category = 'creating'
             AND c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id,),
    ).fetchall()

    has_active = any(r["state"] != "suspended" for r in rows)
    if has_active:
        conn.execute(
            """UPDATE cards SET state='suspended'
               WHERE deck_id = ? AND category = 'creating'
                 AND deleted_at IS NULL AND state = 'new'
                 AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            (deck_id,),
        )
        all_suspended = True
    else:
        conn.execute(
            """UPDATE cards SET state='new'
               WHERE deck_id = ? AND category = 'creating'
                 AND deleted_at IS NULL AND state = 'suspended'
                 AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            (deck_id,),
        )
        all_suspended = False

    conn.commit()
    conn.close()
    return {"all_suspended": all_suspended, "count": len(rows)}


def get_category_all_suspended(deck_id: int, category: str) -> bool:
    """Return True if all non-sentence cards of given category in deck and all descendants are suspended."""
    conn = get_db()
    row = conn.execute(
        """WITH RECURSIVE descendants AS (
             SELECT id FROM decks WHERE id = ?
             UNION ALL
             SELECT d.id FROM decks d JOIN descendants p ON d.parent_id = p.id
           )
           SELECT COUNT(*) as total,
                  SUM(CASE WHEN c.state = 'suspended' THEN 1 ELSE 0 END) as suspended_count
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           JOIN descendants des ON c.deck_id = des.id
           WHERE c.category = ?
             AND c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id, category),
    ).fetchone()
    conn.close()
    total = row["total"] or 0
    suspended = row["suspended_count"] or 0
    return total > 0 and total == suspended


def toggle_category_suspension(deck_id: int, category: str) -> dict:
    """Toggle all non-sentence cards of given category in a deck and all descendants."""
    conn = get_db()
    deck_rows = conn.execute(
        """WITH RECURSIVE descendants AS (
             SELECT id FROM decks WHERE id = ?
             UNION ALL
             SELECT d.id FROM decks d JOIN descendants p ON d.parent_id = p.id
           )
           SELECT id FROM descendants""",
        (deck_id,),
    ).fetchall()
    deck_ids = [r["id"] for r in deck_rows]
    placeholders = ",".join("?" * len(deck_ids))
    active_row = conn.execute(
        f"""SELECT COUNT(*) as cnt FROM cards c
            JOIN entries w ON w.id = c.word_id
            WHERE c.deck_id IN ({placeholders})
              AND c.category = ?
              AND c.state != 'suspended'
              AND c.deleted_at IS NULL
              AND w.note_type != 'sentence'""",
        deck_ids + [category],
    ).fetchone()
    has_active = active_row["cnt"] > 0
    if has_active:
        conn.execute(
            f"""UPDATE cards SET state='suspended'
                WHERE deck_id IN ({placeholders})
                  AND category = ?
                  AND state != 'suspended'
                  AND deleted_at IS NULL
                  AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            deck_ids + [category],
        )
    else:
        conn.execute(
            f"""UPDATE cards SET state='new'
                WHERE deck_id IN ({placeholders})
                  AND category = ?
                  AND state = 'suspended'
                  AND deleted_at IS NULL
                  AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            deck_ids + [category],
        )
    conn.commit()
    conn.close()
    return {"all_suspended": not has_active}


def get_deck_all_suspended(deck_id: int) -> bool:
    """Return True if ALL non-sentence cards in deck and all descendant decks are suspended."""
    conn = get_db()
    row = conn.execute(
        """WITH RECURSIVE descendants AS (
             SELECT id FROM decks WHERE id = ?
             UNION ALL
             SELECT d.id FROM decks d JOIN descendants p ON d.parent_id = p.id
           )
           SELECT COUNT(*) as total,
                  SUM(CASE WHEN c.state = 'suspended' THEN 1 ELSE 0 END) as suspended_count
           FROM cards c
           JOIN entries w ON w.id = c.word_id
           JOIN descendants des ON c.deck_id = des.id
           WHERE c.deleted_at IS NULL
             AND w.note_type != 'sentence'""",
        (deck_id,),
    ).fetchone()
    conn.close()
    total = row["total"] or 0
    suspended = row["suspended_count"] or 0
    return total > 0 and total == suspended


def toggle_deck_all_suspension(deck_id: int) -> dict:
    """Toggle ALL non-sentence cards in deck and all descendant decks."""
    conn = get_db()
    deck_rows = conn.execute(
        """WITH RECURSIVE descendants AS (
             SELECT id FROM decks WHERE id = ?
             UNION ALL
             SELECT d.id FROM decks d JOIN descendants p ON d.parent_id = p.id
           )
           SELECT id FROM descendants""",
        (deck_id,),
    ).fetchall()
    deck_ids = [r["id"] for r in deck_rows]
    placeholders = ",".join("?" * len(deck_ids))
    active_row = conn.execute(
        f"""SELECT COUNT(*) as cnt FROM cards c
            JOIN entries w ON w.id = c.word_id
            WHERE c.deck_id IN ({placeholders})
              AND c.state != 'suspended'
              AND c.deleted_at IS NULL
              AND w.note_type != 'sentence'""",
        deck_ids,
    ).fetchone()
    has_active = active_row["cnt"] > 0
    if has_active:
        conn.execute(
            f"""UPDATE cards SET state='suspended'
                WHERE deck_id IN ({placeholders})
                  AND state != 'suspended'
                  AND deleted_at IS NULL
                  AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            deck_ids,
        )
    else:
        conn.execute(
            f"""UPDATE cards SET state='new'
                WHERE deck_id IN ({placeholders})
                  AND state = 'suspended'
                  AND deleted_at IS NULL
                  AND word_id IN (SELECT id FROM entries WHERE note_type != 'sentence')""",
            deck_ids,
        )
    conn.commit()
    conn.close()
    return {"all_suspended": not has_active}

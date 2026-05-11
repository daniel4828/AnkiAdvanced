import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException

import database
import srs
from .utils import leaf_ids, queue_mgr as _queue_mgr

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory undo stack: list of {card_before, log_id, queue_key, ...}
_undo_stack: list[dict] = []


# ---------------------------------------------------------------------------
# Queue key / build-function helpers
# ---------------------------------------------------------------------------

def _key_and_build(
    *,
    ids: list[int] | None = None,
    category: str | None = None,
    root_deck_id: int | None = None,
    deck_id: int | None = None,
    parent_for_multi: int | None = None,
):
    """Return (queue_key, build_fn) for the given review context."""
    if root_deck_id:
        key = ("any_cat", root_deck_id)
        build_fn = lambda: database.get_due_cards_any_cat(root_deck_id)
    elif ids and len(ids) > 1:
        key = ("multi", tuple(sorted(ids)), category)
        _root = parent_for_multi
        build_fn = lambda: database.get_due_cards_multi(ids, category, root_deck_id=_root)
    else:
        actual = (ids[0] if ids else deck_id)
        key = ("single", actual, category)
        build_fn = lambda: database.get_due_cards(actual, category)
    return key, build_fn


def _next_card_from_queue(key, build_fn) -> dict | None:
    """Ask the queue manager for the next card ID, then fetch the full card."""
    today = database.anki_today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    card_id = _queue_mgr.get_next(key, build_fn, today, now)
    if card_id is None:
        return None
    card = database.get_card(card_id)
    if card:
        card["intervals"] = srs.preview_intervals(card)
    return card


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/today/{deck_id}/{category}")
def get_today(deck_id: int, category: str):
    ids = leaf_ids(deck_id, category)
    key, build_fn = _key_and_build(ids=ids, category=category, parent_for_multi=deck_id)

    card = _next_card_from_queue(key, build_fn)

    if len(ids) == 1:
        counts = database.count_due(ids[0], category)
    else:
        counts = database.count_due_multi(ids, category, root_deck_id=deck_id)

    parent_id = database.get_parent_deck_id(deck_id)
    counts["by_cat"] = database.count_due_by_category(parent_id or deck_id)
    return {"card": card, "counts": counts}


@router.get("/api/today-unfinished")
def get_today_unfinished():
    card = database.get_next_unfinished_card()
    if card:
        card["intervals"] = srs.preview_intervals(card)
    return {"card": card, "counts": database.count_unfinished()}


@router.get("/api/today-unfinished-decks")
def get_today_unfinished_decks():
    return database.get_unfinished_deck_categories()


@router.get("/api/today-mixed/{deck_id}")
def get_today_mixed(deck_id: int):
    key, build_fn = _key_and_build(root_deck_id=deck_id)
    card = _next_card_from_queue(key, build_fn)
    counts = database.count_due_any_cat(deck_id)
    counts["by_cat"] = database.count_due_by_category(deck_id)
    return {"card": card, "counts": counts}


@router.post("/api/review")
def submit_review(card_id: int, rating: int, user_response: str | None = None,
                  root_deck_id: int | None = None, unfinished_mode: bool = False,
                  parent_deck_id: int | None = None):
    card_before = database.get_card(card_id)
    updated, log_id = srs.apply_review(card_id, rating, user_response=user_response)
    deck_id = updated["deck_id"]
    cat     = updated["category"]

    # Apply sibling repulsion: push sibling due dates that are too close to today.
    # Only kicks in when the reviewed card has a long enough interval (> sibling_separation),
    # leaving the initial staggered-introduction phase unaffected.
    preset = database.get_preset_for_deck(deck_id)
    sibling_sep    = preset.get("sibling_separation", 3)
    sibling_factor = preset.get("sibling_factor", 0.2)
    new_interval   = updated.get("interval", 0)
    logger.debug(
        "[sibling_repulsion] triggering for card=#%d  new_interval=%d  sep=%d  factor=%.2f",
        card_id, new_interval, sibling_sep, sibling_factor,
    )
    database.apply_sibling_repulsion(card_id, new_interval, sibling_sep, sibling_factor)

    # Snapshot sibling buried_until values BEFORE burying so undo can restore them
    siblings_before = database.get_sibling_cards(card_id)
    siblings_snapshot = [
        {"id": s["id"], "buried_until": s["buried_until"]}
        for s in siblings_before
    ]

    bury_new, bury_review, bury_learning = database.resolve_bury_flags(preset)
    logger.debug(
        "[preset] deck=%d  bury_quick_mode=%s → new=%s review=%s learning=%s\n"
        "  new_per_day=%d  reviews_per_day=%d  learning_steps=%r\n"
        "  graduating_interval=%d  easy_interval=%d  relearning_steps=%r\n"
        "  new_review_order=%s  new_gather_order=%s  new_sort_order=%s\n"
        "  interday_learning_review_order=%s  review_sort_order=%s\n"
        "  leech_threshold=%d  leech_action=%s  category_order=%s",
        deck_id,
        preset.get("bury_quick_mode"), bury_new, bury_review, bury_learning,
        preset.get("new_per_day", 0), preset.get("reviews_per_day", 0), preset.get("learning_steps", ""),
        preset.get("graduating_interval", 0), preset.get("easy_interval", 0), preset.get("relearning_steps", ""),
        preset.get("new_review_order"), preset.get("new_gather_order"), preset.get("new_sort_order"),
        preset.get("interday_learning_review_order"), preset.get("review_sort_order"),
        preset.get("leech_threshold", 0), preset.get("leech_action"), preset.get("category_order"),
    )
    database.bury_siblings(
        updated["word_id"], cat,
        bury_new=bury_new,
        bury_review=bury_review,
        bury_learning=bury_learning,
    )

    # IDs that bury_siblings() just newly buried — needed to purge them from
    # the in-memory queue so the queue stays consistent with the DB.
    today_str = database.anki_today().isoformat()
    was_buried = {s["id"] for s in siblings_before
                  if s.get("buried_until") is not None and s.get("buried_until") >= today_str}
    siblings_after = database.get_sibling_cards(card_id)
    newly_buried = [
        s["id"] for s in siblings_after
        if s.get("buried_until") == today_str and s["id"] not in was_buried
    ]
    logger.debug(
        "[review] submit card=#%d word=%s cat=%s state=%s→%s\n"
        "  bury_flags: new=%s review=%s learning=%s\n"
        "  siblings_before: %s\n"
        "  siblings_after:  %s\n"
        "  newly_buried:    %s",
        card_id, card_before.get("word_zh"), cat,
        card_before.get("state"), updated.get("state"),
        bury_new, bury_review, bury_learning,
        [(s["id"], s.get("category"), s.get("buried_until")) for s in siblings_before],
        [(s["id"], s.get("category"), s.get("buried_until")) for s in siblings_after],
        newly_buried,
    )

    # Determine queue key for this review context
    if unfinished_mode:
        queue_key = None
    elif root_deck_id:
        queue_key, build_fn = _key_and_build(root_deck_id=root_deck_id)
    elif parent_deck_id:
        ids = leaf_ids(parent_deck_id, cat)
        queue_key, build_fn = _key_and_build(ids=ids, category=cat, parent_for_multi=parent_deck_id)
    else:
        queue_key, build_fn = _key_and_build(deck_id=deck_id, category=cat)

    _undo_stack.append({
        "card_before":        card_before,
        "log_id":             log_id,
        "queue_key":          queue_key,
        "root_deck_id":       root_deck_id,
        "parent_deck_id":     parent_deck_id,
        "unfinished_mode":    unfinished_mode,
        "deck_id":            deck_id,
        "category":           cat,
        "siblings_snapshot":  siblings_snapshot,
    })

    if unfinished_mode:
        next_card = database.get_next_unfinished_card()
        if next_card:
            next_card["intervals"] = srs.preview_intervals(next_card)
        counts = database.count_unfinished()
    elif root_deck_id:
        _queue_mgr.after_review(queue_key, card_id, updated, newly_buried)
        next_card = _next_card_from_queue(queue_key, build_fn)
        counts = database.count_due_any_cat(root_deck_id)
        counts["by_cat"] = database.count_due_by_category(root_deck_id)
    elif parent_deck_id:
        _queue_mgr.after_review(queue_key, card_id, updated, newly_buried)
        next_card = _next_card_from_queue(queue_key, build_fn)
        counts = database.count_due_multi(ids, cat, root_deck_id=parent_deck_id)
        counts["by_cat"] = database.count_due_by_category(parent_deck_id)
    else:
        _queue_mgr.after_review(queue_key, card_id, updated, newly_buried)
        next_card = _next_card_from_queue(queue_key, build_fn)
        counts = database.count_due(deck_id, cat)
        parent_id = database.get_parent_deck_id(deck_id)
        counts["by_cat"] = database.count_due_by_category(parent_id or deck_id)

    rating_label = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}.get(rating, str(rating))
    ivl = updated["interval"]
    ivl_str = f"{ivl}d" if ivl >= 1 else f"{round(ivl * 1440)}m"
    pinyin = card_before.get("pinyin", "")
    pinyin_part = f" ({pinyin})" if pinyin else ""
    logger.info(
        "Card #%d %s%s  %s → %s  %s  due=%s  ivl=%s  ease=%.2f  lapses=%d",
        card_before["id"], card_before["word_zh"], pinyin_part,
        card_before["state"], updated["state"],
        rating_label, updated["due"], ivl_str,
        updated["ease"], updated["lapses"],
    )
    cat_totals = {
        c: sum(database.count_due(deck_id, c).values())
        for c in ("listening", "reading", "creating")
    }
    logger.info(
        "Queue: %d lrn  %d rev  %d new  │ 听=%d  读=%d  创=%d",
        counts["learning"], counts["review"], counts["new"],
        cat_totals["listening"], cat_totals["reading"], cat_totals["creating"],
    )
    if updated.get("state") == "suspended":
        logger.warning(
            "Card #%d %s SUSPENDED (lapses=%d)",
            card_before["id"], card_before["word_zh"], updated["lapses"],
        )
    return {"next_card": next_card, "counts": counts}



@router.post("/api/review/undo")
def undo_review():
    if not _undo_stack:
        raise HTTPException(status_code=404, detail="Nothing to undo")

    entry = _undo_stack.pop()

    cb = entry["card_before"]

    # Restore the card to its pre-review state
    database.update_card(
        cb["id"],
        state=cb["state"],
        due=cb["due"],
        step_index=cb["step_index"],
        interval=cb["interval"],
        ease=cb["ease"],
        repetitions=cb["repetitions"],
        lapses=cb["lapses"],
    )
    database.delete_review_log(entry["log_id"])

    # Restore siblings' buried_until to their pre-review values
    for sib in entry.get("siblings_snapshot", []):
        database.set_card_buried_until(sib["id"], sib["buried_until"])

    # Invalidate the queue so the restored card is picked up on next access
    _queue_mgr.invalidate(entry.get("queue_key"))

    # Return the restored card so the frontend can show it
    restored = database.get_card(cb["id"])
    restored["intervals"] = srs.preview_intervals(restored)

    deck_id         = entry["deck_id"]
    cat             = entry["category"]
    unfinished_mode = entry["unfinished_mode"]
    root_deck_id    = entry["root_deck_id"]
    parent_deck_id  = entry.get("parent_deck_id")

    if unfinished_mode:
        counts = database.count_unfinished()
    elif root_deck_id:
        counts = database.count_due_any_cat(root_deck_id)
    elif parent_deck_id:
        ids    = leaf_ids(parent_deck_id, cat)
        counts = database.count_due_multi(ids, cat)
    else:
        counts = database.count_due(deck_id, cat)

    logger.info("undo review for %s, restored state=%s (stack_size=%d)",
                restored["word_zh"], restored["state"], len(_undo_stack))
    return {"card": restored, "counts": counts, "stack_size": len(_undo_stack)}



@router.post("/api/cards/{card_id}/bury")
def bury_card(card_id: int):
    database.bury_card(card_id)
    return {"ok": True}


@router.post("/api/cards/{card_id}/unbury")
def unbury_card(card_id: int):
    database.unbury_card(card_id)
    return {"ok": True}


@router.get("/api/cards/{card_id}/calendar")
def get_card_calendar(card_id: int):
    return database.get_card_calendar_data(card_id)

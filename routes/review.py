import logging
from fastapi import APIRouter, HTTPException

import database
import srs
from .utils import leaf_ids

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory undo stack: list of {card_before, log_id, root_deck_id, unfinished_mode, deck_id, category}
_undo_stack: list[dict] = []


@router.get("/api/today/{deck_id}/{category}")
def get_today(deck_id: int, category: str):
    ids = leaf_ids(deck_id, category)
    if len(ids) == 1:
        card  = database.get_next_card(ids[0], category)
        counts = database.count_due(ids[0], category)
    else:
        card  = database.get_next_card_multi(ids, category)
        counts = database.count_due_multi(ids, category)
    if card:
        card = database.get_card(card["id"])
        card["intervals"] = srs.preview_intervals(card)
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
    card = database.get_next_card_any_cat(deck_id)
    if card:
        card = database.get_card(card["id"])
        card["intervals"] = srs.preview_intervals(card)
    counts = database.count_due_any_cat(deck_id)
    return {"card": card, "counts": counts}


@router.post("/api/review")
def submit_review(card_id: int, rating: int, user_response: str | None = None,
                  root_deck_id: int | None = None, unfinished_mode: bool = False,
                  parent_deck_id: int | None = None):
    card_before = database.get_card(card_id)
    updated, log_id = srs.apply_review(card_id, rating, user_response=user_response)
    deck_id = updated["deck_id"]
    cat     = updated["category"]

    _undo_stack.append({
        "card_before":     card_before,
        "log_id":          log_id,
        "root_deck_id":    root_deck_id,
        "parent_deck_id":  parent_deck_id,
        "unfinished_mode": unfinished_mode,
        "deck_id":         deck_id,
        "category":        cat,
    })

    preset = database.get_preset_for_deck(deck_id)
    bury_new, bury_review, bury_learning = database.resolve_bury_flags(preset)
    database.bury_siblings(
        updated["word_id"], cat,
        bury_new=bury_new,
        bury_review=bury_review,
        bury_learning=bury_learning,
    )

    if unfinished_mode:
        next_card = database.get_next_unfinished_card()
        counts    = database.count_unfinished()
    elif root_deck_id:
        next_card = database.get_next_card_any_cat(root_deck_id)
        counts    = database.count_due_any_cat(root_deck_id)
    elif parent_deck_id:
        ids       = leaf_ids(parent_deck_id, cat)
        next_card = database.get_next_card_multi(ids, cat)
        counts    = database.count_due_multi(ids, cat)
    else:
        next_card = database.get_next_card(deck_id, cat)
        counts    = database.count_due(deck_id, cat)

    if next_card:
        next_card = database.get_card(next_card["id"])
        next_card["intervals"] = srs.preview_intervals(next_card)

    rating_label = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}.get(rating, rating)
    logger.info("review %s → %s (%s)  due=%s  next=%s  queue: %d lrn %d rev %d new",
                card_before["word_zh"], updated["state"], rating_label,
                updated["due"], next_card["word_zh"] if next_card else "—",
                counts["learning"], counts["review"], counts["new"])
    return {"next_card": next_card, "counts": counts}


@router.post("/api/review/undo")
def undo_review():
    if not _undo_stack:
        raise HTTPException(status_code=404, detail="Nothing to undo")

    entry = _undo_stack.pop()
    cb    = entry["card_before"]

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

    logger.info("undo review for %s, restored state=%s", restored["word_zh"], restored["state"])
    return {"card": restored, "counts": counts}


@router.post("/api/cards/{card_id}/bury")
def bury_card(card_id: int):
    database.bury_card(card_id)
    return {"ok": True}


@router.post("/api/cards/{card_id}/unbury")
def unbury_card(card_id: int):
    database.unbury_card(card_id)
    return {"ok": True}

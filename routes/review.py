import logging
import threading
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException

import database
import srs
import tts
from .utils import leaf_ids, queue_mgr as _queue_mgr, DISABLE_AI

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory undo stack: list of {card_before, log_id, queue_key, ...}
_undo_stack: list[dict] = []


# ---------------------------------------------------------------------------
# "Again" → background single-sentence regeneration
# ---------------------------------------------------------------------------

def _attach_again_sentence(card: dict | None) -> dict | None:
    """If a fresh sentence was regenerated for this word today (from a previous
    Again, or the "new sentence" requeue button), attach it as
    card["again_sentence"] so the frontend shows it instead of the old story one.

    Not gated on card state: the requeue button leaves scheduling untouched, so
    the card can be in any state when it reappears with its new sentence."""
    if (card and card.get("note_type") != "sentence" and card.get("word_id")):
        today = database.anki_today().isoformat()
        again = database.get_again_sentence_for_word(card["word_id"], today)
        if again:
            card["again_sentence"] = again
            logger.debug("again-regen  HIT word=%s — showing regenerated sentence",
                         card.get("word_zh"))
    return card


def _spawn_again_regen(card: dict) -> None:
    """Fire-and-forget: regenerate one fresh sentence for this word in the
    background so the card shows something new when it reappears (~1-10 min)."""
    if DISABLE_AI or card.get("note_type") == "sentence" or not card.get("word_id"):
        return
    # All three vocab categories use story sentences (listening audio, reading text,
    # creating cloze/word-bank), so regenerate a fresh sentence for any of them.
    if card.get("category") not in ("listening", "reading", "creating"):
        return

    logger.info("again-regen  TRIGGER word=%s cat=%s — scheduling background regen",
                card.get("word_zh"), card.get("category"))

    def _run() -> None:
        try:
            from .story import generate_sentence_for_word
            today = database.anki_today().isoformat()
            # Reuse the deck story's generation settings (mode/topic/grammar/model;
            # a random chapter for kahneman) so the new sentence matches its style
            # instead of always being a plain story sentence.
            gen_params = database.get_story_gen_params_for_word(card["word_id"], today)
            sentence = generate_sentence_for_word(card, gen_params)
            if not sentence:
                return
            database.store_again_sentence(card["deck_id"], card["word_id"], sentence, today)
            logger.info("again-regen  word=%s mode=%s → new sentence stored",
                        card.get("word_zh"), (gen_params or {}).get("mode", "story"))
            try:
                tts.preload(sentence.get("sentence_zh", ""))
            except Exception:
                pass
        except Exception as e:
            logger.warning("again-regen failed for word=%s: %s", card.get("word_zh"), e)

    threading.Thread(target=_run, daemon=True).start()


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
        card["fsrs"] = srs.explain_card(card)
        _attach_again_sentence(card)
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
def get_today_unfinished(scope: str = "unfinished"):
    card = database.get_next_unfinished_card(scope)
    if card:
        card["intervals"] = srs.preview_intervals(card)
        card["fsrs"] = srs.explain_card(card)
        _attach_again_sentence(card)
    return {"card": card, "counts": database.count_unfinished(scope)}


@router.get("/api/today-unfinished-decks")
def get_today_unfinished_decks(scope: str = "unfinished"):
    return database.get_unfinished_deck_categories(scope)


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
                  parent_deck_id: int | None = None, duration_ms: int | None = None,
                  next_note: str | None = None, unfinished_scope: str = "unfinished"):
    card_before = database.get_card(card_id)
    # Persist the free-text "note for next time" the user left on this card
    # (None means "leave the existing note untouched"; "" clears it).
    if next_note is not None:
        database.set_card_note(card_id, next_note)
    updated, log_id = srs.apply_review(card_id, rating, user_response=user_response,
                                       duration_ms=duration_ms)
    deck_id = updated["deck_id"]
    cat     = updated["category"]

    # Rated Again → regenerate a fresh sentence for this word in the background,
    # so it shows something new when the card reappears in a few minutes.
    if rating == 1:
        _spawn_again_regen(card_before)

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
        "unfinished_scope":   unfinished_scope,
        "deck_id":            deck_id,
        "category":           cat,
        "siblings_snapshot":  siblings_snapshot,
    })

    if unfinished_mode:
        next_card = database.get_next_unfinished_card(unfinished_scope)
        if next_card:
            next_card["intervals"] = srs.preview_intervals(next_card)
            next_card["fsrs"] = srs.explain_card(next_card)
            _attach_again_sentence(next_card)
        counts = database.count_unfinished(unfinished_scope)
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
    state_from = card_before["state"]
    state_to   = updated["state"]
    # A card only counts as "learned" once its interval reaches learned_interval;
    # graduating to 'review' with a shorter interval is still learning.
    learned_threshold = updated.get("learned_interval", 4)
    is_learned = state_to == "review" and (updated.get("interval") or 0) >= learned_threshold
    transition = {
        "from":    state_from,
        "to":      state_to,
        "changed": state_from != state_to,
        "leech":   bool(updated.get("is_leech")) and state_to == "suspended",
        "learned": is_learned,
    }
    return {"next_card": next_card, "counts": counts, "transition": transition}


@router.post("/api/review/requeue")
def requeue_card(card_id: int, root_deck_id: int | None = None,
                 parent_deck_id: int | None = None, unfinished_mode: bool = False,
                 unfinished_scope: str = "unfinished", delay_seconds: int = 60):
    """"New sentence" button: re-show this card ~delay_seconds later WITHOUT any
    scheduling change, and regenerate its sentence in the background.

    Unlike a rating this touches no SRS state (ease/interval/state/lapses/today's
    review count are all untouched). It mirrors /api/review's queue context so the
    card lands back in the same session queue. Returns {next_card, counts} so the
    frontend advances exactly as it does after a rating."""
    card = database.get_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    deck_id = card["deck_id"]
    cat = card["category"]

    # Background: regenerate one fresh sentence for this word (reuses Again infra).
    _spawn_again_regen(card)

    due = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds")

    if unfinished_mode:
        # The unfinished virtual deck isn't backed by an in-memory queue, so there
        # is nothing to soft-requeue into; just advance (regen still happens).
        next_card = database.get_next_unfinished_card(unfinished_scope)
        if next_card:
            next_card["intervals"] = srs.preview_intervals(next_card)
            next_card["fsrs"] = srs.explain_card(next_card)
            _attach_again_sentence(next_card)
        counts = database.count_unfinished(unfinished_scope)
    elif root_deck_id:
        key, build_fn = _key_and_build(root_deck_id=root_deck_id)
        _queue_mgr.soft_requeue(key, card_id, due)
        next_card = _next_card_from_queue(key, build_fn)
        counts = database.count_due_any_cat(root_deck_id)
        counts["by_cat"] = database.count_due_by_category(root_deck_id)
    elif parent_deck_id:
        ids = leaf_ids(parent_deck_id, cat)
        key, build_fn = _key_and_build(ids=ids, category=cat, parent_for_multi=parent_deck_id)
        _queue_mgr.soft_requeue(key, card_id, due)
        next_card = _next_card_from_queue(key, build_fn)
        counts = database.count_due_multi(ids, cat, root_deck_id=parent_deck_id)
        counts["by_cat"] = database.count_due_by_category(parent_deck_id)
    else:
        key, build_fn = _key_and_build(deck_id=deck_id, category=cat)
        _queue_mgr.soft_requeue(key, card_id, due)
        next_card = _next_card_from_queue(key, build_fn)
        counts = database.count_due(deck_id, cat)
        parent_id = database.get_parent_deck_id(deck_id)
        counts["by_cat"] = database.count_due_by_category(parent_id or deck_id)

    logger.info("requeue  card=#%d word=%s cat=%s → re-show at %s (no scheduling change)",
                card_id, card.get("word_zh"), cat, due)
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
        stability=cb.get("stability"),
        difficulty=cb.get("difficulty"),
        last_review=cb.get("last_review"),
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
    restored["fsrs"] = srs.explain_card(restored)
    _attach_again_sentence(restored)

    deck_id         = entry["deck_id"]
    cat             = entry["category"]
    unfinished_mode = entry["unfinished_mode"]
    root_deck_id    = entry["root_deck_id"]
    parent_deck_id  = entry.get("parent_deck_id")

    if unfinished_mode:
        counts = database.count_unfinished(entry.get("unfinished_scope", "unfinished"))
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


@router.get("/api/cards/{card_id}/timeline")
def get_card_timeline(card_id: int):
    return database.get_card_timeline_data(card_id)


@router.post("/api/session-timelines")
def session_timelines(body: dict):
    """Interval timelines for the cards reviewed in one session (summary graph)."""
    ids = [int(i) for i in body.get("ids", [])]
    return database.get_session_timelines(ids)

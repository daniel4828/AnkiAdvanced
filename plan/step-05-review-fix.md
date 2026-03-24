# Step 05 — Review Fix: Parent-Deck Review + Undo Stack

**Branch:** `feat/review-fix`
**Depends on:** Step 01 (DB — soft-delete awareness in card queries)
**Blocks:** nothing directly (frontend already has undo UI)

---

## What to Read First

1. `routes/review.py` — full file
2. `routes/utils.py` — `leaf_ids()` function
3. `database.py` — `get_next_card()`, `get_next_card_multi()`, `get_due_cards()`
4. `recovery/2026-03-23_parent-deck-review-fix.md` — parent deck bug details
5. `recovery/going_manually_through_chats.md` lines 300–500 — undo stack details

---

## Problem 1: Parent Deck Review

When reviewing a **parent deck** (e.g. "Kouyu"), the review only pulled cards from a
single leaf deck. It should pull from ALL leaf decks under the parent.

### Fix in `POST /api/review`

Add `parent_deck_id` parameter:

```python
@router.post("/api/review")
def submit_review(body: dict):
    card_id = body["card_id"]
    rating = body["rating"]
    user_response = body.get("user_response")
    parent_deck_id = body.get("parent_deck_id")  # NEW
    ...
```

When determining the `next_card` to return:
- If `parent_deck_id` is set → use `leaf_ids(parent_deck_id, category)` to get all
  leaf deck IDs, then call `get_next_card_multi(leaf_ids, category)` (or equivalent)
- Otherwise → use existing single-deck logic

### `GET /api/today/{deck_id}/{category}` — counts for parent decks

When `deck_id` is a parent, counts should aggregate across all leaf decks.
Check if `leaf_ids()` already handles this; if not, fix it.

### Frontend

The frontend already passes `deckId` (which may be a parent). Just ensure `parent_deck_id`
is included in the review POST body:
```javascript
// In app.js submitReview():
body.parent_deck_id = deckId;  // or parentDeckId if stored separately
```
Check the current app.js to see where review is submitted and add `parent_deck_id`.

---

## Problem 2: Undo Stack

Add an undo stack to `routes/review.py` so the user can undo the last review.

### In-memory stack (per server session)

```python
_undo_stack = []  # list of undo entries

# Each entry:
# {
#   "card_before": dict,      # card state before rating
#   "log_id": int,            # review_log row to delete
#   "deck_id": int,
#   "parent_deck_id": int | None,
#   "category": str,
# }
```

### Push to stack in `POST /api/review`

Before applying the rating, capture the card's current state and store it.
After applying the rating, get the `log_id` of the just-inserted review_log entry.

```python
# After review:
_undo_stack.append({
    "card_before": card_snapshot,
    "log_id": last_log_id,
    "deck_id": deck_id,
    "parent_deck_id": parent_deck_id,
    "category": category,
})
# Keep stack at max 1 (or small number)
if len(_undo_stack) > 5:
    _undo_stack.pop(0)
```

### `POST /api/review/undo`

```python
@router.post("/api/review/undo")
def undo_review():
    if not _undo_stack:
        raise HTTPException(status_code=400, detail="Nothing to undo")
    entry = _undo_stack.pop()
    # Restore card to pre-rating state
    database.restore_card_state(entry["card_before"])
    # Delete the review log entry
    database.delete_review_log(entry["log_id"])
    # Get next card (from correct pool)
    ...
    return {"next_card": ..., "counts": ...}
```

### New DB functions needed (add in Step 01 if not there):

```python
def restore_card_state(card: dict) -> None:
    """Restore card fields (state, due, ease, interval, step_index, lapses) to a snapshot."""

def delete_review_log(log_id: int) -> None:
    """Delete a specific review_log entry by id."""
```

---

## How to Implement

1. `git checkout -b feat/review-fix` (after Step 01 is merged)
2. Edit `routes/review.py`:
   - Add `parent_deck_id` to `POST /api/review`
   - Add undo stack
   - Add `POST /api/review/undo` endpoint
3. Edit `database.py` if `restore_card_state` and `delete_review_log` are missing
4. Edit `static/app.js` — add `parent_deck_id` to review submission
5. Test: review a parent deck, verify cards come from all child decks
6. Test: review a card, then hit undo — card should revert
7. Commit and open PR

---

## Verification Checklist

- [ ] Reviewing a parent deck pulls cards from all leaf decks
- [ ] `GET /api/today/{parent_id}/{category}` shows correct aggregate counts
- [ ] Undo reverts the last card rating
- [ ] Undo clears the review_log entry
- [ ] Undo returns the correct next card
- [ ] Multiple undos work (up to stack limit)

---

## When you are done

1. Mark the step as 🔄 IN PROGRESS in `plan/PLAN.md` when you start (update the status column)
2. Open a PR with `gh pr create --fill` referencing `Closes #<issue>`
3. Mark as 👀 REVIEW in `plan/PLAN.md` and push the change
4. Daniel reviews and merges — after merge, update status to ✅ DONE

**Always commit `plan/PLAN.md` together with your last code commit so the tracker stays in sync.**

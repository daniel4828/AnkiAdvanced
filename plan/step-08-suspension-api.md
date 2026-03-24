# Step 08 — Suspension Toggle: Category + Deck-Wide

**Branch:** `feat/suspension`
**Depends on:** Step 01 (DB — suspension toggle functions)
**Blocks:** Step 12 (Frontend: suspension badges)

---

## What to Read First

1. `routes/decks.py` — current deck endpoints
2. `database.py` — suspension functions added in Step 01
3. `recovery/2026-03-24_suspension-toggle-all-categories.md` — full spec
4. `recovery/going_manually_through_chats.md` lines 100–300

---

## Goal

Add API endpoints to toggle card suspension at the category level and deck-wide level.
These replace/extend the existing "toggle creating suspension" endpoint.

---

## New Endpoints

### `POST /api/decks/{deck_id}/categories/{category}/toggle-suspension`

Toggle suspension for ALL cards in a deck (and descendants) for a specific category.

```python
@router.post("/api/decks/{deck_id}/categories/{category}/toggle-suspension")
def toggle_category_suspension(deck_id: int, category: str):
    if category not in ("listening", "reading", "creating"):
        raise HTTPException(status_code=400, detail="Invalid category")
    result = database.toggle_category_suspension(deck_id, category)
    return result
    # Returns: {"suspended": bool, "affected": int}
```

Logic (in `database.py`):
1. Find all leaf deck IDs under `deck_id` (including self if leaf)
2. Check if ALL cards of this category in those decks are suspended
3. If all suspended → unsuspend all (set `state='review'` or back to previous state)
4. If not all suspended → suspend all (set `state='suspended'`)
5. Return `{suspended: <new state>, affected: <count changed>}`

**Note:** "suspended" means `cards.state = 'suspended'`. Reversing suspension should
set state back to `'review'` (since we don't know the prior state). Cards in
`new`/`learning`/`relearn` should probably be left alone (or set to `new` when unsuspending).
Check recovery docs for exact behavior.

### `POST /api/decks/{deck_id}/toggle-all-suspension`

Toggle suspension for ALL cards in the deck + ALL descendant decks, across all categories.

```python
@router.post("/api/decks/{deck_id}/toggle-all-suspension")
def toggle_deck_all_suspension(deck_id: int):
    result = database.toggle_deck_all_suspension(deck_id)
    return result
    # Returns: {"suspended": bool, "affected": int}
```

---

## Existing Endpoint to Check

There may be an existing endpoint like `POST /api/decks/{deck_id}/toggle-creating-suspension`.
Check `routes/decks.py`. If it exists:
- Either migrate it to the new generic form
- Or keep it and mark it as deprecated

The new `toggle-all-suspension` is the deck-level equivalent, and `toggle-category-suspension`
handles per-category toggling.

---

## DB Function Details

### `toggle_category_suspension(deck_id, category)`

```python
def toggle_category_suspension(deck_id: int, category: str) -> dict:
    leaf_ids = get_leaf_deck_ids(deck_id)  # includes deck_id itself if it's a leaf
    with _conn() as db:
        # Count total cards for this category
        total = db.execute(
            f"SELECT COUNT(*) FROM cards WHERE deck_id IN ({placeholders}) AND category=? AND deleted_at IS NULL",
            (*leaf_ids, category)
        ).fetchone()[0]
        # Count suspended cards
        suspended_count = db.execute(
            f"SELECT COUNT(*) FROM cards WHERE deck_id IN ({placeholders}) AND category=? AND state='suspended' AND deleted_at IS NULL",
            (*leaf_ids, category)
        ).fetchone()[0]
        all_suspended = (total > 0 and suspended_count == total)

        if all_suspended:
            # Unsuspend all — set to 'review' (or 'new' if interval=0)
            cur = db.execute(
                f"UPDATE cards SET state=CASE WHEN interval=0 THEN 'new' ELSE 'review' END "
                f"WHERE deck_id IN ({placeholders}) AND category=? AND state='suspended' AND deleted_at IS NULL",
                (*leaf_ids, category)
            )
        else:
            # Suspend all non-suspended
            cur = db.execute(
                f"UPDATE cards SET state='suspended' WHERE deck_id IN ({placeholders}) AND category=? "
                f"AND state!='suspended' AND deleted_at IS NULL",
                (*leaf_ids, category)
            )
        return {"suspended": not all_suspended, "affected": cur.rowcount}
```

### `toggle_deck_all_suspension(deck_id)`

Same logic but without the `category` filter — affects all cards in deck+descendants.

---

## How to Implement

1. `git checkout -b feat/suspension` (after Step 01 is merged)
2. Edit `routes/decks.py` — add the two new toggle endpoints
3. Edit `database.py` — add toggle functions if not done in Step 01
4. Test:
   ```bash
   # Suspend all listening cards in deck 1
   curl -X POST http://localhost:8000/api/decks/1/categories/listening/toggle-suspension
   # Toggle all cards in deck 1
   curl -X POST http://localhost:8000/api/decks/1/toggle-all-suspension
   ```
5. Commit and open PR

---

## Verification Checklist

- [ ] `POST /api/decks/{id}/categories/listening/toggle-suspension` suspends all listening cards
- [ ] Second call to same endpoint unsuspends them
- [ ] `POST /api/decks/{id}/toggle-all-suspension` affects all 3 categories
- [ ] Suspended cards don't appear in review queue
- [ ] `affected` count is accurate
- [ ] Works recursively on parent decks (affects all descendants)

---

## When you are done

1. Mark the step as 🔄 IN PROGRESS in `plan/PLAN.md` when you start (update the status column)
2. Open a PR with `gh pr create --fill` referencing `Closes #<issue>`
3. Mark as 👀 REVIEW in `plan/PLAN.md` and push the change
4. Daniel reviews and merges — after merge, update status to ✅ DONE

**Always commit `plan/PLAN.md` together with your last code commit so the tracker stays in sync.**

# Step 07 — Trash System: Backend API

**Branch:** `feat/trash`
**Depends on:** Step 01 (DB — soft-delete columns, trash functions)
**Blocks:** Step 11 (Frontend: trash modal)

---

## What to Read First

1. `routes/decks.py` — full file (understand existing deck endpoints)
2. `database.py` — soft-delete/trash functions added in Step 01
3. `recovery/2026-03-24_trash-browse-redesign.md` — full trash API spec
4. `recovery/2026-03-24_trash-toggle-expand.md` — trash UI interaction (gives API clues)
5. `recovery/going_manually_through_chats.md` lines 200–400

---

## Goal

Add a complete trash (soft-delete) system for decks and cards.

---

## New Endpoints

### `DELETE /api/decks/{deck_id}` — soft-delete a deck

```python
@router.delete("/api/decks/{deck_id}")
def delete_deck(deck_id: int):
    database.soft_delete_deck(deck_id)
    return {"ok": True}
```

Also soft-deletes all cards in the deck (sets `cards.deleted_at`).

### `DELETE /api/decks/{deck_id}/cards` — soft-delete all cards in a deck

```python
@router.delete("/api/decks/{deck_id}/cards")
def delete_deck_cards(deck_id: int):
    count = database.soft_delete_all_cards_in_deck(deck_id)
    return {"deleted": count}
```

### `GET /api/trash` — get all trash

```python
@router.get("/api/trash")
def get_trash():
    return database.get_trash()
    # Returns: {
    #   "decks": [
    #     {
    #       "id": 3,
    #       "name": "Old Deck",
    #       "deleted_at": "2026-03-20T10:00:00",
    #       "expires_at": "2026-04-19T10:00:00",  # deleted_at + 30 days
    #       "card_count": 5,
    #       "cards": [
    #         {"card_id": 1, "word_zh": "你好", "category": "listening", ...}
    #       ]
    #     }
    #   ],
    #   "cards": [
    #     # Orphan cards (card deleted but deck still alive)
    #     {"card_id": 7, "word_zh": "再见", "deck_name": "Kouyu", ...}
    #   ]
    # }
```

### `POST /api/trash/{deck_id}/restore` — restore a soft-deleted deck

```python
@router.post("/api/trash/{deck_id}/restore")
def restore_deck(deck_id: int):
    database.restore_deck(deck_id)
    return {"ok": True}
```

### `DELETE /api/trash/{deck_id}` — hard-delete a trashed deck

```python
@router.delete("/api/trash/{deck_id}")
def purge_deck(deck_id: int):
    database.purge_deck(deck_id)
    return {"ok": True}
```

### `POST /api/trash/cards/{card_id}/restore` — restore a soft-deleted card

```python
@router.post("/api/trash/cards/{card_id}/restore")
def restore_card(card_id: int):
    database.restore_card(card_id)
    return {"ok": True}
```

### `DELETE /api/trash/cards/{card_id}` — hard-delete a card

```python
@router.delete("/api/trash/cards/{card_id}")
def purge_card(card_id: int):
    database.purge_card(card_id)
    return {"ok": True}
```

### `DELETE /api/trash/{deck_id}/cards/{card_id}` — hard-delete card within trashed deck

```python
@router.delete("/api/trash/{deck_id}/cards/{card_id}")
def purge_card_from_deck(deck_id: int, card_id: int):
    database.purge_card(card_id)
    return {"ok": True}
```

---

## Auto-Purge

Consider adding a startup task that calls `database.purge_old_trash(30)` when the server
starts. Add this to `main.py` startup or just document it.

---

## Expires-At Calculation

In `get_trash()`, compute `expires_at` as `deleted_at + 30 days`.
This should be done in Python (or SQL) and returned as ISO string.

---

## How to Implement

1. `git checkout -b feat/trash` (after Step 01 is merged)
2. Edit `routes/decks.py` — add all trash endpoints above
3. Test endpoints with curl:
   ```bash
   # Create then delete a test deck
   curl -X DELETE http://localhost:8000/api/decks/99
   curl http://localhost:8000/api/trash
   curl -X POST http://localhost:8000/api/trash/99/restore
   ```
4. Commit and open PR

---

## Verification Checklist

- [ ] `DELETE /api/decks/{id}` soft-deletes the deck (deck still in DB, `deleted_at` set)
- [ ] Soft-deleted decks don't appear in `GET /api/decks` tree
- [ ] Soft-deleted cards don't appear in review queue
- [ ] `GET /api/trash` returns decks and cards with expiry dates
- [ ] `POST /api/trash/{id}/restore` clears `deleted_at`
- [ ] `DELETE /api/trash/{id}` permanently removes the deck
- [ ] `POST/DELETE /api/trash/cards/{id}/restore|delete` work for individual cards
- [ ] Server starts without errors

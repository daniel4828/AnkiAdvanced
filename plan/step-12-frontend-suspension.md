# Step 12 — Frontend: Suspension Badges

**Branch:** `feat/frontend-suspension`
**Depends on:** Step 08 (Suspension API)
**Blocks:** nothing

---

## What to Read First

1. `static/index.html` — deck tree rendering structure
2. `static/app.js` — `renderDecks()`, deck tree rendering, `loadDecks()`
3. `static/style.css` — category pill styles
4. `recovery/2026-03-24_suspension-toggle-all-categories.md` — full suspension UI spec
5. `recovery/going_manually_through_chats.md` lines 100–300

---

## Goal

Add visual suspension indicators to the deck tree:
1. **Category-level badges:** each category pill (listening/reading/creating) shows ⏸/▶ when hovered (or always if suspended)
2. **Deck-level button:** a ⏸/▶ button in each deck row to toggle all cards

---

## Category Pill Badges

### Current structure (find in HTML/JS)

The deck tree likely renders category pills like:
```html
<span class="cat-pill listening">3</span>
<span class="cat-pill reading">2</span>
<span class="cat-pill creating">0</span>
```

### Updated structure

Wrap each pill in a group and add a suspension toggle:
```html
<span class="cat-pill-group">
  <span class="cat-pill listening" onclick="startReview(deckId, 'listening')">3</span>
  <button class="cat-suspend-btn" onclick="toggleCategorySuspension(deckId, 'listening')"
          title="Suspend/unsuspend listening cards">⏸</button>
</span>
```

When the category IS suspended (all cards suspended):
- The button shows ▶ (always visible, pulsing/colored)
- The pill itself appears muted/greyed

When the category is NOT fully suspended:
- The button shows ⏸ (only visible on row hover)

---

## Deck-Level Suspension Button

Add to each deck row (before the gear icon):

```html
<button class="deck-suspend-btn" onclick="toggleDeckAllSuspension(${deck.id})"
        title="Suspend/unsuspend all cards in deck">
    ${allSuspended ? '▶' : '⏸'}
</button>
```

The button state (`⏸` vs `▶`) should come from the API response.
The `GET /api/decks` response should include a `all_suspended` field per deck,
OR the frontend calls `GET /api/decks/{id}/all-suspended` — check recovery docs
for which approach was used.

**Simplest approach:** Add `all_suspended: bool` to each deck in the `GET /api/decks` response.
Add this to `database.py` `get_deck_tree()` or `get_decks()` function.

---

## App.js Functions

```javascript
async function toggleCategorySuspension(deckId, category) {
    const btn = event.target;
    btn.disabled = true;
    try {
        const result = await api('POST', `/api/decks/${deckId}/categories/${category}/toggle-suspension`);
        // result: {suspended: bool, affected: int}
        await loadDecks();  // refresh tree to show new state
    } catch (e) {
        showError('Failed to toggle suspension: ' + e.message);
    } finally {
        btn.disabled = false;
    }
}

async function toggleDeckAllSuspension(deckId) {
    const btn = event.target;
    btn.disabled = true;
    try {
        const result = await api('POST', `/api/decks/${deckId}/toggle-all-suspension`);
        await loadDecks();
    } catch (e) {
        showError('Failed to toggle suspension: ' + e.message);
    } finally {
        btn.disabled = false;
    }
}
```

---

## CSS

```css
/* Category pill group wrapper */
.cat-pill-group {
    display: inline-flex;
    align-items: center;
    gap: 2px;
}

/* Suspension toggle button — hidden by default, shown on hover */
.cat-suspend-btn {
    display: none;
    background: none;
    border: none;
    cursor: pointer;
    font-size: 10px;
    color: var(--muted);
    padding: 0 2px;
    opacity: 0.6;
    transition: opacity 0.15s;
}
.cat-suspend-btn:hover { opacity: 1; }

/* Show on row hover */
.deck-row:hover .cat-suspend-btn { display: inline; }

/* When category IS suspended — always visible, distinct style */
.cat-suspend-btn.is-suspended {
    display: inline;
    color: var(--warning, #f59e0b);
    opacity: 1;
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

/* Deck-level suspension button */
.deck-suspend-btn {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--muted);
    font-size: 12px;
    padding: 2px 4px;
    opacity: 0;
    transition: opacity 0.15s;
}
.deck-row:hover .deck-suspend-btn { opacity: 0.6; }
.deck-suspend-btn.is-suspended {
    opacity: 1;
    color: var(--warning, #f59e0b);
}
```

---

## Backend: Include `all_suspended` in Deck Tree

In `database.py`, update `get_decks()` or the function that builds the deck tree
to include suspension state per deck. The simplest approach:

```python
# For each deck in the tree, add:
deck["all_suspended"] = get_deck_all_suspended(deck["id"])
```

Or compute it in SQL to avoid N+1 queries.

Check what `routes/decks.py` `GET /api/decks` returns and update accordingly.

---

## How to Implement

1. `git checkout -b feat/frontend-suspension` (after Step 08 is merged)
2. Edit `database.py` — add `all_suspended` field to deck tree response
3. Edit `routes/decks.py` — ensure `GET /api/decks` includes `all_suspended`
4. Edit `static/index.html` — update deck tree template (if static)
5. Edit `static/app.js`:
   - Update deck tree rendering to add suspension badges
   - Add `toggleCategorySuspension()` and `toggleDeckAllSuspension()`
6. Edit `static/style.css` — add suspension badge styles
7. Test: suspend all listening cards in a deck → badge shows ▶ → cards gone from queue
8. Commit and open PR

---

## Verification Checklist

- [ ] Category pills show ⏸ on row hover
- [ ] Suspended categories show ▶ always (pulsing)
- [ ] Clicking ⏸ suspends all cards in that category
- [ ] Clicking ▶ unsuspends all cards
- [ ] Deck-level ⏸ button suspends ALL categories
- [ ] `GET /api/decks` includes `all_suspended` per deck
- [ ] Suspended cards don't appear in review queue
- [ ] UI updates after toggling (no page refresh needed)

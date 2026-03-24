# Step 11 — Frontend: Trash Modal

**Branch:** `feat/frontend-trash`
**Depends on:** Step 07 (Trash API)
**Blocks:** nothing

---

## What to Read First

1. `static/index.html` — understand existing modal patterns
2. `static/app.js` — how other modals are opened/closed
3. `static/style.css` — existing modal styles
4. `recovery/2026-03-24_trash-browse-redesign.md` — trash UI spec
5. `recovery/2026-03-24_trash-toggle-expand.md` — expand/collapse interaction
6. `recovery/going_manually_through_chats.md` lines 200–400

---

## Goal

Add a Trash modal that shows soft-deleted decks and cards with:
- 30-day expiry countdown
- Expand/collapse deck rows to see cards inside
- Restore and hard-delete buttons
- Auto-calculated expiry dates

---

## HTML Structure

Add to `index.html` (near other modals):

```html
<div id="trash-modal" class="modal" style="display:none">
  <div class="modal-overlay" onclick="closeTrash()"></div>
  <div class="modal-content trash-modal-content">
    <div class="modal-header">
      <h2>Trash</h2>
      <button class="modal-close" onclick="closeTrash()">✕</button>
    </div>
    <div class="modal-body">
      <div id="trash-empty" style="display:none" class="trash-empty">
        Trash is empty
      </div>
      <div id="trash-decks-section">
        <h3 class="trash-section-title">Deleted Decks</h3>
        <div id="trash-decks-list"></div>
      </div>
      <div id="trash-cards-section">
        <h3 class="trash-section-title">Deleted Cards</h3>
        <div id="trash-cards-list"></div>
      </div>
    </div>
  </div>
</div>
```

### Deck row template (rendered by JS):

```html
<div class="trash-deck-row" data-deck-id="3">
  <div class="trash-row-main">
    <button class="trash-expand-btn" onclick="toggleTrashDeck(3)">▶</button>
    <span class="trash-name">Old Deck</span>
    <span class="trash-count">5 cards</span>
    <span class="trash-expiry">Expires in 25 days</span>
    <button class="trash-btn restore" onclick="restoreDeck(3)">Restore</button>
    <button class="trash-btn delete" onclick="purgeDeck(3)">Delete</button>
  </div>
  <div class="trash-deck-cards" style="display:none">
    <!-- Card rows inside the deck -->
    <div class="trash-card-row">
      <span class="trash-word">你好</span>
      <span class="trash-cat">listening</span>
      <button class="trash-btn restore" onclick="purgeCardFromDeck(3, 7)">Delete</button>
    </div>
  </div>
</div>
```

### Orphan card row template:

```html
<div class="trash-card-row orphan">
  <span class="trash-word">再见</span>
  <span class="trash-deck">Kouyu</span>
  <span class="trash-cat">reading</span>
  <span class="trash-expiry">Expires in 18 days</span>
  <button class="trash-btn restore" onclick="restoreCard(7)">Restore</button>
  <button class="trash-btn delete" onclick="purgeCard(7)">Delete</button>
</div>
```

---

## App.js Functions

```javascript
let _trashExpandedDecks = new Set();

async function openTrash() {
    document.getElementById('trash-modal').style.display = 'flex';
    await loadTrash();
}

function closeTrash() {
    document.getElementById('trash-modal').style.display = 'none';
}

async function loadTrash() {
    const data = await api('GET', '/api/trash');
    renderTrash(data);
}

function renderTrash(data) {
    const { decks, cards } = data;
    const isEmpty = decks.length === 0 && cards.length === 0;
    document.getElementById('trash-empty').style.display = isEmpty ? 'block' : 'none';
    document.getElementById('trash-decks-section').style.display = decks.length ? 'block' : 'none';
    document.getElementById('trash-cards-section').style.display = cards.length ? 'block' : 'none';

    // Render decks
    const decksList = document.getElementById('trash-decks-list');
    decksList.innerHTML = decks.map(d => renderTrashDeckRow(d)).join('');

    // Render orphan cards
    const cardsList = document.getElementById('trash-cards-list');
    cardsList.innerHTML = cards.map(c => renderTrashCardRow(c)).join('');
}

function daysUntil(isoDate) {
    const diff = new Date(isoDate) - new Date();
    return Math.max(0, Math.ceil(diff / 86400000));
}

function renderTrashDeckRow(deck) {
    const expanded = _trashExpandedDecks.has(deck.id);
    const days = daysUntil(deck.expires_at);
    const cards = deck.cards || [];
    return `
        <div class="trash-deck-row" data-deck-id="${deck.id}">
          <div class="trash-row-main">
            <button class="trash-expand-btn" onclick="toggleTrashDeck(${deck.id})">${expanded ? '▼' : '▶'}</button>
            <span class="trash-name">${deck.name}</span>
            <span class="trash-count">${cards.length} cards</span>
            <span class="trash-expiry ${days <= 5 ? 'expiry-soon' : ''}">Expires in ${days}d</span>
            <button class="trash-btn restore-btn" onclick="restoreDeck(${deck.id})">Restore</button>
            <button class="trash-btn delete-btn" onclick="purgeDeck(${deck.id})">Delete</button>
          </div>
          <div class="trash-deck-cards" style="display:${expanded ? 'block' : 'none'}">
            ${cards.map(c => `
              <div class="trash-card-row">
                <span class="trash-word">${c.word_zh}</span>
                <span class="trash-cat">${c.category}</span>
                <button class="trash-btn delete-btn" onclick="purgeCardFromDeck(${deck.id}, ${c.card_id})">Delete</button>
              </div>
            `).join('')}
          </div>
        </div>`;
}

function renderTrashCardRow(card) {
    const days = daysUntil(card.expires_at);
    return `
        <div class="trash-card-row orphan">
          <span class="trash-word">${card.word_zh}</span>
          <span class="trash-deck">${card.deck_name}</span>
          <span class="trash-cat">${card.category}</span>
          <span class="trash-expiry ${days <= 5 ? 'expiry-soon' : ''}">Expires in ${days}d</span>
          <button class="trash-btn restore-btn" onclick="restoreCard(${card.card_id})">Restore</button>
          <button class="trash-btn delete-btn" onclick="purgeCard(${card.card_id})">Delete</button>
        </div>`;
}

async function toggleTrashDeck(deckId) {
    if (_trashExpandedDecks.has(deckId)) {
        _trashExpandedDecks.delete(deckId);
    } else {
        _trashExpandedDecks.add(deckId);
    }
    await loadTrash();  // re-render (preserves expand state via Set)
}

async function restoreDeck(deckId) {
    await api('POST', `/api/trash/${deckId}/restore`);
    await loadTrash();
    loadDecks();  // refresh deck tree
}

async function purgeDeck(deckId) {
    if (!confirm('Permanently delete this deck and all its cards?')) return;
    await api('DELETE', `/api/trash/${deckId}`);
    await loadTrash();
}

async function restoreCard(cardId) {
    await api('POST', `/api/trash/cards/${cardId}/restore`);
    await loadTrash();
}

async function purgeCard(cardId) {
    if (!confirm('Permanently delete this card?')) return;
    await api('DELETE', `/api/trash/cards/${cardId}`);
    await loadTrash();
}

async function purgeCardFromDeck(deckId, cardId) {
    await api('DELETE', `/api/trash/${deckId}/cards/${cardId}`);
    await loadTrash();
}
```

---

## CSS

Add to `style.css`:

```css
.trash-modal-content { min-width: 500px; max-width: 700px; }
.trash-section-title { font-size: 13px; font-weight: 600; color: var(--muted); margin: 12px 0 6px; text-transform: uppercase; letter-spacing: 0.5px; }
.trash-deck-row { border-bottom: 1px solid var(--border); }
.trash-row-main { display: flex; align-items: center; gap: 8px; padding: 8px 4px; }
.trash-expand-btn { background: none; border: none; cursor: pointer; color: var(--muted); font-size: 11px; width: 20px; }
.trash-name { flex: 1; font-weight: 500; }
.trash-count { color: var(--muted); font-size: 12px; }
.trash-expiry { color: var(--muted); font-size: 12px; }
.trash-expiry.expiry-soon { color: var(--danger); }
.trash-card-row { display: flex; align-items: center; gap: 8px; padding: 6px 28px; font-size: 13px; border-top: 1px solid var(--border-light); }
.trash-word { flex: 1; }
.trash-cat, .trash-deck { color: var(--muted); font-size: 12px; }
.trash-btn { font-size: 12px; padding: 3px 10px; border-radius: 4px; border: none; cursor: pointer; }
.restore-btn { background: color-mix(in srgb, var(--primary) 12%, transparent); color: var(--primary); }
.delete-btn { background: color-mix(in srgb, var(--danger) 12%, transparent); color: var(--danger); }
.trash-empty { text-align: center; color: var(--muted); padding: 32px; }
```

---

## Entry Point

Add a "Trash" button somewhere accessible — e.g. in the main sidebar or deck menu.
Check the current UI for the best placement. A gear/settings area is natural.

---

## How to Implement

1. `git checkout -b feat/frontend-trash` (after Step 07 is merged)
2. Edit `static/index.html` — add trash modal HTML
3. Edit `static/app.js` — add all trash functions
4. Edit `static/style.css` — add trash styles
5. Add a button to open the trash (find appropriate location in existing UI)
6. Test: delete a deck, open trash, restore it, then permanently delete it
7. Commit and open PR

---

## Verification Checklist

- [ ] Trash modal opens and shows deleted decks/cards
- [ ] Deck rows show card count and expiry countdown
- [ ] Toggle ▶/▼ expands/collapses cards inside deck
- [ ] "Restore" restores deck and it reappears in deck tree
- [ ] "Delete" permanently removes with confirmation
- [ ] Individual card restore/delete work
- [ ] Expiry dates show "expiry-soon" styling within 5 days
- [ ] Empty trash shows appropriate message

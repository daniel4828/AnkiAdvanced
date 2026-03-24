# Step 15 — Frontend: HSK Badge + AI Enrich on Card

**Branch:** `feat/frontend-hsk`
**Depends on:** Step 09 (Browse enrich API — `POST /api/word/{id}/ai-enrich`)
**Blocks:** nothing

---

## What to Read First

1. `static/app.js` — card rendering, `renderCard()` or equivalent
2. `static/index.html` — card HTML structure
3. `static/style.css` — existing card styles
4. `recovery/2026-03-24_deepseek-and-story-modal-fix.md` — complete HSK badge code
5. `recovery/going_manually_through_chats.md` lines 1–100 — HSK badge and mic button

---

## Goal

1. Add HSK level badge to every card (front and back)
2. Clicking the badge triggers AI enrichment (fills missing HSK + character data)
3. Rename "Replay" button to microphone emoji 🎤 for TTS
4. Move back-side play button 🔊 to meta row (small, not next to sentence)
5. Remove front-side replay button

---

## HSK Badge

### HTML

Add to card display (in `index.html`):

```html
<!-- In the card meta row (front side) -->
<button id="card-hsk-badge" class="card-hsk-badge"
        onclick="enrichCard()"
        title="Ask AI to fill HSK level &amp; character data"
        style="display:none">
</button>
```

Back-side play button (small, in meta row):
```html
<button id="back-meta-play-btn" class="play-icon-btn play-icon-sm"
        onclick="playSentence()"
        style="display:none"
        title="Play audio">🔊</button>
```

### App.js — Set badge on card render

```javascript
// In renderCard() or wherever card data is applied to the DOM:

// HSK badge — always visible; "HSK -" when unknown (click to AI-fill)
const hskBadge = document.getElementById('card-hsk-badge');
if (hskBadge) {
    hskBadge.textContent = card.hsk_level ? `HSK ${card.hsk_level}` : 'HSK -';
    hskBadge.classList.toggle('hsk-unknown', !card.hsk_level);
    hskBadge.disabled = false;
    hskBadge.style.display = 'inline';
}
```

### `enrichCard()` Function

```javascript
async function enrichCard() {
    if (!card) return;
    const badge = document.getElementById('card-hsk-badge');
    if (!badge) return;
    badge.textContent = '…';
    badge.disabled = true;
    try {
        const updated = await api('POST', `/api/word/${card.word_id}/ai-enrich`);
        // Update in-memory card HSK level
        if (updated?.hsk_level) card.hsk_level = updated.hsk_level;
        badge.textContent = card.hsk_level ? `HSK ${card.hsk_level}` : 'HSK -';
        badge.classList.toggle('hsk-unknown', !card.hsk_level);
        // Refresh word detail if back is visible
        if (updated && isBackVisible()) {
            wordDetails = updated;
            renderVocabDetail();
        }
    } catch (e) {
        badge.textContent = card.hsk_level ? `HSK ${card.hsk_level}` : 'HSK -';
        showError('AI enrich failed: ' + e.message);
    } finally {
        badge.disabled = false;
    }
}
```

---

## CSS: HSK Badge

```css
.card-hsk-badge {
    font-size: 10px;
    font-weight: 700;
    color: var(--primary);
    background: color-mix(in srgb, var(--primary) 12%, transparent);
    border: none;
    border-radius: 4px;
    padding: 2px 6px;
    letter-spacing: 0.3px;
    cursor: pointer;
    transition: opacity 0.15s, background 0.15s;
}
.card-hsk-badge:hover {
    background: color-mix(in srgb, var(--primary) 22%, transparent);
}
.card-hsk-badge:disabled {
    opacity: 0.6;
    cursor: default;
}
.card-hsk-badge.hsk-unknown {
    color: var(--muted);
    background: color-mix(in srgb, var(--muted) 10%, transparent);
}
```

---

## Microphone Button (TTS)

### Replace existing "Replay" or play button

Find the existing TTS trigger button and rename it:
- Old: 🔊 or "Replay" text
- New: 🎤 (microphone emoji)

This applies to the **front-side** TTS trigger. The back-side gets a separate small 🔊 button.

### Front-side:
```html
<button id="front-play-btn" class="play-icon-btn" onclick="playSentence()" title="Play audio">🎤</button>
```

### Back-side (small, in meta row):
```html
<button id="back-meta-play-btn" class="play-icon-btn play-icon-sm" onclick="playSentence()" style="display:none">🔊</button>
```

Show/hide logic: `back-meta-play-btn` appears when card back is revealed.

---

## Auto-Play on Card Reveal

**All categories** (not just listening) should auto-play TTS when the card back is revealed.
Find where the card flip/reveal happens in `app.js` and ensure `playSentence()` is called:

```javascript
function revealCard() {
    // ... show back side ...
    playSentence();  // auto-play for all categories
}
```

Remove any `if (category === 'listening')` guard around `playSentence()`.

---

## HSK Level in Card Data

Ensure `GET /api/today/{deck_id}/{category}` returns `hsk_level` in the card object.
This comes from `get_word_full()` — check that `hsk_level` is included in the SELECT.

---

## How to Implement

1. `git checkout -b feat/frontend-hsk` (after Step 09 is merged)
2. Edit `static/index.html`:
   - Add `#card-hsk-badge` button to card meta area
   - Add `#back-meta-play-btn` to back-side meta area
   - Rename/update front-side TTS button to 🎤
3. Edit `static/app.js`:
   - Update card render to set HSK badge text
   - Add `enrichCard()` function
   - Add auto-play for all categories
   - Show `#back-meta-play-btn` on card reveal
4. Edit `static/style.css` — add `.card-hsk-badge` and `.play-icon-sm` styles
5. Test: load a card without HSK level → shows "HSK -" → click → AI fills it
6. Test: card reveal auto-plays TTS
7. Commit and open PR

---

## Verification Checklist

- [ ] Every card shows HSK badge (blue for known level, grey for "HSK -")
- [ ] Clicking "HSK -" triggers AI enrichment
- [ ] After enrichment, badge updates to "HSK N"
- [ ] Word detail view also updates after enrichment
- [ ] Front-side button shows 🎤
- [ ] Back-side shows small 🔊 in meta row
- [ ] Card reveal auto-plays TTS for all categories
- [ ] No front-side "Replay" button

---

## When you are done

1. Mark the step as 🔄 IN PROGRESS in `plan/PLAN.md` when you start (update the status column)
2. Open a PR with `gh pr create --fill` referencing `Closes #<issue>`
3. Mark as 👀 REVIEW in `plan/PLAN.md` and push the change
4. Daniel reviews and merges — after merge, update status to ✅ DONE

**Always commit `plan/PLAN.md` together with your last code commit so the tracker stays in sync.**

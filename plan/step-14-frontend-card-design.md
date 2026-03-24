# Step 14 — Frontend: Sentence/Chengyu Card Design (Interactive Tooltips)

**Branch:** `feat/frontend-cards`
**Depends on:** Step 01 (DB — `note_components`, `get_note_components()`, `note_type` field)
**Blocks:** nothing

---

## What to Read First

1. `static/app.js` — card rendering functions (`renderCard()`, `renderVocabDetail()`, etc.)
2. `static/index.html` — card display HTML structure
3. `static/style.css` — card styles
4. `recovery/2026-03-23_sentence-chengyu-card-design.md` — full spec
5. `recovery/2026-03-23_full-snapshot_sentences-routing.md` — complete snapshot
6. `recovery/going_manually_through_chats.md` lines 300–500

---

## Goal

Make sentence and chengyu cards interactive:
1. Component words are rendered as hoverable spans
2. Hovering shows a floating tooltip with: word, pinyin, definition, character etymologies
3. Examples for sentence/chengyu are auto-expanded (no toggle needed)
4. Examples for vocabulary are still collapsible

---

## Data Flow

`GET /api/today/{deck_id}/{category}` (or wherever card data comes from) returns:
```json
{
  "word_zh": "一石二鸟",
  "note_type": "chengyu",
  "components": [
    {
      "word_id": 42,
      "word_zh": "石",
      "pinyin": "shí",
      "definition": "stone",
      "position": 0,
      "characters": [
        {"char": "石", "pinyin": "shí", "etymology": "Pictograph of a cliff...", "other_meanings": ["stone","rock"]}
      ]
    },
    ...
  ]
}
```

This data comes from `get_word_full()` which calls `get_note_components()`.
Ensure `GET /api/today` includes this data (it should via `get_word_full`).

---

## `renderInteractiveZh(text, components)`

New function that renders the Chinese text with hoverable component spans:

```javascript
function renderInteractiveZh(text, components) {
    if (!components || components.length === 0) {
        return `<span>${escapeHtml(text)}</span>`;
    }

    // Sort components by position
    const sorted = [...components].sort((a, b) => a.position - b.position);

    // Mark up each component word in the text
    let result = text;
    // Build a map of word positions in the text string
    // Simple approach: replace each component word with a marked span
    // (be careful about overlapping matches — process from right to left or longest first)
    sorted.forEach((comp, idx) => {
        const escaped = escapeHtml(comp.word_zh);
        // Replace first occurrence of comp.word_zh with a hoverable span
        result = result.replace(comp.word_zh, `<span class="iword" data-comp="${idx}"
            onmouseenter="showWordTip(${idx}, this)"
            onmouseleave="hideWordTip()">${comp.word_zh}</span>`);
    });

    // Store components for tooltip access
    window._currentComponents = sorted;

    return result;
}
```

**Note:** The simple replace approach may have issues with overlapping characters.
A more robust approach marks positions first, then renders — check the recovery docs
for the exact implementation that was used.

---

## `showWordTip(idx, el)` — Floating Tooltip

```javascript
let _tipTimeout = null;

function showWordTip(idx, el) {
    clearTimeout(_tipTimeout);
    const comp = window._currentComponents?.[idx];
    if (!comp) return;

    const tip = document.getElementById('word-tip');
    if (!tip) return;

    // Build tooltip content
    let html = `<div class="word-tip-word">${comp.word_zh}</div>`;
    html += `<div class="word-tip-pinyin">${comp.pinyin || ''}</div>`;
    html += `<div class="word-tip-def">${comp.definition || ''}</div>`;

    if (comp.characters && comp.characters.length > 0) {
        html += '<div class="word-tip-chars">';
        comp.characters.forEach(c => {
            html += `<div class="word-tip-char">
                <span class="word-tip-char-zh">${c.char}</span>
                <span class="word-tip-char-py">${c.pinyin || ''}</span>
                ${c.etymology ? `<div class="word-tip-etym">${c.etymology}</div>` : ''}
            </div>`;
        });
        html += '</div>';
    }

    tip.innerHTML = html;
    tip.style.display = 'block';

    // Position near element
    const rect = el.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    let top = rect.top - tipRect.height - 8;
    if (top < 8) top = rect.bottom + 8;  // show below if no room above
    let left = rect.left;
    if (left + 250 > window.innerWidth) left = window.innerWidth - 260;
    tip.style.top = `${top}px`;
    tip.style.left = `${left}px`;
}

function hideWordTip() {
    _tipTimeout = setTimeout(() => {
        const tip = document.getElementById('word-tip');
        if (tip) tip.style.display = 'none';
    }, 80);
}
```

---

## HTML: Tooltip Container

Add once to `index.html` (outside all modals, near body end):

```html
<div id="word-tip" class="word-tip" style="display:none"></div>
```

---

## CSS

```css
/* Interactive word spans in sentence/chengyu */
.iword {
    cursor: pointer;
    border-bottom: 1px dotted var(--primary);
    color: var(--primary);
    transition: background 0.1s;
}
.iword:hover {
    background: color-mix(in srgb, var(--primary) 10%, transparent);
    border-radius: 2px;
}

/* Floating tooltip */
.word-tip {
    position: fixed;
    z-index: 9999;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    max-width: 260px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.12);
    pointer-events: none;
}
.word-tip-word { font-size: 20px; font-weight: 700; color: var(--text); }
.word-tip-pinyin { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.word-tip-def { font-size: 13px; color: var(--text); margin-bottom: 8px; }
.word-tip-chars { border-top: 1px solid var(--border); padding-top: 6px; }
.word-tip-char { margin-top: 4px; }
.word-tip-char-zh { font-size: 16px; font-weight: 600; }
.word-tip-char-py { font-size: 11px; color: var(--muted); margin-left: 4px; }
.word-tip-etym { font-size: 11px; color: var(--muted); margin-top: 2px; line-height: 1.4; }
```

---

## Examples: Auto-Expand for Sentence/Chengyu

In `renderVocabDetail()` (or wherever examples are shown), check `note_type`:

```javascript
const isInteractive = ['sentence', 'chengyu', 'expression'].includes(card.note_type);
// For sentence/chengyu: expand examples by default, don't show toggle
// For vocabulary: keep examples collapsible
const examplesExpanded = isInteractive;
```

---

## Card Rendering Integration

In the main card rendering function, when the card has `note_type` of `sentence` or `chengyu`:

```javascript
// Replace plain text rendering with interactive rendering
const zhHtml = renderInteractiveZh(card.word_zh, card.components);
document.getElementById('card-word').innerHTML = zhHtml;
// Or wherever the word is displayed
```

---

## How to Implement

1. `git checkout -b feat/frontend-cards` (after Step 01 is merged — needs `note_type` + `components`)
2. Edit `static/index.html` — add `<div id="word-tip">` tooltip container
3. Edit `static/app.js`:
   - Add `renderInteractiveZh()`
   - Add `showWordTip()` and `hideWordTip()`
   - Update card rendering to use interactive mode for sentence/chengyu
   - Update example toggle logic for auto-expand
4. Edit `static/style.css` — add `.iword` and `.word-tip` styles
5. Test: review a sentence-type card, hover over component words
6. Commit and open PR

---

## Verification Checklist

- [ ] Sentence/chengyu cards show component words as hoverable spans
- [ ] Hovering shows tooltip with word, pinyin, definition
- [ ] Tooltip includes character etymology when available
- [ ] Tooltip positioning doesn't go off-screen
- [ ] Tooltip disappears after mouse leaves (80ms delay)
- [ ] Examples for sentence/chengyu are auto-expanded
- [ ] Examples for vocabulary are still collapsible (unchanged behavior)
- [ ] Vocabulary cards still render normally (no `.iword` spans)

---

## When you are done

1. Mark the step as 🔄 IN PROGRESS in `plan/PLAN.md` when you start (update the status column)
2. Open a PR with `gh pr create --fill` referencing `Closes #<issue>`
3. Mark as 👀 REVIEW in `plan/PLAN.md` and push the change
4. Daniel reviews and merges — after merge, update status to ✅ DONE

**Always commit `plan/PLAN.md` together with your last code commit so the tracker stays in sync.**

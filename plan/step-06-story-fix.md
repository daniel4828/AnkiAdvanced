# Step 06 — Story Fix: Sentences Deck Suppression + Mixed Mode

**Branch:** `feat/story-fix`
**Depends on:** Step 01 (DB — `is_sentences_deck()`)
**Blocks:** Step 10 (Frontend: story modal)

---

## What to Read First

1. `routes/story.py` — full file
2. `database.py` — `is_sentences_deck()` function (added in Step 01)
3. `recovery/2026-03-24_story-generation-fix.md` — main reference
4. `recovery/2026-03-23_sentences-deck-routing.md` — sentences deck logic
5. `recovery/going_manually_through_chats.md` lines 1–100 — mixed mode fix

---

## Problem 1: Sentences Deck Story Suppression

The Sentences deck (and its child decks) should **not** generate stories — they ARE the
sentences. Currently story generation is called regardless of deck type.

### Fix in `routes/story.py`

Add an early return for Sentences decks:

```python
@router.get("/api/story/{deck_id}/{category}")
def get_story(deck_id: int, category: str):
    if database.is_sentences_deck(deck_id):
        return {"story": None, "sentences": [], "has_story": False}
    # ... existing logic

@router.post("/api/story/{deck_id}/{category}/regenerate")
def regenerate_story(deck_id: int, category: str, body: dict = {}):
    if database.is_sentences_deck(deck_id):
        return {"story": None, "sentences": [], "has_story": False}
    # ... existing logic

@router.get("/api/story/{deck_id}/{category}/count")
def story_count(deck_id: int, category: str):
    if database.is_sentences_deck(deck_id):
        return {"count": 0, "has_story": False}
    # ... existing logic
```

---

## Problem 2: Mixed Mode Story Generation Race Condition

**Bug:** In `_doStartReviewMixed()` in `app.js`, all stories were fetched with
fire-and-forget (`fetch()` without `await`), so the `story` global was never set
before the first card rendered.

**Fix in `app.js`:**

```javascript
async function _doStartReviewMixed() {
    // Await story for the FIRST card's category
    const firstCategory = /* determine first card category */;
    story = await fetchStory(deckId, firstCategory);
    renderStory();  // render with first story

    // Fire-and-forget for remaining categories
    const otherCategories = ['listening', 'reading', 'writing'].filter(c => c !== firstCategory);
    for (const cat of otherCategories) {
        fetchStory(deckId, cat).then(s => {
            if (/* current category matches */) {
                story = s;
                renderStory();
            }
        });
    }
}
```

Apply the same pattern to `_doStartReviewUnfinished()`.

---

## Problem 3: Story Modal — Accept Model + HSK Parameters

The `POST /api/story/{deck_id}/{category}` and
`POST /api/story/{deck_id}/{category}/regenerate` endpoints should accept:

```json
{
  "model": "deepseek-chat",
  "max_hsk": 2,
  "topic": "at the hospital"
}
```

Update story routes to extract these from request body and pass to `ai.generate_story()`.
The `generate_story()` function should already have `model` param after Step 02.

```python
@router.post("/api/story/{deck_id}/{category}/regenerate")
def regenerate_story(deck_id: int, category: str, body: dict = {}):
    if database.is_sentences_deck(deck_id):
        return {"story": None, "sentences": [], "has_story": False}

    model = body.get("model", "deepseek-chat")
    max_hsk = body.get("max_hsk", 2)
    topic = body.get("topic", "")
    ...
    # pass model, max_hsk, topic to generate_story()
```

---

## How to Implement

1. `git checkout -b feat/story-fix` (after Step 01 is merged)
2. Edit `routes/story.py`:
   - Add Sentences deck early returns
   - Add model/max_hsk/topic extraction from body
3. Edit `static/app.js`:
   - Fix mixed mode story await pattern
   - Fix unfinished mode story await pattern
4. Test: open a Sentences deck → no story should appear
5. Test: start mixed mode review → story should appear for first card
6. Test: regenerate story with DeepSeek model
7. Commit and open PR

---

## Verification Checklist

- [ ] Sentences deck returns `{has_story: false}` from story endpoints
- [ ] Mixed mode first card has story available immediately
- [ ] Background story fetches populate story when category changes
- [ ] `POST /api/story/.../regenerate` accepts `model` parameter
- [ ] Server starts without errors

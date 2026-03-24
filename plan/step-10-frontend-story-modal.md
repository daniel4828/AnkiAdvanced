# Step 10 — Frontend: Story Modal Model Selector

**Branch:** `feat/frontend-story`
**Depends on:** Step 02 (AI provider — model list), Step 06 (Story fix — accepts model param)
**Blocks:** nothing

---

## What to Read First

1. `static/index.html` — find existing story setup modal
2. `static/app.js` — `openStorySetup()`, `_doStartReviewMixed()`, `startReview()`, story-related functions
3. `static/style.css` — existing modal styles
4. `recovery/2026-03-24_deepseek-and-story-modal-fix.md` — model selector UI
5. `recovery/going_manually_through_chats.md` lines 1–100 — model list, defaults

---

## Goal

Update the story setup modal to include:
1. Model selector dropdown (DeepSeek, GLM, Qwen, Claude options)
2. Max HSK slider (1–6, default 2)
3. Topic text input (optional)
4. Pass all three to the story generation API

---

## Story Setup Modal HTML

The modal already exists. Update or replace the model selector area:

```html
<!-- Model selector -->
<div class="modal-field">
  <label for="story-model">AI Model</label>
  <select id="story-model">
    <option value="deepseek-chat" selected>DeepSeek Chat (default)</option>
    <option value="glm-4-flash">GLM-4 Flash (free)</option>
    <option value="glm-4-air">GLM-4 Air</option>
    <option value="qwen-turbo">Qwen Turbo</option>
    <option value="claude-haiku-4-5-20251001">Claude Haiku</option>
    <option value="claude-sonnet-4-6">Claude Sonnet</option>
  </select>
</div>

<!-- Max HSK slider -->
<div class="modal-field">
  <label for="story-max-hsk">Background vocab max HSK: <span id="story-hsk-val">2</span></label>
  <input type="range" id="story-max-hsk" min="1" max="6" value="2"
         oninput="document.getElementById('story-hsk-val').textContent = this.value">
</div>

<!-- Topic input -->
<div class="modal-field">
  <label for="story-topic">Topic (optional)</label>
  <input type="text" id="story-topic" placeholder="e.g. at the hospital, cooking, travel">
</div>
```

---

## App.js Changes

### Pass model/hsk/topic when generating story

In `startReview()`, `_doStartReviewMixed()`, or wherever `POST /api/story/.../regenerate` is called:

```javascript
function getStoryParams() {
    return {
        model: document.getElementById('story-model')?.value || 'deepseek-chat',
        max_hsk: parseInt(document.getElementById('story-max-hsk')?.value || '2'),
        topic: document.getElementById('story-topic')?.value?.trim() || '',
    };
}

async function fetchStory(deckId, category) {
    const params = getStoryParams();
    return await api('POST', `/api/story/${deckId}/${category}/regenerate`, params);
}
```

Also update `regenerateStory()` to pass params.

### Story modal sizing

Ensure the modal has appropriate sizing (not too large, not too small):
```css
#story-setup-modal .modal-content {
    min-width: min(75vw, 600px);
    max-height: 75vh;
    overflow-y: auto;
}
```

---

## `regenerateStory()` Function

```javascript
async function regenerateStory() {
    const params = getStoryParams();
    showLoading('Generating story...');
    try {
        const result = await api('POST', `/api/story/${deckId}/${category}/regenerate`, params);
        story = result;
        renderStory();
    } catch (e) {
        showError('Story generation failed: ' + e.message);
    } finally {
        hideLoading();
    }
}
```

---

## How to Implement

1. `git checkout -b feat/frontend-story` (after Steps 02 and 06 are merged)
2. Edit `static/index.html` — update story setup modal with model/hsk/topic fields
3. Edit `static/app.js` — add `getStoryParams()`, update story fetch calls
4. Edit `static/style.css` if modal sizing needs adjustment
5. Test: open story setup modal, select DeepSeek, set HSK 3, enter topic, generate
6. Commit and open PR

---

## Verification Checklist

- [ ] Model dropdown shows all 6 options with DeepSeek as default
- [ ] HSK slider shows current value (1–6)
- [ ] Topic input is optional (empty = no topic)
- [ ] Story generates with selected model
- [ ] Regenerate button passes updated params
- [ ] Modal sizing is reasonable on all screen sizes

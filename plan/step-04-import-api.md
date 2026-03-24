# Step 04 — Import API Endpoints (Preview + Upload)

**Branch:** `feat/import-api`
**Depends on:** Step 01 (DB), Step 03 (Importer)
**Blocks:** Step 13 (Frontend: import modal)

---

## What to Read First

1. `routes/imports.py` — current file (understand existing `/api/import` endpoint)
2. `importer.py` — especially `preview_yaml_content()` and updated import function
3. `recovery/2026-03-23_import-ui-backend.md` — API design
4. `recovery/going_manually_through_chats.md` lines 100–300 — request/response details

---

## Goal

Add two new endpoints to `routes/imports.py`:
1. `POST /api/import/preview` — parse YAML, return preview without writing
2. `POST /api/import/upload` — full import with conflict resolution

---

## `POST /api/import/preview`

**Request:** `multipart/form-data`
- `file`: YAML file upload

**Response:**
```json
{
  "entries": [
    {
      "simplified": "你好",
      "note_type": "vocabulary",
      "status": "ok",
      "reason": null,
      "raw_yaml": "simplified: 你好\npinyin: nǐ hǎo\n..."
    },
    {
      "simplified": "学习",
      "note_type": "vocabulary",
      "status": "duplicate",
      "reason": "Word already has cards",
      "raw_yaml": "..."
    }
  ],
  "summary": {"ok": 5, "duplicate": 2, "invalid": 0, "unknown_type": 0},
  "conflicts": [
    {
      "word_zh": "学习",
      "existing": {"pinyin": "xué xí", "definition": "to study", "traditional": null},
      "incoming": {"pinyin": "xuéxí", "definition": "study", "traditional": null}
    }
  ]
}
```

**Implementation:**
```python
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
import json

@router.post("/api/import/preview")
async def preview_import(file: UploadFile = File(...)):
    content = (await file.read()).decode("utf-8")
    try:
        result = importer.preview_yaml_content(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result
```

---

## `POST /api/import/upload`

**Request:** `multipart/form-data`
- `file`: YAML file upload
- `deck_id`: int (optional)
- `deck_path`: str (optional, e.g. `"Kouyu::Advanced"`)
- `deck_name`: str (optional)
- `resolutions`: JSON string (optional), e.g. `'{"学习": "update", "工作": "keep"}'`

**Response:**
```json
{
  "deck_id": 3,
  "imported": 5,
  "skipped_duplicate": 2,
  "skipped_invalid": 0,
  "skipped_entries": []
}
```

**Implementation:**
```python
@router.post("/api/import/upload")
async def upload_import(
    file: UploadFile = File(...),
    deck_id: int | None = Form(None),
    deck_path: str | None = Form(None),
    deck_name: str | None = Form(None),
    resolutions: str | None = Form(None),
):
    content = (await file.read()).decode("utf-8")
    parsed_resolutions = {}
    if resolutions:
        try:
            parsed_resolutions = json.loads(resolutions)
        except json.JSONDecodeError:
            pass
    try:
        result = importer.import_yaml_content(
            content=content,
            deck_id=deck_id,
            deck_path=deck_path,
            deck_name=deck_name,
            resolutions=parsed_resolutions,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result
```

---

## Keep Existing Endpoint

The existing `POST /api/import` (directory scan) must still work unchanged.

---

## Router Registration

Ensure the imports router is registered in `main.py`. Check current registration:
```python
# main.py should have:
from routes import imports
app.include_router(imports.router)
```

---

## How to Implement

1. `git checkout -b feat/import-api` (after Steps 01 and 03 are merged)
2. Edit `routes/imports.py` — add the two new endpoints
3. Test via curl or browser:
   ```bash
   curl -X POST http://localhost:8000/api/import/preview \
     -F "file=@imports/Kouyu/test.yaml"
   ```
4. Commit and open PR

---

## Verification Checklist

- [ ] `POST /api/import/preview` returns correct preview for a YAML file
- [ ] `POST /api/import/upload` imports entries and returns counts
- [ ] `deck_path` creates nested decks correctly (using `get_or_create_deck_path`)
- [ ] `resolutions` JSON is parsed and applied correctly
- [ ] Existing `POST /api/import` still works
- [ ] Server starts without errors

---

## When you are done

1. Mark the step as 🔄 IN PROGRESS in `plan/PLAN.md` when you start (update the status column)
2. Open a PR with `gh pr create --fill` referencing `Closes #<issue>`
3. Mark as 👀 REVIEW in `plan/PLAN.md` and push the change
4. Daniel reviews and merges — after merge, update status to ✅ DONE

**Always commit `plan/PLAN.md` together with your last code commit so the tracker stays in sync.**

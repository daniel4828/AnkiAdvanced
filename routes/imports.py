import json
import logging

import database
import importer
from fastapi import APIRouter, Form, HTTPException, UploadFile

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/import/preview")
async def preview_import(file: UploadFile):
    """Parse a YAML file and return a preview without writing to the DB."""
    content = (await file.read()).decode("utf-8")
    return importer.preview_yaml_content(content)


@router.post("/api/import/upload")
async def upload_import(
    file: UploadFile,
    deck_id: int | None = Form(None),
    deck_path: str | None = Form(None),
    deck_name: str | None = Form(None),
    resolutions: str | None = Form(None),  # JSON: {"word_zh": "keep"|"update"}
):
    """Import a YAML file into a deck.

    Deck resolution order:
      1. deck_id   — existing deck id
      2. deck_path — Anki-style 'Parent::Child' path (creates hierarchy if needed)
      3. deck_name — creates a new top-level deck with this name
    """
    if deck_id is None and not deck_path and not deck_name:
        raise HTTPException(status_code=400, detail="Provide deck_id, deck_path, or deck_name")

    content = (await file.read()).decode("utf-8")

    if deck_id is None:
        if deck_path:
            try:
                deck_id = database.get_or_create_deck_path(deck_path)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        else:
            all_id = database.get_all_deck_id()
            preset_id = database.get_preset_for_deck(all_id)["id"]
            deck_id = database.insert_deck(deck_name, parent_id=all_id, preset_id=preset_id)

    if deck_id == database.get_all_deck_id():
        raise HTTPException(status_code=400, detail="Cannot import directly into 'All' — select a specific sub-deck")

    resolution_map: dict = {}
    if resolutions:
        try:
            resolution_map = json.loads(resolutions)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="resolutions must be valid JSON")

    result = importer.import_yaml_content(content, deck_id, resolutions=resolution_map)
    return {"deck_id": deck_id, **result}

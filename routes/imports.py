import json
import logging
import os

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
    resolutions: str | None = Form(None),    # JSON: {"word_zh": "keep"|"update"|"custom"}
    card_configs: str | None = Form(None),   # JSON: {word_zh: {include, deck_path, suspended, ai_fill}}
    custom_fields: str | None = Form(None),  # JSON: {word_zh: {pinyin, definition, traditional}}
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

    card_configs_map: dict = {}
    if card_configs:
        try:
            card_configs_map = json.loads(card_configs)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="card_configs must be valid JSON")

    custom_fields_map: dict = {}
    if custom_fields:
        try:
            custom_fields_map = json.loads(custom_fields)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="custom_fields must be valid JSON")

    result = importer.import_yaml_content(
        content, deck_id,
        resolutions=resolution_map,
        card_configs=card_configs_map,
        custom_fields=custom_fields_map,
    )
    return {"deck_id": deck_id, **result}


@router.post("/api/import/directory")
async def import_from_directory(
    deck_id: int | None = Form(None),
    deck_path: str | None = Form(None),
    deck_name: str | None = Form(None),
    imports_dir: str = Form("imports"),
):
    """Scan the imports/ directory recursively and import all YAML files.

    Deck resolution order:
      1. deck_id   — existing deck id
      2. deck_path — Anki-style 'Parent::Child' path (creates hierarchy if needed)
      3. deck_name — creates a new top-level deck with this name
    """
    if deck_id is None and not deck_path and not deck_name:
        raise HTTPException(status_code=400, detail="Provide deck_id, deck_path, or deck_name")

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

    # Collect all YAML files
    yaml_files = []
    if os.path.isdir(imports_dir):
        for dirpath, dirnames, filenames in os.walk(imports_dir):
            dirnames.sort()
            for fn in sorted(f for f in filenames if f.endswith((".yaml", ".yml"))):
                yaml_files.append(os.path.join(dirpath, fn))

    if not yaml_files:
        raise HTTPException(status_code=404, detail=f"No YAML files found in {imports_dir}/")

    total_imported = 0
    total_duplicate = 0
    total_invalid = 0
    errors = []

    for filepath in yaml_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            errors.append({"file": os.path.basename(filepath), "problem": str(e)})
            continue

        result = importer.import_yaml_content(content, deck_id)
        if result.get("yaml_error"):
            err = result["yaml_error"]
            err["file"] = os.path.relpath(filepath, imports_dir)
            errors.append(err)
            continue

        total_imported += result.get("imported", 0)
        total_duplicate += result.get("skipped_duplicate", 0)
        total_invalid += result.get("skipped_invalid", 0)

    return {
        "deck_id": deck_id,
        "imported": total_imported,
        "skipped_duplicate": total_duplicate,
        "skipped_invalid": total_invalid,
        "errors": errors,
        "files_processed": len(yaml_files),
    }

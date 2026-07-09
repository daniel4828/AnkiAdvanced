import json
import logging
import os
import threading
import time
import uuid
from datetime import date, timedelta

import ai
import database
import importer
from fastapi import APIRouter, Form, HTTPException, UploadFile

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Background import jobs (issue #458) — the previous /api/import/upload ran
# the AI-heavy import synchronously, blocking the browser for 1-2 minutes.
# Now the request just kicks off a daemon thread and returns a job id; the
# frontend polls /api/import/progress/{job_id} for status.
# ---------------------------------------------------------------------------
_import_jobs: dict[str, dict] = {}
_import_jobs_lock = threading.Lock()
_MAX_IMPORT_JOBS = 10


def _prune_import_jobs() -> None:
    """Keep at most _MAX_IMPORT_JOBS entries, oldest-first, never evicting a
    job that's still running."""
    with _import_jobs_lock:
        if len(_import_jobs) <= _MAX_IMPORT_JOBS:
            return
        for job_id in list(_import_jobs.keys()):
            if len(_import_jobs) <= _MAX_IMPORT_JOBS:
                break
            if _import_jobs[job_id]["status"] == "running":
                continue
            del _import_jobs[job_id]


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

    job_id = uuid.uuid4().hex[:8]
    with _import_jobs_lock:
        _import_jobs[job_id] = {
            "status": "running",
            "message": "Importing…",
            "started_at": time.time(),
        }
    _prune_import_jobs()

    def _run_import():
        try:
            result = importer.import_yaml_content(
                content, deck_id,
                resolutions=resolution_map,
                card_configs=card_configs_map,
                custom_fields=custom_fields_map,
            )
            with _import_jobs_lock:
                started_at = _import_jobs[job_id]["started_at"]
                _import_jobs[job_id] = {
                    "status": "done",
                    "message": "Import complete",
                    "summary": {"deck_id": deck_id, **result},
                    "started_at": started_at,
                }
        except Exception as e:
            logger.exception("Unhandled error during import (deck_id=%s): %s", deck_id, e)
            with _import_jobs_lock:
                started_at = _import_jobs.get(job_id, {}).get("started_at", time.time())
                _import_jobs[job_id] = {
                    "status": "error",
                    "message": "Import failed",
                    "error": str(e),
                    "started_at": started_at,
                }

    threading.Thread(target=_run_import, daemon=True).start()
    return {"job_id": job_id}


@router.get("/api/import/progress/{job_id}")
def import_progress(job_id: str):
    """Poll status for a background import job started by /api/import/upload."""
    job = _import_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


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


@router.post("/api/quick-add-word")
def quick_add_word(body: dict):
    """Add a compound word to tomorrow's Daily deck with AI-generated fields.

    Body: { word_zh, pinyin?, meaning? }
    Returns: { status: "created"|"added_to_deck"|"already_in_deck", entry_id, deck_path, deck_id }
    """
    word_zh = (body.get("word_zh") or "").strip()
    if not word_zh:
        raise HTTPException(status_code=400, detail="word_zh is required")

    pinyin = (body.get("pinyin") or "").strip()
    meaning = (body.get("meaning") or "").strip()

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    deck_path = f"Daily::{tomorrow}"
    deck_id = database.get_or_create_deck_path(deck_path)
    # Cards must live in the per-category leaf decks ('<date> · Listening/Reading/Creating'),
    # not the category-less parent — otherwise due counts and review queues never see them.
    leaf_decks = database.get_or_create_category_decks(deck_id, tomorrow)

    existing = database.get_word_by_zh(word_zh)
    if existing:
        entry_id = existing["id"]
        conn = database.get_db()
        leaf_ids = tuple(leaf_decks.values())
        placeholders = ",".join("?" * len(leaf_ids))
        already = conn.execute(
            f"SELECT id FROM cards WHERE word_id = ? AND deck_id IN ({placeholders}) "
            "AND deleted_at IS NULL LIMIT 1",
            (entry_id, *leaf_ids),
        ).fetchone()
        conn.close()
        if already:
            return {"status": "already_in_deck", "entry_id": entry_id,
                    "deck_path": deck_path, "deck_id": deck_id}
        status = "added_to_deck"
    else:
        word_data = {
            "word_zh": word_zh,
            "pinyin": pinyin,
            "definition": meaning,
            "note_type": "vocabulary",
        }
        if not os.environ.get("DISABLE_AI"):
            try:
                result = ai.regenerate_entry_fields(
                    word_data, [], ["definition", "definition_zh", "definition_de", "pos"]
                )
                for field in ("definition", "definition_zh", "definition_de", "pos"):
                    if result.get(field):
                        word_data[field] = result[field]
            except Exception as exc:
                logger.warning("quick_add_word: AI generation failed for %r: %s", word_zh, exc)

        entry_id = database.insert_word(word_data)
        status = "created"

    for category in ("listening", "reading", "creating"):
        database.insert_card(entry_id, category, leaf_decks[category], state="new", due=tomorrow)

    return {"status": status, "entry_id": entry_id, "deck_path": deck_path, "deck_id": deck_id}


@router.post("/api/save-word")
def save_word(body: dict):
    """Stage a compound word in the fixed 'Saved' deck as suspended cards.

    Unlike /api/quick-add-word this does NOT call the AI and does NOT activate
    the cards — content is generated later on demand, and the word only enters
    the study algorithm when promoted to a Daily deck (see /api/saved/{id}/promote).

    Body: { word_zh, pinyin?, meaning? }
    Returns: { status: "saved"|"already_saved"|"exists_elsewhere", entry_id, saved_deck_id }
    """
    word_zh = (body.get("word_zh") or "").strip()
    if not word_zh:
        raise HTTPException(status_code=400, detail="word_zh is required")

    pinyin = (body.get("pinyin") or "").strip()
    meaning = (body.get("meaning") or "").strip()

    saved_deck_id = database.get_or_create_saved_deck()

    existing = database.get_word_by_zh(word_zh)
    if existing:
        entry_id = existing["id"]
        conn = database.get_db()
        deck_ids = {
            r["deck_id"] for r in conn.execute(
                "SELECT deck_id FROM cards WHERE word_id=? AND deleted_at IS NULL",
                (entry_id,),
            ).fetchall()
        }
        conn.close()
        if saved_deck_id in deck_ids:
            return {"status": "already_saved", "entry_id": entry_id, "saved_deck_id": saved_deck_id}
        if deck_ids:
            # Word already lives in a real deck — nothing to stage.
            return {"status": "exists_elsewhere", "entry_id": entry_id, "saved_deck_id": saved_deck_id}
    else:
        entry_id = database.insert_word({
            "word_zh": word_zh,
            "pinyin": pinyin,
            "definition": meaning,
            "note_type": "vocabulary",
        })

    for category in ("listening", "reading", "creating"):
        database.insert_card(entry_id, category, saved_deck_id, state="suspended")

    return {"status": "saved", "entry_id": entry_id, "saved_deck_id": saved_deck_id}


@router.post("/api/saved/{word_id}/promote")
def promote_saved(word_id: int):
    """Move a saved word's suspended cards into tomorrow's Daily deck as active new cards."""
    saved_deck_id = database.get_or_create_saved_deck()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    deck_path = f"Daily::{tomorrow}"
    daily_deck_id = database.get_or_create_deck_path(deck_path)
    leaf_decks = database.get_or_create_category_decks(daily_deck_id, tomorrow)

    count = database.promote_saved_word(word_id, leaf_decks, saved_deck_id, tomorrow)
    if not count:
        raise HTTPException(status_code=404, detail="No saved cards found for this word")

    return {"status": "promoted", "count": count, "deck_path": deck_path, "deck_id": daily_deck_id}

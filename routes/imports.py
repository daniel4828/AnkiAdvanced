import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import date, timedelta

import ai
import database
import importer
from fastapi import APIRouter, Form, HTTPException, UploadFile

from .utils import ai_disabled

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


# ---------------------------------------------------------------------------
# In-app "add a word" (issue #627) — one button, one Chinese word, a full
# de-zh-bot style entry in today's Daily deck.
#
# Everything downstream is the ordinary import path: importer._create_cards
# dues cards at database.anki_today() and importer._make_leaf_decks builds the
# very same '<date> · Listening/Reading/Creating' children that
# database.get_or_create_category_decks does. So handing the generated YAML to
# import_yaml_content() with today's Daily deck yields cards indistinguishable
# from a hand-imported entry — no special-case card creation here.
# ---------------------------------------------------------------------------


def _card_deck_ids(entry_id: int) -> set[int]:
    """Deck ids holding this entry's live cards."""
    conn = database.get_db()
    rows = conn.execute(
        "SELECT DISTINCT deck_id FROM cards WHERE word_id = ? AND deleted_at IS NULL",
        (entry_id,),
    ).fetchall()
    conn.close()
    return {r["deck_id"] for r in rows}


@router.post("/api/add-word-ai")
def add_word_ai(body: dict):
    """Add one Chinese word to a Daily deck with an AI-generated entry.

    Body: { word_zh, day?: "today"|"tomorrow" }
    Returns either {job_id, deck_path} — generation runs in the background,
    poll /api/add-word-ai/progress/{job_id} — or, when the word is already in
    the database, a finished {status, ...} with no AI call at all.
    """
    word_zh = (body.get("word_zh") or "").strip()
    if not word_zh:
        raise HTTPException(status_code=400, detail="word_zh is required")
    if not re.search(r"[一-鿿]", word_zh):
        raise HTTPException(status_code=400,
                            detail="Please enter the word in Chinese characters")

    day = (body.get("day") or "today").strip()
    if day not in ("today", "tomorrow"):
        raise HTTPException(status_code=400, detail="day must be 'today' or 'tomorrow'")
    # A daily deck dated in the future is locked until its date arrives
    # (database.parse_daily_deck_date), which is exactly the "stage it for
    # tomorrow" semantics — the cards just have to be due then too (#636).
    due_offset_days = 1 if day == "tomorrow" else 0
    target_day = (date.today() + timedelta(days=due_offset_days)).isoformat()
    deck_path = f"Daily::{target_day}"
    deck_id = database.get_or_create_deck_path(deck_path)

    # Known word → don't pay for a second generation; the importer would skip
    # it as a duplicate anyway. What we can do depends on where its cards are:
    # `cards` has UNIQUE(word_id, category), so a word owns exactly one card per
    # category for its whole lifetime — there is no "also add it to today".
    existing = database.get_word_by_zh(word_zh)
    if existing:
        card_decks = _card_deck_ids(existing["id"])
        saved_deck_id = database.get_or_create_saved_deck()
        if card_decks and card_decks <= {saved_deck_id}:
            # Only staged in Saved — promoting is exactly what the user wants.
            leaf_decks = database.get_or_create_category_decks(deck_id, target_day)
            database.promote_saved_word(existing["id"], leaf_decks, saved_deck_id, target_day)
            return {"status": "promoted", "word_zh": word_zh, "entry_id": existing["id"],
                    "deck_path": deck_path, "deck_id": deck_id}
        # Already being studied somewhere. Moving it here would reset its FSRS
        # state and lose real scheduling progress, so report instead of acting.
        deck_names = sorted(
            (database.get_deck(d) or {}).get("name") or f"deck {d}" for d in card_decks
        )
        return {"status": "already_exists", "word_zh": word_zh, "entry_id": existing["id"],
                "decks": deck_names}

    if ai_disabled():
        raise HTTPException(status_code=503,
                            detail="AI is disabled (offline mode) — cannot generate a new entry")

    job_id = uuid.uuid4().hex[:8]
    with _import_jobs_lock:
        _import_jobs[job_id] = {
            "status": "running",
            "message": f"Generating entry for {word_zh}…",
            "started_at": time.time(),
        }
    _prune_import_jobs()

    def _run():
        try:
            yaml_text = ai.generate_word_entry_yaml(word_zh)
            result = importer.import_yaml_content(yaml_text, deck_id,
                                                  due_offset_days=due_offset_days)
            if result.get("yaml_error"):
                raise ValueError(
                    f"AI returned invalid YAML: {result['yaml_error'].get('problem', 'parse error')}")
            with _import_jobs_lock:
                started_at = _import_jobs[job_id]["started_at"]
                _import_jobs[job_id] = {
                    "status": "done",
                    "message": "Entry added",
                    "summary": {"word_zh": word_zh, "deck_id": deck_id,
                                "deck_path": deck_path, **result},
                    "started_at": started_at,
                }
        except Exception as e:
            logger.exception("add_word_ai failed for %r: %s", word_zh, e)
            with _import_jobs_lock:
                started_at = _import_jobs.get(job_id, {}).get("started_at", time.time())
                _import_jobs[job_id] = {
                    "status": "error",
                    "message": "Failed to add word",
                    "error": str(e),
                    "started_at": started_at,
                }

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "deck_path": deck_path}


@router.get("/api/add-word-ai/progress/{job_id}")
def add_word_ai_progress(job_id: str):
    """Poll status for a background add-word job started by /api/add-word-ai."""
    job = _import_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


# /api/quick-add-word was removed in #643. It only had the AI fill four fields
# (definition/definition_zh/definition_de/pos) — no examples, character
# breakdown, measure words or synonyms — and its "added_to_deck" branch claimed
# success while cards' UNIQUE(word_id, category) silently dropped every insert
# for a word already studied elsewhere. Both callers now use /api/add-word-ai.


@router.post("/api/save-word")
def save_word(body: dict):
    """Stage a compound word in the fixed 'Saved' deck as suspended cards.

    Unlike /api/add-word-ai this does NOT call the AI and does NOT activate
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

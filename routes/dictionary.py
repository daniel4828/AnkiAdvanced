"""In-app AI dictionary API (#746): a single-turn structured lookup that
replaces pasting words into a chat AI and copying the answer by hand. Every
result is stored (database/dictionary.py) so the /dict page can show a
searchable history. Adding a word from a result goes through the existing
POST /api/add-word-ai — this router never touches cards/entries directly
(#643's "one add pipeline" rule).
"""
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import ai
import database
from routes.utils import ai_disabled

logger = logging.getLogger(__name__)
router = APIRouter()


class DictLookupRequest(BaseModel):
    query: str
    lang: str = "zh"
    model: str | None = None
    # Repeat button (#777): ask the same question again and overwrite this
    # stored row instead of adding a second one. Daniel wants only the latest
    # answer in the history — a repeat means "that one was bad", not "keep both".
    replace_id: int | None = None


def _row_to_result(row: dict) -> dict:
    """Shape shared by POST /lookup and GET /history/{id}: the stored row
    plus the parsed JSON blob under "result"."""
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "query": row["query"],
        "lang": row["lang"],
        "result": json.loads(row["result_json"]),
    }


@router.post("/api/dict/lookup")
def lookup(body: DictLookupRequest):
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(400, "query required")
    if ai_disabled():
        raise HTTPException(400, "AI is disabled")
    # A bad language is the caller's mistake, not a model failure — 400, not
    # the 500 the ValueError below would otherwise produce.
    if body.lang not in ("zh", "fr", "es"):
        raise HTTPException(400, f"language {body.lang!r} is not supported yet (zh/fr/es only)")

    # Check the target exists *before* spending 5–15s and a paid AI call on a
    # repeat that could never be stored.
    if body.replace_id is not None and database.get_dict_query(body.replace_id) is None:
        raise HTTPException(404, "not found")

    try:
        result, model_used = ai.dictionary_lookup(query, lang=body.lang, model=body.model)
    except ValueError as e:
        # Nothing is written on a parse failure — for a repeat that also means
        # the previous (good) answer stays untouched: a bad retry must never
        # destroy what it was meant to improve.
        logger.error("dict lookup failed for %r: %s", query, e)
        raise HTTPException(500, str(e))

    fields = dict(
        input_lang=result.get("input_lang"),
        kind=result.get("kind"),
        headline=result.get("headline") or None,
        result_json=json.dumps(result, ensure_ascii=False),
        model=model_used,
    )
    if body.replace_id is not None:
        if not database.update_dict_query(body.replace_id, **fields):
            raise HTTPException(404, "not found")
        row_id = body.replace_id
    else:
        row_id = database.save_dict_query(query=query, lang=body.lang, **fields)
    return _row_to_result(database.get_dict_query(row_id))


@router.get("/api/dict/history")
def history(q: str | None = None, limit: int = 50):
    return {"items": database.list_dict_queries(q=q, limit=limit)}


@router.get("/api/dict/history/{qid}")
def history_item(qid: int):
    row = database.get_dict_query(qid)
    if row is None:
        raise HTTPException(404, "not found")
    return _row_to_result(row)


@router.delete("/api/dict/history/{qid}")
def delete_history_item(qid: int):
    if not database.delete_dict_query(qid):
        raise HTTPException(404, "not found")
    return {"status": "ok"}

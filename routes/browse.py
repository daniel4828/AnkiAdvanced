import database
import importer
from fastapi import APIRouter

router = APIRouter()


@router.post("/api/import")
def trigger_import():
    return importer.import_all("imports")


@router.get("/api/word/{word_id}")
def get_word_detail(word_id: int):
    return database.get_word_full(word_id)


@router.get("/api/browse")
def browse(deck_id: int | None = None, category: str | None = None,
           state: str | None = None, q: str | None = None):
    return database.get_all_cards_for_browse({
        "deck_id": deck_id,
        "category": category,
        "state": state,
        "search_text": q,
    })


@router.get("/api/stats")
def get_stats(deck_id: int | None = None):
    return database.get_stats(deck_id)

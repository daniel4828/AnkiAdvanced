import database
import importer
from fastapi import APIRouter
from pypinyin import pinyin as _pinyin, Style

router = APIRouter()


@router.post("/api/import")
def trigger_import():
    return importer.import_all("imports")


@router.get("/api/word/{word_id}")
def get_word_detail(word_id: int):
    word = database.get_word_full(word_id)
    if word:
        word["cards"] = database.get_cards_for_word(word_id)
    return word


@router.put("/api/word/{word_id}")
def update_word(word_id: int, body: dict):
    database.update_word(word_id, body)
    return database.get_word_full(word_id)


@router.get("/api/browse-words")
def browse_words():
    return database.get_words_for_browse()


@router.get("/api/search-words")
def search_words(q: str):
    return database.search_words(q)


@router.get("/api/words/{word_id}/cards")
def get_word_cards(word_id: int):
    return database.get_cards_for_word(word_id)


@router.post("/api/cards/{card_id}/suspend")
def toggle_suspend(card_id: int):
    return database.suspend_card(card_id)


@router.post("/api/cards/{card_id}/reset")
def reset_card_endpoint(card_id: int):
    return database.reset_card(card_id)


@router.post("/api/cards/{card_id}/bury")
def bury_card_endpoint(card_id: int):
    return database.bury_card_until_tomorrow(card_id)


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


@router.get("/api/costs")
def get_api_costs():
    return database.get_api_costs()


@router.get("/api/pinyin")
def get_pinyin(text: str):
    syllables = [item[0] for item in _pinyin(text, style=Style.TONE)]
    return {"syllables": syllables}

import json as _json

import ai
import database
import importer
from fastapi import APIRouter, HTTPException
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


def _apply_enrich_result(word: dict, characters: list, result: dict) -> None:
    """Apply ai.enrich_word result to a single word and its characters."""
    if result.get("hsk_level") and not word.get("hsk_level"):
        database.update_word(word["id"], {"hsk_level": result["hsk_level"]})
    char_map = {c["char"]: c for c in characters}
    for char_data in result.get("characters", []):
        ch = char_data.get("char")
        if not ch or ch not in char_map:
            continue
        existing = char_map[ch]
        updates = {}
        if char_data.get("etymology") and not existing.get("etymology"):
            updates["etymology"] = char_data["etymology"]
        if char_data.get("other_meanings") and not existing.get("other_meanings"):
            meanings = char_data["other_meanings"]
            if isinstance(meanings, list):
                updates["other_meanings"] = _json.dumps(meanings, ensure_ascii=False)
        if updates:
            database.update_character(existing["char_id"], updates)


@router.post("/api/word/{word_id}/ai-enrich")
def ai_enrich_word(word_id: int):
    word = database.get_word(word_id)
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")

    components = database.get_note_components(word_id)
    if components:
        # Multi-word card (chengyu/sentence/expression): enrich each component separately,
        # because characters are linked to component words, not the top-level note.
        for comp in components:
            comp_chars = database.get_word_characters(comp["id"])
            result = ai.enrich_word(comp, comp_chars)
            _apply_enrich_result(comp, comp_chars, result)
    else:
        characters = database.get_word_characters(word_id)
        result = ai.enrich_word(word, characters)
        _apply_enrich_result(word, characters, result)

    return database.get_word_full(word_id)


@router.get("/api/browse-words")
def browse_words():
    return database.get_words_for_browse()


@router.get("/api/hanzi")
def get_all_hanzi():
    return database.get_all_characters()


@router.get("/api/hanzi/{char_id}")
def get_hanzi(char_id: int):
    char = database.get_character_by_id(char_id)
    if char:
        char["words"] = database.get_words_for_character(char_id)
    return char


@router.post("/api/hanzi/{char_id}/regenerate")
def regenerate_hanzi_info(char_id: int):
    char = database.get_character_by_id(char_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    result = ai.generate_character_info(char["char"], char.get("pinyin") or "")
    updates = {}
    if result["etymology"]:
        updates["etymology"] = result["etymology"]
    if result["translation"]:
        existing = []
        try:
            existing = _json.loads(char.get("other_meanings") or "[]")
        except (ValueError, TypeError):
            existing = []
        merged = [result["translation"]] + [m for m in existing if m != result["translation"]]
        updates["other_meanings"] = _json.dumps(merged, ensure_ascii=False)
    if updates:
        database.update_character(char_id, updates)
    updated = database.get_character_by_id(char_id)
    updated["words"] = database.get_words_for_character(char_id)
    return updated


@router.put("/api/hanzi/{char_id}")
def update_hanzi(char_id: int, body: dict):
    # compounds is handled via character_compounds table, not the JSON column
    compounds_raw = body.pop("compounds", None)
    if body:
        database.update_character(char_id, body)
    if compounds_raw is not None:
        try:
            compounds_list = _json.loads(compounds_raw) if isinstance(compounds_raw, str) else compounds_raw
            if isinstance(compounds_list, list):
                conn = database.get_db()
                conn.execute("DELETE FROM character_compounds WHERE char_id = ?", (char_id,))
                conn.commit()
                conn.close()
                database.upsert_character_compounds(char_id, compounds_list)
        except Exception:
            pass
    char = database.get_character_by_id(char_id)
    char["words"] = database.get_words_for_character(char_id)
    return char


@router.get("/api/search-words")
def search_words(q: str):
    return database.search_words(q)


@router.get("/api/words/{word_id}/cards")
def get_word_cards(word_id: int):
    return database.get_cards_for_word(word_id)


@router.post("/api/cards/{card_id}/suspend")
def toggle_suspend(card_id: int):
    return database.toggle_card_suspension(card_id)


@router.post("/api/cards/{card_id}/reset")
def reset_card_endpoint(card_id: int):
    return database.reset_card(card_id)


@router.post("/api/cards/{card_id}/bury")
def bury_card_endpoint(card_id: int):
    return database.bury_card_until_tomorrow(card_id)


@router.delete("/api/cards/{card_id}")
def delete_card_endpoint(card_id: int):
    card = database.get_card(card_id)
    if card:
        database.delete_word(card["word_id"])
    return {"ok": True}


@router.post("/api/cards/bulk-bury")
def bulk_bury(body: dict):
    count = database.bulk_bury_cards_by_words(body.get("word_ids", []))
    return {"ok": True, "count": count}


@router.post("/api/cards/bulk-suspend")
def bulk_suspend(body: dict):
    count = database.bulk_suspend_cards_by_words(body.get("word_ids", []))
    return {"ok": True, "count": count}


@router.post("/api/cards/bulk-delete")
def bulk_delete(body: dict):
    count = database.bulk_delete_cards_by_words(body.get("word_ids", []))
    return {"ok": True, "count": count}


@router.post("/api/cards/{card_id}/move")
def move_card(card_id: int, body: dict):
    deck_id = body.get("deck_id")
    if not deck_id:
        raise HTTPException(status_code=400, detail="deck_id required")
    ok = database.move_card_to_deck(card_id, deck_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Card not found")
    return {"ok": True}


@router.post("/api/cards/bulk-move")
def bulk_move(body: dict):
    deck_id = body.get("deck_id")
    if not deck_id:
        raise HTTPException(status_code=400, detail="deck_id required")
    count = database.bulk_move_cards_by_words(body.get("word_ids", []), deck_id)
    return {"ok": True, "count": count}


@router.post("/api/entries/{entry_id}/add-to-deck")
def add_entry_to_deck(entry_id: int, body: dict):
    deck_id = body.get("deck_id")
    if not deck_id:
        raise HTTPException(status_code=400, detail="deck_id required")
    result = database.add_entry_to_deck(entry_id, deck_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


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

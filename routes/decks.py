import logging

import database
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten(tree: list) -> list:
    result = []
    for node in tree:
        result.append(node)
        result.extend(_flatten(node.get("children", [])))
    return result


def _leaf_pairs(deck: dict) -> list[tuple[int, str]]:
    """All (id, category) tuples for leaf descendants that have a category."""
    result = []
    for child in deck.get("children", []):
        if not child.get("children"):
            if child.get("category"):
                result.append((child["id"], child["category"]))
        else:
            result.extend(_leaf_pairs(child))
    return result


def _attach_counts(flat_decks: list) -> None:
    """Compute due counts for leaf decks; deduplicated aggregate for parents."""
    for deck in flat_decks:
        if not deck.get("children"):
            cat = deck.get("category")
            deck["counts"] = database.count_due(deck["id"], cat) if cat else {"new": 0, "learning": 0, "review": 0}
            if cat in ("creating", "listening", "reading"):
                deck["all_suspended"] = database.get_category_all_suspended(deck["id"], cat)
    for deck in reversed(flat_decks):
        if deck.get("children"):
            pairs = _leaf_pairs(deck)
            deck["counts"] = database.count_due_deduped(pairs) if pairs else {"new": 0, "learning": 0, "review": 0}
            deck["deck_all_suspended"] = database.get_deck_all_suspended(deck["id"])


# ---------------------------------------------------------------------------
# Deck routes
# ---------------------------------------------------------------------------

@router.get("/api/decks")
def get_decks():
    tree = database.get_deck_tree()
    flat = _flatten(tree)
    _attach_counts(flat)
    # Attach bury_mode so the frontend can render the 3-state toggle without extra calls
    presets = {p["id"]: p for p in database.list_presets()}
    for deck in flat:
        pid = deck.get("preset_id")
        p = presets.get(pid, {})
        deck["bury_mode"] = p.get("bury_quick_mode", "all")
    unfinished = database.count_unfinished()
    if unfinished["learning"] > 0:
        tree.insert(0, {
            "id": "unfinished",
            "name": "Unfinished Cards",
            "virtual": True,
            "counts": unfinished,
            "children": [],
        })
    return tree


@router.post("/api/decks")
def create_deck(name: str, parent_id: int | None = None, category: str | None = None):
    # Support Anki-style 'Parent::Child' hierarchy in name
    if "::" in name:
        deck_id = database.get_or_create_deck_path(name)
        return database.get_deck(deck_id)
    if parent_id is None:
        parent_id = database.get_all_deck_id()
    preset_id = database.get_preset_for_deck(parent_id)["id"]
    deck_id = database.insert_deck(name, parent_id, preset_id, category)
    return database.get_deck(deck_id)


@router.delete("/api/decks/{deck_id}")
def delete_deck(deck_id: int):
    deck = database.get_deck(deck_id)
    if deck and deck.get("name") in ("Sentences", "Sentences · Listening", "Sentences · Reading", "Sentences · Creating"):
        raise HTTPException(status_code=400, detail="Filtered decks cannot be deleted")
    database.delete_deck(deck_id)
    return {"ok": True}


@router.delete("/api/decks/{deck_id}/cards")
def clear_deck_cards(deck_id: int):
    """Soft-delete all cards in a filtered deck (and its children)."""
    count = database.delete_all_deck_cards(deck_id)
    return {"ok": True, "deleted": count}


@router.get("/api/trash")
def get_trash():
    return {"decks": database.get_trash(), "cards": database.get_trashed_cards()}


@router.post("/api/trash/{deck_id}/restore")
def restore_deck(deck_id: int):
    database.restore_deck(deck_id)
    return {"ok": True}


@router.delete("/api/trash/{deck_id}")
def purge_deck(deck_id: int):
    database.purge_deck(deck_id)
    return {"ok": True}


@router.post("/api/decks/{deck_id}/creating/toggle-suspension")
def toggle_creating_suspension(deck_id: int):
    return database.toggle_category_suspension(deck_id, "creating")


@router.post("/api/decks/{deck_id}/categories/{category}/toggle-suspension")
def toggle_category_suspension(deck_id: int, category: str):
    return database.toggle_category_suspension(deck_id, category)


@router.post("/api/decks/{deck_id}/toggle-all-suspension")
def toggle_deck_all_suspension(deck_id: int):
    return database.toggle_deck_all_suspension(deck_id)


@router.post("/api/trash/cards/{card_id}/restore")
def restore_trashed_card(card_id: int):
    database.restore_card(card_id)
    return {"ok": True}


@router.delete("/api/trash/cards/{card_id}")
def purge_trashed_card(card_id: int):
    database.purge_card(card_id)
    return {"ok": True}


@router.delete("/api/trash/{deck_id}/cards")
def purge_all_cards_from_trash_deck(deck_id: int):
    count = database.purge_all_cards_from_deck(deck_id)
    return {"ok": True, "deleted": count}


@router.delete("/api/trash/{deck_id}/cards/{card_id}")
def purge_card_from_trash_deck(deck_id: int, card_id: int):
    database.purge_card_from_deck(card_id)
    return {"ok": True}


@router.delete("/api/trash")
def empty_trash():
    count = database.purge_all_trash()
    return {"ok": True, "deleted": count}


class DeckUpdate(BaseModel):
    name: str | None = None

@router.put("/api/decks/{deck_id}")
def update_deck(deck_id: int, body: DeckUpdate):
    if body.name:
        database.rename_deck(deck_id, body.name)
    return database.get_deck(deck_id)


# ---------------------------------------------------------------------------
# Preset routes
# ---------------------------------------------------------------------------

@router.get("/api/presets")
def list_presets():
    return database.list_presets()


@router.post("/api/presets")
def create_preset(name: str, clone_from_id: int | None = None):
    src = database.get_preset(clone_from_id) if clone_from_id else database.default_preset()
    src["name"] = name
    src.pop("id", None)
    src.pop("is_default", None)
    src.pop("deck_count", None)
    return database.get_preset(database.insert_preset(src))


@router.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: int):
    try:
        database.delete_preset(preset_id)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/api/decks/{deck_id}/preset")
def get_deck_preset(deck_id: int):
    return database.get_preset_for_deck(deck_id)


@router.put("/api/decks/{deck_id}/preset")
def update_deck_preset(deck_id: int, fields: dict):
    deck = database.get_deck(deck_id)
    database.update_preset(deck["preset_id"], fields)
    return database.get_preset(deck["preset_id"])


@router.put("/api/decks/{deck_id}/preset/assign")
def assign_preset_to_deck(deck_id: int, preset_id: int):
    database.assign_preset_to_deck(deck_id, preset_id)
    return database.get_preset(preset_id)


@router.post("/api/decks/{deck_id}/preset/toggle-bury")
def toggle_bury_siblings(deck_id: int):
    deck = database.get_deck(deck_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    preset = database.get_preset(deck["preset_id"])
    current = preset.get("bury_quick_mode", "all")
    # Cycle: all → none → custom → all
    next_mode = {"all": "none", "none": "custom", "custom": "all"}.get(current, "all")
    database.update_preset(preset["id"], {"bury_quick_mode": next_mode})
    return {"bury_mode": next_mode}


@router.post("/api/decks/{deck_id}/preset/set-default")
def set_deck_preset_as_default(deck_id: int):
    deck = database.get_deck(deck_id)
    database.set_default_preset(deck["preset_id"])
    return database.get_preset(deck["preset_id"])

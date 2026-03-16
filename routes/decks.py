import logging

import database
from fastapi import APIRouter, HTTPException

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


def _attach_counts(flat_decks: list) -> None:
    """Compute due counts for leaf decks; aggregate upward for parents."""
    for deck in flat_decks:
        if not deck.get("children"):
            cat = deck.get("category")
            deck["counts"] = database.count_due(deck["id"], cat) if cat else {"new": 0, "learning": 0, "review": 0}
    for deck in reversed(flat_decks):
        children = deck.get("children", [])
        if children:
            agg = {"new": 0, "learning": 0, "review": 0}
            for child in children:
                for k in agg:
                    agg[k] += child.get("counts", {}).get(k, 0)
            deck["counts"] = agg


# ---------------------------------------------------------------------------
# Deck routes
# ---------------------------------------------------------------------------

@router.get("/api/decks")
def get_decks():
    tree = database.get_deck_tree()
    _attach_counts(_flatten(tree))
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
    preset_id = database.get_preset_for_deck(
        database.get_default_deck_id()
    )["id"] if parent_id is None else database.get_deck(parent_id)["preset_id"]
    deck_id = database.insert_deck(name, parent_id, preset_id, category)
    return database.get_deck(deck_id)


@router.put("/api/decks/{deck_id}")
def update_deck(deck_id: int, name: str | None = None):
    if name:
        database.rename_deck(deck_id, name)
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


@router.post("/api/decks/{deck_id}/preset/set-default")
def set_deck_preset_as_default(deck_id: int):
    deck = database.get_deck(deck_id)
    database.set_default_preset(deck["preset_id"])
    return database.get_preset(deck["preset_id"])

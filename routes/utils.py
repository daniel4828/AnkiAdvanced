import os

import database
from .queue_manager import QueueManager

# Set DISABLE_AI=1 in run.dev.sh to skip story generation during development
DISABLE_AI = os.getenv("DISABLE_AI", "").lower() in ("1", "true", "yes")

# Shared singleton — imported by review.py and browse.py
queue_mgr = QueueManager()


def leaf_ids(deck_id: int, category: str) -> list[int]:
    """If deck is a parent (no category), return descendant leaf IDs; else [deck_id]."""
    deck = database.get_deck(deck_id)
    if deck["category"] is None:
        return database.get_descendant_leaf_deck_ids(deck_id, category)
    return [deck_id]

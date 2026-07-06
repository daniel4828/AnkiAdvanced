import os

import database
from .queue_manager import QueueManager

# Set DISABLE_AI=1 in run.dev.sh to skip story generation during development
DISABLE_AI = os.getenv("DISABLE_AI", "").lower() in ("1", "true", "yes")

# Shared singleton — imported by review.py and browse.py
queue_mgr = QueueManager()


def leaf_ids(deck_id: int, category: str, lang: str | None = None) -> list[int]:
    """If deck is a parent (no category), return descendant leaf IDs (optionally lang-filtered); else [deck_id].

    Lang filtering only applies to descendant expansion — a direct category-leaf deck
    is always returned as-is even if its own lang differs from the requested lang.
    """
    deck = database.get_deck(deck_id)
    if deck["category"] is None:
        return database.get_descendant_leaf_deck_ids(deck_id, category, lang=lang)
    return [deck_id]

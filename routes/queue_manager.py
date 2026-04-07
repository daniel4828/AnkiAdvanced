"""Persistent per-session card queues — Anki v3-style two-queue architecture.

Without this module the interleaving computed by _interleave_cards() is
discarded after every card review because get_due_cards() is called fresh each
time.  That means new cards always end up behind all review cards and
new_review_order="mixed" is silently broken.

This module fixes the problem by building the queue ONCE per session day and
keeping it in memory:

  main          — deque of card IDs in pre-interleaved order
                  (interday learning + review + new, mixed at build time)
  intraday_learning — deque of {id, due} for learning/relearn cards that are
                  due today (identified by an ISO datetime with "T" in due).
                  These are checked by timestamp before every card.

After each review:
  - If the card became learning/relearn with a same-day due → pop from main /
    intraday, re-insert into intraday sorted by due.
  - Otherwise → popleft from main, remove from intraday if it was there.

The queue is invalidated (rebuilt on next access) when:
  - The Anki day changes (built_date != anki_today()).
  - An undo operation restores a card to a previous state.
  - The queue is exhausted (main is empty and intraday has nothing due).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime


class SessionQueue:
    def __init__(
        self,
        main_ids: list[int],
        intraday: list[dict],
        built_date: str,
    ) -> None:
        self.main = deque(main_ids)          # card IDs, pre-interleaved
        self.intraday = deque(intraday)      # [{id, due}, ...], sorted by due
        self.built_date = built_date


class QueueManager:
    """Manages per-session queues keyed by (mode, deck/ids, category)."""

    def __init__(self) -> None:
        self._queues: dict[tuple, SessionQueue] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_next(
        self,
        key: tuple,
        build_fn,
        today: str,
        now: str,
    ) -> int | None:
        """Return the ID of the highest-priority card.

        Builds (or rebuilds) the queue if stale.  Checks intraday_learning
        first; if nothing is due right now, returns main[0].
        """
        if key not in self._queues or self._queues[key].built_date != today:
            self._build(key, build_fn, today)

        q = self._queues[key]

        # Intraday learning card due right now?
        due_now = next(
            (e for e in q.intraday if e["due"] <= now),
            None,
        )
        if due_now:
            return due_now["id"]

        # Next card from the persistent main queue
        return q.main[0] if q.main else None

    def after_review(
        self,
        key: tuple,
        card_id: int,
        updated: dict,
        buried_sibling_ids: list[int] | None = None,
    ) -> None:
        """Update the queue after a card has been reviewed.

        - If the card entered learning/relearn with a same-day timestamp,
          remove it from main (if present) and (re)insert into intraday.
        - Otherwise pop it from the front of main (it was just answered) and
          remove it from intraday if it happened to be there.
        - buried_sibling_ids: card IDs that bury_siblings() just buried in the
          DB.  They are removed from both queues so the DB and in-memory state
          stay in sync.
        """
        if key not in self._queues:
            return

        q = self._queues[key]
        new_state = updated.get("state", "")
        new_due = updated.get("due", "")
        becomes_intraday = new_state in ("learning", "relearn") and "T" in new_due

        # Remove from main front if this was the card we just answered
        if q.main and q.main[0] == card_id:
            q.main.popleft()
        else:
            # Card may have been deeper in main (e.g. came from intraday)
            # Remove it to avoid duplicates after re-insertion below
            new_main = deque(cid for cid in q.main if cid != card_id)
            q.main = new_main

        # Remove from intraday
        q.intraday = deque(e for e in q.intraday if e["id"] != card_id)

        # Re-insert into intraday if it became a same-day learning card
        if becomes_intraday:
            entries = list(q.intraday) + [{"id": card_id, "due": new_due}]
            q.intraday = deque(sorted(entries, key=lambda e: e["due"]))

        # Remove siblings that bury_siblings() just buried in the DB so the
        # in-memory queue stays consistent with the database state.
        if buried_sibling_ids:
            remove_set = set(buried_sibling_ids)
            q.main = deque(cid for cid in q.main if cid not in remove_set)
            q.intraday = deque(e for e in q.intraday if e["id"] not in remove_set)

    def invalidate(self, key: tuple | None = None) -> None:
        """Discard one queue (or all queues) so the next access rebuilds."""
        if key is not None:
            self._queues.pop(key, None)
        else:
            self._queues.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build(self, key: tuple, build_fn, today: str) -> None:
        """Call build_fn() and split the result into intraday / main."""
        cards = build_fn()

        def _is_intraday(c: dict) -> bool:
            return c["state"] in ("learning", "relearn") and "T" in c.get("due", "")

        intraday = [
            {"id": c["id"], "due": c["due"]}
            for c in cards
            if _is_intraday(c)
        ]
        main_ids = [c["id"] for c in cards if not _is_intraday(c)]

        self._queues[key] = SessionQueue(main_ids, intraday, today)

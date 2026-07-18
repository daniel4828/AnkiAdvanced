"""
Tests for the in-memory session queues in routes/queue_manager.py.

Pure in-memory tests — no DB is touched.  Focus: cross-queue removal of
buried sibling cards (issue #573): burying is global state, so a card buried
after a review in one category must disappear from ALL cached queues, not
just the queue the review happened in.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from routes.queue_manager import QueueManager

TODAY = "2026-07-17"
NOW = "2026-07-17T10:00:00"

KEY_LISTENING = ("multi", (1,), "listening")
KEY_CREATING = ("multi", (1,), "creating")


def build_fn_for(cards):
    return lambda: [dict(c) for c in cards]


def make_cards(*ids, state="review", due=TODAY):
    return [{"id": i, "state": state, "due": due, "category": "x"} for i in ids]


def fresh_manager():
    """Manager with a listening queue (cards 10, 11, 12) and a creating
    queue (cards 20, 21) already built."""
    mgr = QueueManager()
    mgr.get_next(KEY_LISTENING, build_fn_for(make_cards(10, 11, 12)), TODAY, NOW)
    mgr.get_next(KEY_CREATING, build_fn_for(make_cards(20, 21)), TODAY, NOW)
    return mgr


# ---------------------------------------------------------------------------
# discard_everywhere
# ---------------------------------------------------------------------------

def test_discard_everywhere_removes_from_all_queues():
    mgr = fresh_manager()
    mgr.discard_everywhere([11, 20])
    assert list(mgr._queues[KEY_LISTENING].main) == [10, 12]
    assert list(mgr._queues[KEY_CREATING].main) == [21]


def test_discard_everywhere_removes_from_intraday_and_requeued():
    mgr = QueueManager()
    cards = [
        {"id": 30, "state": "review", "due": TODAY, "category": "x"},
        {"id": 31, "state": "learning", "due": "2026-07-17T11:00:00", "category": "x"},
    ]
    mgr.get_next(KEY_LISTENING, build_fn_for(cards), TODAY, NOW)
    mgr.soft_requeue(KEY_LISTENING, 30, "2026-07-17T12:00:00")
    q = mgr._queues[KEY_LISTENING]
    assert [e["id"] for e in q.intraday] == [31]
    assert [e["id"] for e in q.requeued] == [30]

    mgr.discard_everywhere([30, 31])
    assert list(q.main) == []
    assert list(q.intraday) == []
    assert list(q.requeued) == []


def test_discard_everywhere_empty_is_noop():
    mgr = fresh_manager()
    mgr.discard_everywhere([])
    assert list(mgr._queues[KEY_LISTENING].main) == [10, 11, 12]


# ---------------------------------------------------------------------------
# after_review with buried siblings (issue #573)
# ---------------------------------------------------------------------------

def test_after_review_purges_buried_siblings_from_other_queues():
    """Reviewing card 20 in the creating queue buries sibling 11 — card 11
    must also leave the listening queue that was built earlier."""
    mgr = fresh_manager()
    mgr.after_review(
        KEY_CREATING, 20,
        {"state": "review", "due": "2026-07-25"},
        buried_sibling_ids=[11],
    )
    assert list(mgr._queues[KEY_CREATING].main) == [21]
    assert list(mgr._queues[KEY_LISTENING].main) == [10, 12]


def test_after_review_purges_buried_siblings_even_without_own_queue():
    """The buried-sibling purge must run even when the reviewing context has
    no cached queue (after_review used to return early in that case)."""
    mgr = fresh_manager()
    mgr.after_review(
        ("single", 99, "creating"), 500,
        {"state": "review", "due": "2026-07-25"},
        buried_sibling_ids=[12],
    )
    assert list(mgr._queues[KEY_LISTENING].main) == [10, 11]


def test_after_review_still_pops_reviewed_card():
    mgr = fresh_manager()
    mgr.after_review(KEY_LISTENING, 10, {"state": "review", "due": "2026-07-25"})
    assert list(mgr._queues[KEY_LISTENING].main) == [11, 12]


# ---------------------------------------------------------------------------
# End-to-end regression for issue #573 (through the real HTTP endpoints)
# ---------------------------------------------------------------------------
# Scenario observed in production 2026-07-17 (word 基本上): the listening
# queue was built, then the creating sibling was reviewed — bury_siblings
# marked the listening card buried in the DB, but the already-built listening
# queue still served it 11 minutes later.

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def e2e(tmp_path, monkeypatch):
    """Fresh temp DB + empty queue manager + one imported word.

    Returns (client, deck_id).  Uses the same DB_PATH monkeypatch pattern as
    test_api.py; additionally clears the queue_mgr singleton, which persists
    across tests inside one pytest process.
    """
    import database
    import database.core
    import importer
    import yaml
    import main
    from routes.utils import queue_mgr

    db_file = tmp_path / "test.db"
    # Patch core.DB_PATH — get_db() reads this module global at call time.
    # (Patching the `database` package attribute, as older tests do, silently
    # leaves connections pointing at data/srs.db.)
    monkeypatch.setattr(database.core, "DB_PATH", str(db_file))
    database.init_db()
    queue_mgr.invalidate()

    d = tmp_path / "Kouyu"
    d.mkdir()
    entry = {"type": "vocabulary", "simplified": "你好", "pinyin": "nǐ hǎo",
             "english": "hello", "pos": "intj", "hsk": "1"}
    (d / "words.yaml").write_text(yaml.dump({"entries": [entry]}, allow_unicode=True))
    importer.import_all(str(tmp_path))
    deck_id = database.get_all_deck_id()

    yield TestClient(main.app), deck_id
    queue_mgr.invalidate()


def _card_from(resp):
    assert resp.status_code == 200
    return resp.json()["card"]


def test_buried_sibling_not_served_by_stale_queue(e2e):
    client, deck_id = e2e

    # 1. Build the listening queue — it now contains the listening card.
    listening = _card_from(client.get(f"/api/today/{deck_id}/listening"))
    assert listening is not None and listening["category"] == "listening"

    # 2. Review the creating sibling (Good) → bury_siblings buries the
    #    listening card in the DB.
    creating = _card_from(client.get(f"/api/today/{deck_id}/creating"))
    assert creating is not None and creating["category"] == "creating"
    r = client.post("/api/review", params={"card_id": creating["id"], "rating": 3})
    assert r.status_code == 200

    # 3. The stale listening queue must NOT serve the buried card anymore.
    after = _card_from(client.get(f"/api/today/{deck_id}/listening"))
    assert after is None, (
        f"buried listening card {listening['id']} was still served "
        f"from the stale queue (issue #573)"
    )


def test_undo_brings_unburied_sibling_back(e2e):
    client, deck_id = e2e

    listening = _card_from(client.get(f"/api/today/{deck_id}/listening"))
    creating = _card_from(client.get(f"/api/today/{deck_id}/creating"))
    client.post("/api/review", params={"card_id": creating["id"], "rating": 3})
    assert _card_from(client.get(f"/api/today/{deck_id}/listening")) is None

    # Undo restores the sibling's buried_until and invalidates all queues,
    # so the listening card must be served again.
    r = client.post("/api/review/undo")
    assert r.status_code == 200
    restored = _card_from(client.get(f"/api/today/{deck_id}/listening"))
    assert restored is not None and restored["id"] == listening["id"]

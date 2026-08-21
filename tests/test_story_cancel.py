"""Cancelling an in-flight story generation (#828).

The point of the Cancel button is that it is *not* "Continue in background":
nothing may be written, and the flag must not survive into the next run.
"""

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient

import ai
import database
import importer
import main
from routes import story as story_routes

from tests.test_api import (  # noqa: F401  (fixtures come along by reference)
    ENTRY_你好,
    ENTRY_谢谢,
    fake_generate_story,
    populated_db,
    tmp_db,
    write_yaml,
)

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_generation_state():
    """These are module-level singletons; a leak here poisons other tests."""
    yield
    ai._cancelled_keys.clear()
    ai._story_progress.clear()
    story_routes._generating.clear()


# ---------------------------------------------------------------------------
# The flag itself
# ---------------------------------------------------------------------------

def test_set_progress_raises_once_cancelled():
    key = "1/listening/zh"
    ai._set_progress(key, phase="request", percent=10)   # fine before cancel
    ai.request_cancel(key)
    with pytest.raises(ai.StoryCancelled):
        ai._set_progress(key, phase="translating", percent=88)


def test_clear_cancel_releases_the_key():
    """A leftover flag would kill the next run for this deck the moment it
    starts, which is why clear_cancel() lives in the thread's finally."""
    key = "1/listening/zh"
    ai.request_cancel(key)
    ai.clear_cancel(key)
    ai._set_progress(key, phase="request", percent=10)   # must not raise
    assert not ai.is_cancelled(key)


def test_cancel_does_not_leak_to_other_keys():
    ai.request_cancel("1/listening/zh")
    ai._set_progress("1/reading/zh", phase="request", percent=10)
    ai._set_progress("2/listening/zh", phase="request", percent=10)


# ---------------------------------------------------------------------------
# POST /api/story/{deck_id}/{category}/cancel
# ---------------------------------------------------------------------------

def test_cancel_reports_false_when_nothing_is_running(populated_db):
    deck_id = populated_db
    r = client.post(f"/api/story/{deck_id}/listening/cancel")
    assert r.status_code == 200
    # Reporting a cancel that never happened would be a lie.
    assert r.json() == {"cancelled": False}


def test_cancel_sets_the_flag_for_a_running_generation(populated_db):
    deck_id = populated_db
    lang = database.get_deck_lang(deck_id)
    key = f"{deck_id}/listening/{lang}"
    story_routes._generating.add(key)

    r = client.post(f"/api/story/{deck_id}/listening/cancel")

    assert r.json() == {"cancelled": True}
    assert ai.is_cancelled(key)


def test_cancel_clears_a_sticky_error_state(populated_db):
    """A background run that ended in an error leaves a sticky progress entry so
    polling stops. Walking away from it must not leave that error waiting to be
    re-shown on the next visit."""
    deck_id = populated_db
    lang = database.get_deck_lang(deck_id)
    key = f"{deck_id}/listening/{lang}"
    ai._story_progress[key] = {"phase": "error", "percent": 0, "msg": "boom"}

    client.post(f"/api/story/{deck_id}/listening/cancel")

    assert key not in ai._story_progress


# ---------------------------------------------------------------------------
# Nothing gets written
# ---------------------------------------------------------------------------

def test_cancelled_generation_stores_no_story(populated_db, monkeypatch):
    """The checkpoint right before create_story is the one that matters: every
    step above it is throwaway work, a stored story is not."""
    deck_id = populated_db
    lang = database.get_deck_lang(deck_id)
    key = f"{deck_id}/listening/{lang}"
    today = database.anki_today().isoformat()
    cards = story_routes._get_cards_for_story(deck_id, "listening", lang=lang)
    assert cards, "fixture must have due cards, otherwise this proves nothing"

    monkeypatch.setattr(ai, "generate_story", fake_generate_story)
    # Cancel pressed while the AI call was already in flight.
    ai.request_cancel(key)

    result = story_routes._generate_and_store(
        deck_id, "listening", today, cards,
        topic=None, max_hsk=3, model=None, grammar_focus=None, grammar_pct=75,
        mode="story", chapter_ids=None, progress_key=key, lang=lang)

    assert result == {"cancelled": True}
    assert database.get_active_story(today, "listening", deck_id, lang=lang) is None


def test_uncancelled_generation_still_stores_a_story(populated_db, monkeypatch):
    """Guard against the checkpoint being wired in a way that always fires."""
    deck_id = populated_db
    lang = database.get_deck_lang(deck_id)
    key = f"{deck_id}/listening/{lang}"
    today = database.anki_today().isoformat()
    cards = story_routes._get_cards_for_story(deck_id, "listening", lang=lang)

    monkeypatch.setattr(ai, "generate_story", fake_generate_story)
    result = story_routes._generate_and_store(
        deck_id, "listening", today, cards,
        topic=None, max_hsk=3, model=None, grammar_focus=None, grammar_pct=75,
        mode="story", chapter_ids=None, progress_key=key, lang=lang)

    assert not result.get("cancelled")
    assert database.get_active_story(today, "listening", deck_id, lang=lang) is not None

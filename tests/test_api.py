"""
Tests for the FastAPI endpoints added/wired in M2.

Strategy:
  - Use FastAPI's TestClient to make real HTTP calls to the app in-process.
  - Patch ai.generate_story and tts.speak so tests run instantly offline
    and never make real API calls or play audio.
  - Use the same tmp_db fixture pattern as test_importer.py so each test
    gets a clean, isolated SQLite database.
"""

import pytest
from unittest.mock import patch, call
from datetime import date

# If fastapi is not installed the whole file is skipped cleanly.
pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
import database
import importer
import main

client = TestClient(main.app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_yaml(tmp_path, name, entries):
    """Write entries into tmp_path/Kouyu/<name> so import_all can find them."""
    import yaml
    d = tmp_path / "Kouyu"
    d.mkdir(exist_ok=True)
    (d / name).write_text(yaml.dump({"entries": entries}, allow_unicode=True))


# Entry keys match what importer.py reads: "simplified" and "english"
ENTRY_你好 = {"type": "vocabulary", "simplified": "你好", "pinyin": "nǐ hǎo",
               "english": "hello",      "pos": "intj", "hsk": "1"}
ENTRY_谢谢 = {"type": "vocabulary", "simplified": "谢谢", "pinyin": "xiè xie",
               "english": "thank you", "pos": "v",    "hsk": "1"}


def fake_generate_story(cards):
    """
    Drop-in replacement for ai.generate_story used in tests.
    Returns one sentence per card with the correct word_id so the
    endpoint can save them to the DB and return them to the client.
    """
    return [
        {
            "word_id": c["word_id"],
            "sentence_zh": f"他说{c['word_zh']}。",
            "sentence_en": f"He said {c['definition']}.",
        }
        for c in cards
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Redirect all DB access to a fresh temp file for this test.
    Same pattern as test_importer.py — keeps tests fully isolated.
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    database.init_db()
    return db_file


@pytest.fixture
def populated_db(tmp_db, tmp_path):
    """tmp_db with 2 words already imported. Returns the Kouyu deck_id."""
    write_yaml(tmp_path, "words.yaml", [ENTRY_你好, ENTRY_谢谢])
    importer.import_all(str(tmp_path))
    return database.get_or_create_deck("Kouyu")


# ---------------------------------------------------------------------------
# GET /api/story/{deck_id}/{category}
# ---------------------------------------------------------------------------

class TestGetStory:

    def test_generates_story_when_none_exists_today(self, populated_db):
        """
        First request of the day: no story in DB yet.
        The endpoint must call generate_story, save the result, and return
        the story with its sentences.
        """
        deck_id = populated_db
        with patch("ai.generate_story", side_effect=fake_generate_story) as mock_gen:
            r = client.get(f"/api/story/{deck_id}/listening")

        assert r.status_code == 200
        mock_gen.assert_called_once()          # AI called exactly once
        body = r.json()
        assert body is not None
        assert "sentences" in body
        assert len(body["sentences"]) == 2     # one sentence per due card

    def test_reuses_existing_story_without_calling_ai_again(self, populated_db):
        """
        Second request for the same day/deck/category must return the
        cached story and NOT call generate_story again.
        Calling the AI twice would waste tokens and change the story
        mid-session.
        """
        deck_id = populated_db
        with patch("ai.generate_story", side_effect=fake_generate_story) as mock_gen:
            client.get(f"/api/story/{deck_id}/listening")  # first call
            client.get(f"/api/story/{deck_id}/listening")  # second call

        assert mock_gen.call_count == 1, (
            "AI should only be called once — second GET must reuse cached story"
        )

    def test_returns_null_when_no_due_cards(self, tmp_db):
        """
        Empty deck → no due cards → no story to generate.
        Endpoint must return null (not crash, not call AI).
        The frontend handles null by showing 'all done'.
        """
        deck_id = database.get_default_deck_id()
        with patch("ai.generate_story") as mock_gen:
            r = client.get(f"/api/story/{deck_id}/listening")

        mock_gen.assert_not_called()
        assert r.json() is None

    def test_story_sentences_are_saved_to_db(self, populated_db):
        """
        After generation the sentences must be persisted in the DB so that
        subsequent requests (and page reloads) return the same sentences
        without re-calling AI.
        """
        deck_id = populated_db
        today = date.today().isoformat()

        with patch("ai.generate_story", side_effect=fake_generate_story):
            client.get(f"/api/story/{deck_id}/listening")

        story = database.get_active_story(today, "listening", deck_id)
        assert story is not None
        sentences = database.get_story_sentences(story["id"])
        assert len(sentences) == 2
        for s in sentences:
            assert s["sentence_zh"]
            assert s["sentence_en"]


# ---------------------------------------------------------------------------
# POST /api/story/{deck_id}/{category}/regenerate
# ---------------------------------------------------------------------------

class TestRegenerateStory:

    def test_regenerate_creates_new_db_row(self, populated_db):
        """
        Every call to /regenerate must insert a NEW story row, even if a
        story already exists for today. The new row has a later generated_at
        so it becomes the active story. Old stories are kept forever.
        """
        deck_id = populated_db
        today = date.today().isoformat()

        with patch("ai.generate_story", side_effect=fake_generate_story):
            client.get(f"/api/story/{deck_id}/listening")           # story 1
            client.post(f"/api/story/{deck_id}/listening/regenerate")  # story 2

        conn = database.get_db()
        count = conn.execute(
            "SELECT COUNT(*) FROM stories WHERE date=? AND category='listening' AND deck_id=?",
            (today, deck_id),
        ).fetchone()[0]
        conn.close()
        assert count == 2, "Each regenerate must insert a new story row, not overwrite"

    def test_regenerate_calls_ai_again(self, populated_db):
        """
        Regenerate must call generate_story even when a story already
        exists — that's the whole point of regeneration.
        """
        deck_id = populated_db
        with patch("ai.generate_story", side_effect=fake_generate_story) as mock_gen:
            client.get(f"/api/story/{deck_id}/listening")
            client.post(f"/api/story/{deck_id}/listening/regenerate")

        assert mock_gen.call_count == 2

    def test_regenerate_returns_new_story_with_sentences(self, populated_db):
        """
        The regenerate response must include the full story object with
        sentences so the frontend can display it immediately without a
        follow-up GET.
        """
        deck_id = populated_db
        with patch("ai.generate_story", side_effect=fake_generate_story):
            r = client.post(f"/api/story/{deck_id}/listening/regenerate")

        assert r.status_code == 200
        body = r.json()
        assert body is not None
        assert "sentences" in body
        assert len(body["sentences"]) == 2


# ---------------------------------------------------------------------------
# POST /api/speak
# ---------------------------------------------------------------------------

class TestSpeak:

    def test_speak_calls_tts_with_correct_text(self, tmp_db):
        """
        POST /api/speak?text=你好 must pass exactly "你好" to tts.speak.
        The endpoint is a thin wrapper — we just verify the routing.
        """
        with patch("tts.speak") as mock_tts:
            r = client.post("/api/speak", params={"text": "你好"})

        assert r.status_code == 200
        mock_tts.assert_called_once_with("你好")

    def test_speak_returns_ok(self, tmp_db):
        """Response must be {"ok": true} so the frontend can detect success."""
        with patch("tts.speak"):
            r = client.post("/api/speak", params={"text": "你好"})

        assert r.json() == {"ok": True}

    def test_speak_does_not_crash_if_tts_fails(self, tmp_db):
        """
        TTS depends on edge-tts and afplay being available (macOS + internet).
        If TTS throws for any reason, the endpoint must still return 200 —
        speaking is best-effort and must never break the review session.
        """
        with patch("tts.speak", side_effect=Exception("afplay not found")):
            r = client.post("/api/speak", params={"text": "你好"})

        assert r.status_code == 200

"""Tests for issue #815: Browse is language-scoped, and rows can be deleted.

Before this, /api/browse-words and /api/search-words had no lang parameter at
all, so French words (boire, dossier, ...) were listed and pinyin-sorted right
next to Chinese ones under the Chinese tab.

The filter deliberately keys off entries.lang, not decks.lang: Browse also
lists reference entries that have no card in any deck, and a deck-based filter
would silently drop every one of them.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database
import main


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    # database.core.DB_PATH, never database.DB_PATH — the latter is only a
    # wildcard-import copy and patching it writes to the real DB (#615).
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()


def _entry(word: str, lang: str, definition: str = "") -> int:
    return database.insert_word({
        "word_zh": word, "lang": lang, "pinyin": None, "definition": definition,
        "pos": None, "hsk_level": None, "traditional": None, "definition_zh": None,
        "source": "test", "note_type": "vocabulary", "notes": None, "date_yaml": None,
        "source_sentence": None, "grammar_notes": None, "register": None,
        "definition_de": None, "definition_fr": None,
    })


@pytest.fixture
def client(tmp_db):
    _entry("生态", "zh", "ecology")
    _entry("boire", "fr", "to drink")
    _entry("dossier", "fr", "file")
    return TestClient(main.app)


def test_browse_words_filters_by_lang(client):
    zh = client.get("/api/browse-words?lang=zh").json()
    assert {w["word_zh"] for w in zh} == {"生态"}

    fr = client.get("/api/browse-words?lang=fr").json()
    assert {w["word_zh"] for w in fr} == {"boire", "dossier"}


def test_browse_words_without_lang_returns_everything(client):
    """Back-compat: callers that predate the language tabs see no change."""
    words = client.get("/api/browse-words").json()
    assert {w["word_zh"] for w in words} == {"生态", "boire", "dossier"}


def test_browse_lists_cardless_reference_entries(client):
    """The lang filter must not turn into a deck filter: an entry with no card
    at all still belongs to Browse."""
    fr = client.get("/api/browse-words?lang=fr").json()
    assert all(w["cards"] == [] for w in fr)
    assert len(fr) == 2


def test_search_filters_by_lang(client):
    """"o" matches all three entries (the Chinese one through its English
    definition "ecology"), so it isolates the lang filter itself."""
    words = {w["id"]: w["word_zh"] for w in client.get("/api/browse-words").json()}

    def _hits(lang):
        r = client.get(f"/api/search-words?q=o&lang={lang}").json()
        return {words[i] for i in set(r["primary"]) | set(r["secondary"])}

    assert _hits("fr") == {"boire", "dossier"}
    assert _hits("zh") == {"生态"}


def test_delete_word_removes_entry_and_cards(tmp_db):
    word_id = _entry("dossier", "fr", "file")
    deck_id = database.get_or_create_deck("Français", lang="fr")
    database.insert_card(word_id, "listening", deck_id)
    assert database.get_cards_for_word(word_id)

    client = TestClient(main.app)
    resp = client.delete(f"/api/word/{word_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == word_id

    assert database.get_word(word_id) is None
    assert database.get_cards_for_word(word_id) == []


def test_delete_missing_word_is_404(tmp_db):
    """A no-op delete must not report success (nothing was deleted)."""
    client = TestClient(main.app)
    assert client.delete("/api/word/999999").status_code == 404

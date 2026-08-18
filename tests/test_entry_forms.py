"""Tests for issue #803: language families, entry_forms morphology storage,
and per-language uniqueness for entries/known_words.

Each test gets its own isolated temp DB by monkeypatching database.core.DB_PATH
before calling any database function (same pattern as tests/test_importer.py).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point database.core.DB_PATH at a temp file and initialise the schema.

    It has to be core's global: database.DB_PATH is only a copy of the name
    made by `from .core import *`, so patching it leaves get_db() connecting
    to the real data/srs.db (issue #615).
    """
    db_file = str(tmp_path / "test_srs.db")
    monkeypatch.setattr(database.core, "DB_PATH", db_file)
    database.init_db()
    return db_file


def _minimal_word(word_zh: str, lang: str = "zh") -> dict:
    return {
        "word_zh": word_zh,
        "lang": lang,
        "pinyin": None,
        "definition": None,
        "pos": None,
        "hsk_level": None,
        "traditional": None,
        "definition_zh": None,
        "source": "test",
        "note_type": "vocabulary",
        "notes": None,
        "date_yaml": None,
        "source_sentence": None,
        "grammar_notes": None,
        "register": None,
        "definition_de": None,
        "definition_fr": None,
    }


# ---------------------------------------------------------------------------
# languages.py — family inheritance
# ---------------------------------------------------------------------------

def test_language_family_inheritance():
    import languages

    zh = languages.get_lang_config("zh")
    fr = languages.get_lang_config("fr")
    es = languages.get_lang_config("es")

    assert zh["family"] == "sinitic"
    assert fr["family"] == "romance"
    assert es["family"] == "romance"

    # Romance languages inherit morphology feature flags from the base, zh does not.
    assert fr["features"]["conjugation"] is True
    assert fr["features"]["gender"] is True
    assert es["features"]["conjugation"] is True
    assert zh["features"]["conjugation"] is False

    # es is a real, fully-configured language now (issue #803).
    assert languages.is_valid_lang("es")
    assert es["deck_root"] == "Español"
    assert es["level_system"] == "CEFR"


# ---------------------------------------------------------------------------
# entry_conjugations -> entry_forms migration (runs once)
# ---------------------------------------------------------------------------

def test_conjugation_migration_runs_once(tmp_db):
    word_id = database.insert_word(_minimal_word("parler", lang="fr"))

    conn = database.core.get_db()
    # Simulate a pre-#803 database: data sitting only in the legacy table,
    # and the one-shot marker not yet set (tmp_db's initial init_db() call
    # already set it, since the marker guards "has this ever run", not "was
    # there data to migrate").
    conn.execute("DELETE FROM app_settings WHERE key = 'migrated_entry_conjugations'")
    conn.execute(
        """INSERT INTO entry_conjugations (word_id, tense, person, form, position)
           VALUES (?, 'présent', 'je', 'parle', 0)""",
        (word_id,),
    )
    conn.commit()
    conn.close()

    # Re-running init_db() should now migrate the row into entry_forms.
    database.init_db()

    forms = database.get_word_conjugations(word_id)
    assert len(forms) == 1
    assert forms[0]["tense"] == "présent"
    assert forms[0]["person"] == "je"
    assert forms[0]["form"] == "parle"

    # Manually remove the migrated row, then run init_db() again — because
    # the marker is now set, the migration must NOT re-run and restore it.
    conn = database.core.get_db()
    conn.execute("DELETE FROM entry_forms WHERE word_id = ?", (word_id,))
    conn.commit()
    conn.close()

    database.init_db()

    assert database.get_word_conjugations(word_id) == []


# ---------------------------------------------------------------------------
# forms_lookup — language-scoped morphology lookup
# ---------------------------------------------------------------------------

def test_forms_lookup_matches_conjugated_form_within_language_only(tmp_db):
    fr_word_id = database.insert_word(_minimal_word("parler", lang="fr"))
    database.set_entry_forms(fr_word_id, [
        {"kind": "conjugation", "paradigm": "présent", "slot": "nous", "form": "parlons", "position": 0},
    ])

    # An unrelated Spanish word — no relation to "parlons" at all.
    database.insert_word(_minimal_word("hablar", lang="es"))

    # Matches under fr (the language that actually has this conjugated form).
    assert database.forms_lookup(["parlons"], "fr") == {"parlons"}

    # Does NOT match under es — same surface form, wrong language.
    assert database.forms_lookup(["parlons"], "es") == set()

    # A word not present anywhere doesn't match either.
    assert database.forms_lookup(["xyzzy"], "fr") == set()


def test_forms_lookup_also_matches_dictionary_headword(tmp_db):
    fr_word_id = database.insert_word(_minimal_word("manger", lang="fr"))
    database.set_entry_forms(fr_word_id, [
        {"kind": "conjugation", "paradigm": "présent", "slot": "je", "form": "mange", "position": 0},
    ])

    # The headword itself counts as "known" even though it's not a stored
    # conjugated form.
    assert database.forms_lookup(["manger"], "fr") == {"manger"}


# ---------------------------------------------------------------------------
# entries.UNIQUE(word_zh, lang) — same headword, different languages
# ---------------------------------------------------------------------------

def test_entries_unique_per_word_and_lang_not_globally(tmp_db):
    fr_id = database.insert_word(_minimal_word("capital", lang="fr"))
    es_id = database.insert_word(_minimal_word("capital", lang="es"))

    assert fr_id != es_id

    conn = database.core.get_db()
    rows = conn.execute(
        "SELECT id, lang FROM entries WHERE word_zh = 'capital' ORDER BY lang"
    ).fetchall()
    conn.close()

    assert {r["lang"] for r in rows} == {"es", "fr"}
    assert len(rows) == 2


def test_known_words_scoped_per_language(tmp_db):
    database.add_known_word("capital", lang="fr")

    assert database.known_words_exists(["capital"], lang="fr") == {"capital"}
    # Same surface form, different language — must not leak across languages.
    assert database.known_words_exists(["capital"], lang="es") == set()

    # Default lang stays 'zh' so existing Chinese call sites (no lang kwarg)
    # keep working exactly as before #803.
    database.add_known_word("谢谢")
    assert database.known_words_exists(["谢谢"]) == {"谢谢"}

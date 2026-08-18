"""Tests for issue #805: French/Spanish add-word full morphology (conjugations
+ inflected forms + gender), and the AI dictionary's fr/es support.

Isolation follows tests/test_entry_forms.py / tests/test_importer_fr.py:
monkeypatch database.core.DB_PATH directly (the package-level DB_PATH is only
a name copy — issue #615). The AI is stubbed at ai._call_api, never at a
provider client (issue #615's other half).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
from unittest.mock import patch

import ai
import database
import importer
import main
import routes.dictionary

client = TestClient(main.app)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_srs.db")
    monkeypatch.setattr(database.core, "DB_PATH", db_file)
    database.init_db()
    monkeypatch.setattr(routes.dictionary, "ai_disabled", lambda: False)
    return db_file


# ---------------------------------------------------------------------------
# Importer: Spanish entries — gender + forms (inflection) + conjugations
# ---------------------------------------------------------------------------

ENTRY_YAML_ES_NOUN = """lang: es
entries:
  - type: word
    date: "08/18"
    word: el gato
    pos: sustantivo (m)
    english: cat
    german: die Katze
    level: "A1"
    register: neutral
    gender: m
    note: |
      Männliches Substantiv.
    examples:
      - es: El gato duerme.
        english: The cat is sleeping.
        german: Die Katze schläft.
    forms:
      numero:
        plural: gatos
"""

ENTRY_YAML_ES_ADJ = """lang: es
entries:
  - type: word
    date: "08/18"
    word: verde
    pos: adjetivo
    english: green
    german: grün
    level: "A1"
    register: neutral
    note: |
      Regelmäßiges Adjektiv.
    examples:
      - es: El jersey verde es mío.
        english: The green sweater is mine.
        german: Der grüne Pullover gehört mir.
    forms:
      numero:
        plural: verdes
      genero:
        femenino: verde
"""

ENTRY_YAML_ES_VERB = """lang: es
entries:
  - type: word
    date: "08/18"
    word: hablar
    pos: verbo
    english: to speak
    german: sprechen
    level: "A1"
    register: neutral
    note: |
      Regelmäßiges Verb auf -ar.
    examples:
      - es: Hablo español.
        english: I speak Spanish.
        german: Ich spreche Spanisch.
    conjugations:
      presente:
        yo: hablo
        tú: hablas
      participio: hablado
"""


def _import(yaml_text):
    deck_id = database.get_or_create_deck_path("Español::Test")
    return importer.import_yaml_content(yaml_text, deck_id)


def test_spanish_noun_imports_gender_and_plural_inflection(tmp_db):
    result = _import(ENTRY_YAML_ES_NOUN)
    assert result["imported"] == 1, result

    entry = database.get_word_by_zh("el gato")
    assert entry is not None
    assert entry["lang"] == "es"
    assert entry["gender"] == "m"

    full = database.get_word_full(entry["id"])
    assert full["inflections"] == [
        {"paradigm": "numero", "slot": "plural", "form": "gatos", "position": 0}
    ]
    # forms_lookup must find the inflected form for known-word annotation (#803).
    assert database.forms_lookup(["gatos"], "es") == {"gatos"}


def test_spanish_adjective_imports_multiple_inflection_dimensions(tmp_db):
    result = _import(ENTRY_YAML_ES_ADJ)
    assert result["imported"] == 1, result

    entry = database.get_word_by_zh("verde")
    assert entry["gender"] is None  # adjectives don't carry entries.gender
    full = database.get_word_full(entry["id"])
    paradigms = {(f["paradigm"], f["slot"]): f["form"] for f in full["inflections"]}
    assert paradigms[("numero", "plural")] == "verdes"
    assert paradigms[("genero", "femenino")] == "verde"


def test_spanish_verb_conjugations_still_work_via_shared_pipeline(tmp_db):
    result = _import(ENTRY_YAML_ES_VERB)
    assert result["imported"] == 1, result

    entry = database.get_word_by_zh("hablar")
    conj = database.get_word_conjugations(entry["id"])
    by_tense_person = {(c["tense"], c["person"]): c["form"] for c in conj}
    assert by_tense_person[("presente", "yo")] == "hablo"
    assert by_tense_person[("presente", "tú")] == "hablas"
    assert by_tense_person[("participio", "")] == "hablado"


def test_reimporting_es_entry_is_idempotent(tmp_db):
    first = _import(ENTRY_YAML_ES_NOUN)
    assert first["imported"] == 1
    second = _import(ENTRY_YAML_ES_NOUN)
    assert second["imported"] == 0
    assert second["skipped_duplicate"] == 1


# ---------------------------------------------------------------------------
# Importer: invalid gender value falls back to None rather than raising
# ---------------------------------------------------------------------------

def test_invalid_gender_value_is_dropped_not_stored(tmp_db):
    bad = ENTRY_YAML_ES_NOUN.replace("gender: m", "gender: neuter")
    _import(bad)
    entry = database.get_word_by_zh("el gato")
    assert entry["gender"] is None


# ---------------------------------------------------------------------------
# ai.generate_word_entry_yaml routes to the Spanish prompt
# ---------------------------------------------------------------------------

def test_spanish_prompt_is_used_for_es():
    with patch.object(ai, "_call_api", return_value=ENTRY_YAML_ES_NOUN.split("entries:\n")[1]) as call:
        ai.generate_word_entry_yaml("el gato", lang="es")
    prompt = call.call_args[0][1][0]["content"]
    assert "Spanish dictionary expert" in prompt
    assert "gender" in prompt and "forms" in prompt


def test_french_prompt_now_requires_gender_and_forms():
    """#805 extends the existing French prompt to also require gender/forms
    for nouns and adjectives, not just conjugations for verbs."""
    with patch.object(ai, "_call_api", return_value="- type: word\n  word: x\n") as call:
        ai.generate_word_entry_yaml("chat", lang="fr")
    prompt = call.call_args[0][1][0]["content"]
    assert "gender" in prompt and "forms" in prompt


# ---------------------------------------------------------------------------
# AI dictionary: fr/es lookups
# ---------------------------------------------------------------------------

ROMANCE_DICT_RESULT = {
    "input_lang": "de",
    "kind": "phrase",
    "headline": "confier une tâche",
    "headline_de": "jemandem eine Aufgabe geben",
    "groups": [
        {
            "label": "assigner (Verb)",
            "options": [
                {
                    "key": "a",
                    "zh": "confier une tâche",
                    "de": "jdm. eine Aufgabe anvertrauen",
                    "usage": "neutral, im Alltag üblich.",
                    "register": "spoken_neutral",
                    "recommended": True,
                    "example_zh": "Le prof m'a confié une tâche.",
                    "example_de": "Der Lehrer hat mir eine Aufgabe gegeben.",
                }
            ],
        }
    ],
}


def test_dictionary_lookup_uses_romance_prompt_for_fr():
    with patch.object(ai, "_call_api", return_value=json.dumps(ROMANCE_DICT_RESULT)) as call:
        result, model = ai.dictionary_lookup("eine Aufgabe geben", lang="fr")
    assert result["headline"] == "confier une tâche"
    prompt = call.call_args[0][1][0]["content"]
    assert "French dictionary" in prompt


def test_dictionary_lookup_uses_romance_prompt_for_es():
    with patch.object(ai, "_call_api", return_value=json.dumps(ROMANCE_DICT_RESULT)):
        result, model = ai.dictionary_lookup("eine Aufgabe geben", lang="es")
    assert result["headline"] == "confier une tâche"


def test_dictionary_lookup_rejects_unsupported_lang():
    with pytest.raises(ValueError):
        ai.dictionary_lookup("test", lang="de")


def test_dict_lookup_api_accepts_fr(tmp_db):
    with patch.object(ai, "_call_api", return_value=json.dumps(ROMANCE_DICT_RESULT)):
        r = client.post("/api/dict/lookup", json={"query": "eine Aufgabe geben", "lang": "fr"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lang"] == "fr"
    assert body["result"]["headline"] == "confier une tâche"


def test_dict_lookup_api_accepts_es(tmp_db):
    with patch.object(ai, "_call_api", return_value=json.dumps(ROMANCE_DICT_RESULT)):
        r = client.post("/api/dict/lookup", json={"query": "eine Aufgabe geben", "lang": "es"})
    assert r.status_code == 200, r.text
    assert r.json()["lang"] == "es"


def test_dict_lookup_api_rejects_unsupported_lang(tmp_db):
    r = client.post("/api/dict/lookup", json={"query": "test", "lang": "de"})
    assert r.status_code == 400


def test_dict_page_served_with_lang_picker_markup():
    r = client.get("/dict")
    assert r.status_code == 200
    assert 'id="langs"' in r.text


def test_langs_endpoint_available_lists_registered_languages(tmp_db):
    """/api/langs?available=1 must list every registered language (#805).

    The home tab bar wants "languages in use" — a tab for a language with no
    cards would be noise. But /add and /dict are where a language *starts*:
    a brand-new language has no decks yet, so filtering by usage there would
    make its very first word impossible to add.
    """
    from fastapi.testclient import TestClient
    import main
    import languages as languages_mod

    client = TestClient(main.app)

    in_use = client.get("/api/langs").json()
    assert "es" not in in_use  # no Spanish decks in a fresh DB

    available = client.get("/api/langs?available=1").json()
    assert set(available) == set(languages_mod.LANGUAGES)
    assert available[0] == "zh"  # default language stays first

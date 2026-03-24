# Step 03 — Importer: Multi-Type YAML + Preview

**Branch:** `feat/importer`
**Depends on:** Step 01 (DB migrations — `note_type`, `note_components`, `word_has_cards`)
**Blocks:** Step 04 (Import API)

---

## What to Read First

1. `importer.py` — full file (understand current import logic)
2. `database.py` — especially `insert_word`, `insert_card`, `word_has_cards`, `insert_note_component`
3. `recovery/2026-03-23_import-note-types.md` — note type handling
4. `recovery/2026-03-24_database-import-functions.md` — DB functions used by importer
5. `recovery/going_manually_through_chats.md` lines 200–400 — import conflict resolution details

---

## Goal

Extend `importer.py` to:
1. Support multiple note types: `vocabulary`, `sentence`, `chengyu`, `expression`
2. Route all sentences to the hardcoded "Sentences" deck
3. Handle component words (`word_analyses`) and link them via `note_components`
4. Detect duplicates properly (word with existing cards = duplicate)
5. Add a `preview_yaml_content()` function that does a dry-run with no DB writes

---

## Note Type Support

### YAML entry structure

Top-level key can be `entries:`, `vocab:`, or `vocabulary:`.

Each entry has a `type:` field (or defaults to `vocabulary`):
- `vocabulary` — standard word card
- `sentence` — example sentence, routes to Sentences deck
- `chengyu` — 4-character idiom
- `expression` — set phrase/collocation

### Fields mapping

| YAML field | DB column | Notes |
|-----------|-----------|-------|
| `type` | `words.note_type` | default: vocabulary |
| `simplified` | `words.word_zh` | required |
| `pinyin` | `words.pinyin` | |
| `english` | `words.definition` | |
| `hsk` | `words.hsk_level` | int |
| `traditional` | `words.traditional` | |
| `pos` | `words.pos` | |
| `definition_zh` | `words.definition_zh` | |
| `source_de` | `words.notes` | source sentence in German |
| `grammar_de` | `words.grammar_notes` | grammar explanation |
| `note` | `words.notes` | general notes |
| `literal` | `words.notes` | for chengyu |
| `origin` | part of notes | for chengyu |
| `examples` | `word_examples` table | list of {zh, pinyin, en} dicts |
| `characters` | `word_characters` + `characters` tables | existing logic |
| `word_analyses` | `note_components` table | component words |

---

## Sentence Deck Routing

```python
# At import time, for note_type == 'sentence':
deck_ids = database.get_sentences_deck_ids()
# use deck_ids['listening'], deck_ids['reading'], deck_ids['creating']
# as the deck_id for card creation
```

All other types use the target deck passed to the import function.

---

## Duplicate Detection

A word is a **duplicate** if:
- A word with the same `word_zh` already exists AND
- `database.word_has_cards(word_id)` returns True

If the word exists but has NO cards → not a duplicate (proceed normally, just add cards).

---

## Component Words (`word_analyses`)

Each entry can have a `word_analyses:` list. Each item is either:
- A `char_only` entry: `{char_only: 你}` — minimal word record (no cards)
- A full typed entry: `{type: vocabulary, simplified: 你好, ...}`

Processing:
1. For each component, check if word already exists in DB
2. If not → insert as word (with minimal fields for `char_only`)
3. **Never** create cards for component words
4. Link via `database.insert_note_component(parent_word_id, component_word_id, position)`

---

## Conflict Detection

When importing a word that already exists (same `word_zh`), check for field conflicts:
- `pinyin` differs
- `definition` differs
- `traditional` differs

If any differ → mark as `conflict` in preview. User can choose:
- `keep` — keep existing DB values (default)
- `update` — overwrite with incoming YAML values

---

## `preview_yaml_content(content: str) -> dict`

New function — no DB writes whatsoever.

```python
def preview_yaml_content(content: str) -> dict:
    """
    Dry-run parse of YAML content.
    Returns:
    {
        entries: [
            {
                simplified: str,
                note_type: str,
                status: 'ok' | 'duplicate' | 'invalid' | 'unknown_type',
                reason: str | None,
                raw_yaml: str,  # the raw YAML text for this entry
            }
        ],
        summary: {ok: int, duplicate: int, invalid: int, unknown_type: int},
        conflicts: [
            {
                word_zh: str,
                existing: {pinyin, definition, traditional},
                incoming: {pinyin, definition, traditional},
            }
        ]
    }
    """
```

This function reads from DB (to check duplicates) but never writes.

---

## Main Import Function Signature

Update the existing import function to accept:

```python
def import_yaml_content(
    content: str,
    deck_id: int | None = None,
    deck_path: str | None = None,
    deck_name: str | None = None,
    resolutions: dict | None = None,  # {word_zh: 'keep'|'update'}
) -> dict:
    """
    Returns: {
        deck_id: int,
        imported: int,
        skipped_duplicate: int,
        skipped_invalid: int,
        skipped_entries: list[str],  # word_zh of skipped entries
    }
    """
```

Deck resolution priority: `deck_id` > `deck_path` (Anki-style `::` path) > `deck_name`.
If none given, use a default deck.

---

## Creating category: start suspended

When creating cards for any note type, the `creating` category card should be created
with `state='suspended'` (or equivalent). Check existing card creation code for the pattern.

---

## How to Implement

1. `git checkout -b feat/importer` (after Step 01 is merged)
2. Edit `importer.py`:
   - Add `preview_yaml_content()` function
   - Update main import function to handle note types, sentence routing, components, conflicts
   - Keep backward compatibility with existing CLI `python main.py import`
3. Test: `python main.py import` — existing YAML files should still import correctly
4. Test: preview a YAML file via the new function in a Python shell
5. Commit and open PR

---

## Verification Checklist

- [ ] `python main.py import` still works for existing YAML files
- [ ] Sentence-type entries get routed to Sentences deck
- [ ] Component words (`word_analyses`) are linked but have no cards
- [ ] `preview_yaml_content()` returns correct status for known/unknown words
- [ ] Conflict detection works when `pinyin` differs from existing
- [ ] `resolutions={'word_zh': 'update'}` overwrites existing fields

---

## When you are done

1. Mark the step as 🔄 IN PROGRESS in `plan/PLAN.md` when you start (update the status column)
2. Open a PR with `gh pr create --fill` referencing `Closes #<issue>`
3. Mark as 👀 REVIEW in `plan/PLAN.md` and push the change
4. Daniel reviews and merges — after merge, update status to ✅ DONE

**Always commit `plan/PLAN.md` together with your last code commit so the tracker stays in sync.**

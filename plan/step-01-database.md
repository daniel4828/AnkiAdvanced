# Step 01 — Database Migrations & New Functions

**Branch:** `feat/db-migrations`
**Depends on:** nothing (do this first)
**Blocks:** all other steps

---

## What to Read First

1. `database.py` — full file (understand existing structure of `init_db()` and function conventions)
2. `recovery/2026-03-24_database-import-functions.md` — DB functions for import
3. `recovery/2026-03-24_trash-browse-redesign.md` — trash & browse DB functions
4. `recovery/2026-03-24_suspension-toggle-all-categories.md` — suspension DB functions
5. `recovery/2026-03-23_sentences-deck-routing.md` — sentence routing DB functions
6. `recovery/going_manually_through_chats.md` lines 1–400 — additional context

---

## Schema Changes (add to `init_db()` as safe migrations)

All migrations must be safe for existing databases. Use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
or check if column exists before adding. Never DROP columns.

### New columns on `words` table

```sql
ALTER TABLE words ADD COLUMN source_sentence TEXT;
ALTER TABLE words ADD COLUMN grammar_notes TEXT;
ALTER TABLE words ADD COLUMN note_type TEXT DEFAULT 'vocabulary';
ALTER TABLE words ADD COLUMN hsk_level INT;
```

### New columns on `cards` table

```sql
ALTER TABLE cards ADD COLUMN deleted_at TEXT;
```

### New columns on `decks` table

```sql
ALTER TABLE decks ADD COLUMN deleted_at TEXT;
```

### New table: `note_components`

```sql
CREATE TABLE IF NOT EXISTS note_components (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id   INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    word_id   INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    UNIQUE(note_id, word_id)
);
```

### New columns on `deck_presets` table (display order / bury settings)

```sql
ALTER TABLE deck_presets ADD COLUMN bury_new_siblings INTEGER NOT NULL DEFAULT 0;
ALTER TABLE deck_presets ADD COLUMN bury_review_siblings INTEGER NOT NULL DEFAULT 0;
ALTER TABLE deck_presets ADD COLUMN bury_interday_siblings INTEGER NOT NULL DEFAULT 0;
ALTER TABLE deck_presets ADD COLUMN bury_quick_mode TEXT NOT NULL DEFAULT 'all' CHECK(bury_quick_mode IN ('all','none','custom'));
ALTER TABLE deck_presets ADD COLUMN new_gather_order TEXT NOT NULL DEFAULT 'deck' CHECK(new_gather_order IN ('deck','deck_random_notes','ascending_position','descending_position','random_notes','random_cards'));
ALTER TABLE deck_presets ADD COLUMN new_sort_order TEXT NOT NULL DEFAULT 'card_type_gathered' CHECK(new_sort_order IN ('card_type_gathered','gathered','card_type_random','random_note_card_type','random'));
ALTER TABLE deck_presets ADD COLUMN new_review_order TEXT NOT NULL DEFAULT 'mixed' CHECK(new_review_order IN ('mixed','new_first','reviews_first'));
ALTER TABLE deck_presets ADD COLUMN interday_learning_review_order TEXT NOT NULL DEFAULT 'mixed' CHECK(interday_learning_review_order IN ('mixed','learning_first','reviews_first'));
ALTER TABLE deck_presets ADD COLUMN review_sort_order TEXT NOT NULL DEFAULT 'due_random' CHECK(review_sort_order IN ('due_random','due_deck','deck_due','ascending_intervals','descending_intervals','ascending_ease','descending_ease','relative_overdueness'));
```

**Important:** SQLite doesn't support `ADD COLUMN IF NOT EXISTS`. Wrap each in a try/except
inside `init_db()` like the existing pattern in the codebase.

---

## New Database Functions to Add

All go in `database.py`. Follow the existing code style (use `_conn()` context manager,
return plain dicts or None, never raise — log errors).

### Import support

```python
def word_has_cards(word_id: int) -> bool:
    """Returns True if the word already has at least one card."""
    with _conn() as db:
        row = db.execute(
            "SELECT 1 FROM cards WHERE word_id = ? LIMIT 1", (word_id,)
        ).fetchone()
        return row is not None

def insert_note_component(note_id: int, word_id: int, position: int) -> None:
    """Link a component word to a parent note (sentence/chengyu)."""
    with _conn() as db:
        db.execute(
            "INSERT OR IGNORE INTO note_components (note_id, word_id, position) VALUES (?,?,?)",
            (note_id, word_id, position)
        )
```

### Sentence deck routing

```python
def get_sentences_deck_ids() -> dict:
    """
    Return {listening: id, reading: id, creating: id} for the three Sentences leaf decks.
    Creates Sentences > Listening/Reading/Creating if they don't exist.
    """
    # find or create "Sentences" root deck
    # find or create its three children
    # return dict with their IDs

def get_all_deck_id() -> int:
    """Get or create the top-level 'All' deck, returning its id."""

def get_or_create_deck_path(deck_path: str) -> int:
    """
    Given an Anki-style 'Parent::Child::Leaf' path, return the leaf deck id.
    Creates any missing decks along the path.
    """

def is_sentences_deck(deck_id: int) -> bool:
    """Return True if deck_id is the Sentences deck or any of its children."""
```

### Trash / soft-delete

```python
def soft_delete_card(card_id: int) -> None:
    """Mark card as deleted (sets deleted_at to now)."""
    with _conn() as db:
        db.execute(
            "UPDATE cards SET deleted_at = datetime('now') WHERE id = ?", (card_id,)
        )

def restore_card(card_id: int) -> None:
    """Clear deleted_at on a soft-deleted card."""
    with _conn() as db:
        db.execute("UPDATE cards SET deleted_at = NULL WHERE id = ?", (card_id,))

def purge_card(card_id: int) -> None:
    """Hard-delete a card permanently."""
    with _conn() as db:
        db.execute("DELETE FROM cards WHERE id = ?", (card_id,))

def purge_old_trash(days: int = 30) -> int:
    """Hard-delete cards that were soft-deleted more than `days` ago. Returns count."""
    with _conn() as db:
        cur = db.execute(
            "DELETE FROM cards WHERE deleted_at IS NOT NULL "
            "AND deleted_at < datetime('now', ? || ' days')",
            (f'-{days}',)
        )
        return cur.rowcount

def get_trash_words() -> list[dict]:
    """Return words that have at least one soft-deleted card."""

def soft_delete_deck(deck_id: int) -> None:
    """Soft-delete a deck (sets deleted_at to now)."""

def restore_deck(deck_id: int) -> None:
    """Restore a soft-deleted deck."""

def purge_deck(deck_id: int) -> None:
    """Hard-delete a soft-deleted deck and all its cards."""

def get_trash() -> dict:
    """Return {decks: [...], cards: [...]} of all soft-deleted items."""
    # decks: each entry has deck info + cards inside it
    # cards: orphan soft-deleted cards (deck not deleted)
```

### Suspension toggles

```python
def get_category_all_suspended(deck_id: int, category: str) -> bool:
    """Return True if ALL cards in this deck+descendants for this category are suspended."""

def toggle_category_suspension(deck_id: int, category: str) -> dict:
    """
    If all suspended → unsuspend all. Otherwise → suspend all.
    Returns {suspended: bool, affected: int}.
    """

def get_deck_all_suspended(deck_id: int) -> bool:
    """Return True if ALL cards in this deck+all descendants are suspended."""

def toggle_deck_all_suspension(deck_id: int) -> dict:
    """
    Toggle suspension for all cards in deck + all descendant decks recursively.
    Returns {suspended: bool, affected: int}.
    """
```

### Browse / character detail

```python
def get_characters_for_browse() -> list[dict]:
    """Return all characters sorted by pinyin for the Browse > Hanzi tab."""

def get_character_full(char_text: str) -> dict:
    """Return character detail including all words that contain it."""
```

### Note components (for sentence/chengyu display)

```python
def get_note_components(word_id: int) -> list[dict]:
    """
    Return component words for a note (sentence/chengyu).
    Each entry: {word_id, word_zh, pinyin, definition, position, characters: [...]}
    """
```

### Card delete (existing browse route needs this)

```python
def delete_card(card_id: int) -> None:
    """Alias for soft_delete_card."""
    soft_delete_card(card_id)
```

---

## Existing Functions to Update

### `get_word_full(word_id)` — add `note_type`, `hsk_level`, `components`

The existing function should also return:
- `note_type` from the words table
- `hsk_level` from the words table
- `components` from `get_note_components(word_id)`

### `update_word(word_id, fields)` — extend allowed fields

Add to the allowed set:
```python
allowed = {"word_zh", "pinyin", "definition", "pos", "traditional",
           "definition_zh", "notes", "hsk_level", "source_sentence",
           "grammar_notes", "note_type"}
```

### `get_due_cards()` / `get_next_card()` — exclude soft-deleted cards

All card queries must add `AND cards.deleted_at IS NULL` to WHERE clauses.

---

## How to Implement

1. Create branch: `git checkout -b feat/db-migrations`
2. Edit `database.py`:
   - In `init_db()`: add all `ALTER TABLE` calls wrapped in try/except
   - Add `CREATE TABLE IF NOT EXISTS note_components`
   - Add all new functions listed above
   - Update existing functions listed above
3. Test: `python main.py status` — should not crash
4. Test: start server `python main.py` and verify `/api/decks` still works
5. Commit: `git add database.py schema.sql && git commit -m "feat: DB migrations + new functions for trash, suspension, import, components"`
6. Push and open PR

---

## Verification Checklist

- [ ] `python main.py` starts without errors
- [ ] `/api/decks` returns deck tree
- [ ] `/api/today/{deck_id}/{category}` still works
- [ ] `init_db()` can be called on existing DB without data loss
- [ ] `get_word_full()` returns `note_type` and `hsk_level` fields
- [ ] `get_due_cards()` excludes soft-deleted cards

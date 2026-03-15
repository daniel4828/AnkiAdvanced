-- Chinese SRS Database Schema

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- deck_presets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deck_presets (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,

    -- Per-category daily limits
    listening_new_per_day   INTEGER NOT NULL DEFAULT 20,
    listening_reviews_per_day INTEGER NOT NULL DEFAULT 100,
    reading_new_per_day     INTEGER NOT NULL DEFAULT 20,
    reading_reviews_per_day INTEGER NOT NULL DEFAULT 100,
    creating_new_per_day    INTEGER NOT NULL DEFAULT 10,
    creating_reviews_per_day INTEGER NOT NULL DEFAULT 50,

    -- Learning steps in minutes, space-separated e.g. "1 10"
    learning_steps          TEXT NOT NULL DEFAULT '1 10',

    -- Graduation intervals in days
    graduating_interval     INTEGER NOT NULL DEFAULT 1,
    easy_interval           INTEGER NOT NULL DEFAULT 4,

    -- Relearning steps in minutes, space-separated e.g. "10"
    relearning_steps        TEXT NOT NULL DEFAULT '10',

    -- Review scheduling
    minimum_interval        INTEGER NOT NULL DEFAULT 1,

    -- Leech settings
    leech_threshold         INTEGER NOT NULL DEFAULT 8,
    leech_action            TEXT NOT NULL DEFAULT 'suspend'
                                CHECK(leech_action IN ('suspend', 'tag'))
);

-- ---------------------------------------------------------------------------
-- decks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    parent_id   INTEGER REFERENCES decks(id) ON DELETE CASCADE,
    preset_id   INTEGER NOT NULL REFERENCES deck_presets(id)
);

-- ---------------------------------------------------------------------------
-- words
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS words (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word_zh         TEXT NOT NULL UNIQUE,
    pinyin          TEXT,
    definition      TEXT,           -- English definition
    pos             TEXT,           -- part of speech
    hsk_level       INTEGER,        -- 1-6, NULL for 超纲
    deck_id         INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    traditional     TEXT,
    definition_zh   TEXT,
    date_added      TEXT NOT NULL DEFAULT (datetime('now')),
    source          TEXT NOT NULL DEFAULT 'kouyu'
);

-- ---------------------------------------------------------------------------
-- word_examples
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word_examples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id         INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    example_zh      TEXT NOT NULL,
    example_pinyin  TEXT,
    example_de      TEXT,
    position        INTEGER NOT NULL
);

-- ---------------------------------------------------------------------------
-- characters
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS characters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    char            TEXT NOT NULL UNIQUE,
    traditional     TEXT,
    pinyin          TEXT,
    hsk_level       INTEGER,
    etymology       TEXT,
    other_meanings  TEXT,   -- JSON array
    compounds       TEXT    -- JSON array
);

-- ---------------------------------------------------------------------------
-- word_characters  (junction table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word_characters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id             INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    char_id             INTEGER NOT NULL REFERENCES characters(id),
    position            INTEGER NOT NULL,
    meaning_in_context  TEXT,
    UNIQUE(word_id, char_id)
);

-- ---------------------------------------------------------------------------
-- cards
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id     INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    category    TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
    state       TEXT NOT NULL DEFAULT 'new'
                    CHECK(state IN ('new', 'learning', 'review', 'relearn', 'suspended')),

    -- due: ISO datetime for learning/relearn, ISO date for new/review
    due         TEXT NOT NULL DEFAULT (date('now')),

    step_index  INTEGER NOT NULL DEFAULT 0,
    interval    INTEGER NOT NULL DEFAULT 0,     -- days
    ease        REAL    NOT NULL DEFAULT 2.5,
    repetitions INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,

    UNIQUE(word_id, category)
);

-- ---------------------------------------------------------------------------
-- review_log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    reviewed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    rating          INTEGER NOT NULL CHECK(rating IN (1, 2, 3, 4)),
    user_response   TEXT,       -- what the user typed (creating category)
    ai_score        INTEGER     -- future: AI evaluation score
);

-- ---------------------------------------------------------------------------
-- stories
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,  -- YYYY-MM-DD
    category        TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
    deck_id         INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    generated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    -- NO unique constraint: multiple stories per (date, category, deck) allowed
    -- active story = latest generated_at
    -- stories are NEVER auto-deleted
);

-- ---------------------------------------------------------------------------
-- sentences
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sentences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    word_id     INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    sentence_zh TEXT NOT NULL,
    sentence_en TEXT NOT NULL,
    UNIQUE(story_id, word_id),
    UNIQUE(story_id, position)
);

-- ---------------------------------------------------------------------------
-- structures  (future)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS structures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL,
    description TEXT,
    example_zh  TEXT,
    example_en  TEXT
);

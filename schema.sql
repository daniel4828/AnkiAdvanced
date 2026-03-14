CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word_zh TEXT NOT NULL UNIQUE,        -- simplified Chinese characters
    traditional TEXT,                    -- traditional characters
    pinyin TEXT,
    definition TEXT,                     -- English definition (short)
    definition_zh TEXT,                  -- Chinese definition
    cultural_note TEXT,                  -- cultural/usage note
    pos TEXT,
    hsk_level INTEGER DEFAULT 5,
    frequency INTEGER DEFAULT 0,
    example_zh TEXT,                     -- primary example sentence (Chinese)
    example_en TEXT,                     -- primary example sentence (English/German)
    date_added TEXT,
    source TEXT DEFAULT 'language_reactor',
    known INTEGER DEFAULT 0              -- 1 = already known, story vocab only
);

CREATE TABLE IF NOT EXISTS word_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    zh TEXT NOT NULL,
    pinyin TEXT,
    translation TEXT                     -- German or English
);

CREATE TABLE IF NOT EXISTS word_characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    char TEXT NOT NULL,
    traditional TEXT,
    pinyin TEXT,
    hsk TEXT,                            -- raw value e.g. "3", "5-6", "超纲"
    detailed_analysis INTEGER DEFAULT 0,
    meaning_in_context TEXT,
    other_meanings TEXT,                 -- JSON array of strings
    etymology TEXT,
    etymology_example TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS character_compounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES word_characters(id) ON DELETE CASCADE,
    simplified TEXT NOT NULL,
    pinyin TEXT,
    meaning TEXT
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id INTEGER NOT NULL REFERENCES words(id),
    category TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
    state TEXT DEFAULT 'new' CHECK(state IN ('new', 'learning', 'review', 'locked')),
    due_date TEXT DEFAULT (date('now')),
    interval INTEGER DEFAULT 1,
    ease REAL DEFAULT 2.5,
    repetitions INTEGER DEFAULT 0,
    learning_step INTEGER DEFAULT 0,
    learning_due TEXT,
    UNIQUE(word_id, category)
);

CREATE TABLE IF NOT EXISTS review_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    reviewed_at TEXT DEFAULT (datetime('now')),
    rating INTEGER NOT NULL CHECK(rating IN (1,2,3,4)),
    user_response TEXT,
    ai_score INTEGER
);

CREATE TABLE IF NOT EXISTS daily_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
    word_ids TEXT NOT NULL,
    content_zh TEXT NOT NULL,
    content_en TEXT NOT NULL,
    sentences_zh TEXT NOT NULL,
    sentences_en TEXT NOT NULL,
    UNIQUE(date, category)
);

CREATE TABLE IF NOT EXISTS structures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    description TEXT,
    example_zh TEXT,
    example_en TEXT
);

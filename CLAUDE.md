# Chinese SRS — Project Briefing

## What this is
A personal Spaced Repetition System for learning Mandarin Chinese, built for one user (Daniel, HSK 4–5 level). It replaces Anki with an AI-powered review experience that generates contextual stories using the words due for review each day.

## Tech Stack
- **Backend:** Python + FastAPI
- **Database:** SQLite (local, via `sqlite3` stdlib — no ORM)
- **Frontend:** Single HTML file with vanilla JS (no framework) served by FastAPI
- **AI:** Anthropic API (`claude-sonnet-4-6` for evaluation, `claude-haiku-4-5-20251001` for story generation)
- **TTS:** `edge-tts` with voice `zh-CN-XiaoxiaoNeural` (plays via `afplay`)
- **Language:** All UI labels in English, all content in Mandarin Chinese

## Project Structure
```
├── CLAUDE.md              # This file
├── main.py                # CLI entry + FastAPI app
├── database.py            # All DB access (no raw SQL elsewhere)
├── srs.py                 # Anki-style SM-2 scheduling
├── importer.py            # Kouyu YAML importer
├── ai.py                  # Anthropic API calls
├── tts.py                 # edge-tts wrapper
├── schema.sql             # Database schema
├── static/
│   └── index.html         # Single-page frontend (M2+)
├── data/
│   └── srs.db             # SQLite database (auto-created)
└── imports/
    └── Kouyu/             # YAML vocabulary files (only import source)
```

## Data Source
**Only source:** `imports/Kouyu/*.yaml` — rich YAML with pos, examples, etymology, character breakdowns.

HSK CSVs are NOT imported. The AI is simply told "use only HSK 1–2 vocabulary for non-target words" in the story prompt.

## Database Schema (summary)

```
deck_presets → decks (self-ref parent_id for nesting) → words
                                                          ├── word_examples
                                                          ├── word_characters → characters
                                                          └── cards → review_log

decks → stories → sentences → words (FK)
```

Key design decisions:
- `cards.due` is a single TEXT field: ISO datetime for learning/relearn, ISO date for new/review
- `cards.state`: `new` | `learning` | `review` | `relearn` | `suspended`
- `cards.step_index` tracks position in learning/relearn steps
- `cards.lapses` counts failed review cards (for leech detection)
- `stories` has NO unique constraint — multiple per (date, category, deck) allowed; active = latest `generated_at`; never auto-deleted
- `sentences` links story → word with position and 1:1 constraint per story

## Scheduling — Anki SM-2 variant

### States
- `new` → `learning` (first review)
- `learning` → `review` (graduated after all steps passed)
- `review` → `relearn` (rated Again = lapse)
- `relearn` → `review` (relearn steps passed)

### Learning steps (default: 1min, 10min)
- **Again** → step_index=0, due=now+steps[0]min
- **Hard** → step_index unchanged; delay = avg of step[0]+step[1] on step 0, else current step
- **Good** → advance step; if last step → graduate (state=review, interval=graduating_interval)
- **Easy** → graduate immediately (interval=easy_interval)

### Review phase
- **Again** → lapse: ease-=0.20, interval×0.5, state=relearn
- **Hard** → ease-=0.15, interval×1.2
- **Good** → interval×ease
- **Easy** → ease+=0.15, interval×ease×1.3

Ease floor: 1.3. Leech: suspend when lapses >= leech_threshold.

## Queue design
No queue table. `get_due_cards()` assembles the queue from live DB state on every call.
`get_next_card()` returns the top-priority card (LIMIT 1):
  1. Intraday due (learning/relearn, due <= now)
  2. Review cards (due <= today)
  3. New cards (up to daily limit)

`POST /api/review` returns `{next_card, counts}` — no extra round-trip needed.

## Story generation
- Each category (reading/listening/creating) generates its own independent story
- Exactly 1 sentence per target word (1:1 mapping by position)
- `get_due_cards()` used to collect all target words for the AI prompt
- `create_story()` always inserts a new row — regeneration = another row, old stories kept forever
- Haiku prompt enforces: coherent narrative, same characters, ≤15 chars per sentence, HSK 1–2 background vocab

## UI category order
Reading → Listening → Creating

## API Endpoints

```
GET  /api/decks                          → deck tree with due counts
POST /api/decks                          → create deck
PUT  /api/decks/{id}                     → rename deck
GET  /api/decks/{id}/preset              → get preset settings
PUT  /api/decks/{id}/preset              → update preset settings

GET  /api/today/{deck_id}/{category}     → {card, counts}  (top card + progress counts)
GET  /api/story/{deck_id}/{category}     → active story for today (generate if none)
POST /api/story/{deck_id}/{category}/regenerate → create new story from current queue
POST /api/review                         → {card_id, rating, user_response?}
                                           returns {next_card, counts}
POST /api/speak                          → {text} → triggers TTS
POST /api/import                         → trigger YAML import
GET  /api/browse                         → {deck_id?, category?, state?, q?}
GET  /api/stats                          → global or per-deck stats
```

## CLI

```bash
python main.py import               # import all Kouyu YAML files
python main.py status               # show due counts per deck/category
python main.py status --deck Kouyu  # filter to one deck
```

## Rules & Conventions
- All DB access goes through `database.py` — no raw SQL in other files
- Keep `ai.py` clean — one function per prompt type
- No external dependencies beyond: `fastapi`, `uvicorn`, `anthropic`, `edge-tts`, `pyyaml`
- Frontend is a single `index.html` — no build step, no npm
- Never store the API key in code — read from `ANTHROPIC_API_KEY` env var
- Always handle malformed AI JSON with try/except + fallback

## Milestones
- **M1** ✅ Schema, SM-2, Kouyu YAML importer, CLI (`import` + `status`)
- **M2** — Listening module (story generation + TTS + review loop + frontend)
- **M3** — Reading module (reuses M2 story, no TTS)
- **M4** — Creating module (self-rated translation)
- **M5** — Full Anki-like UI (deck list, browse, options modal, stats)
- **M6** — Polish (streak, leech tagging UI, AnkiConnect export)

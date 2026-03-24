# Recovery Plan — AnkiAdvanced

## Background

A `git reset --hard` to commit `8f9f396` (branch `wip/backend-recovery`) wiped ~6 days of work.
All lost changes are documented in `recovery/`. This plan reconstructs them systematically.

**Baseline commit:** `8f9f396` (wip: save backend changes before frontend recovery)

## Git Workflow (MANDATORY going forward)

- Every step gets its own branch: `feat/<step-name>`
- Commit frequently — at minimum after each file is done
- Open a PR when the step is complete; do NOT merge without Daniel's approval
- Never work directly on `main` or `wip/backend-recovery`

## How to start each step (professional workflow)

```bash
# 1. Make sure main is up to date
git checkout main && git pull

# 2. Create a GitHub Issue for the step (or use the one already created)
gh issue create --title "feat: DB migrations & new functions" \
  --label "feature,database" --milestone "Recovery Sprint" \
  --body "See plan/step-01-database.md"
# Note the issue number, e.g. #12

# 3. Create a branch that references the issue
git checkout -b feat/12-db-migrations

# 4. Work — commit after every logical unit
git add database.py
git commit -m "feat: add soft-delete columns to cards and decks"
git add database.py
git commit -m "feat: add note_components table"
# ...

# 5. Open a PR when done
gh pr create --fill
# Fill in the PR template — reference "Closes #12"

# 6. Wait for CI to pass and Daniel to approve
# 7. Daniel merges → issue auto-closes
```

## Token Efficiency Strategy

Each step file is self-contained: it lists exactly which files to read,
which recovery docs to reference, and what to implement. Give a new Claude
chat the PLAN.md + the specific step file. It does not need the whole recovery folder.

---

## Steps Overview

| # | Issue | Step | Status | Branch | Depends On |
|---|-------|------|--------|--------|------------|
| 01 | [#41](https://github.com/daniel4828/AnkiAdvanced/issues/41) | [Database migrations & new functions](step-01-database.md) | 🔲 TODO | `feat/41-db-migrations` | — |
| 02 | [#38](https://github.com/daniel4828/AnkiAdvanced/issues/38) | [Multi-provider AI (DeepSeek/GLM/Qwen)](step-02-ai-provider.md) | 🔲 TODO | `feat/38-ai-provider` | — |
| 03 | [#39](https://github.com/daniel4828/AnkiAdvanced/issues/39) | [Importer: multi-type YAML + preview](step-03-importer.md) | 🔲 TODO | `feat/39-importer` | #41 |
| 04 | [#40](https://github.com/daniel4828/AnkiAdvanced/issues/40) | [Import API endpoints](step-04-import-api.md) | 🔲 TODO | `feat/40-import-api` | #41, #39 |
| 05 | [#42](https://github.com/daniel4828/AnkiAdvanced/issues/42) | [Review fix: parent-deck + undo](step-05-review-fix.md) | 🔲 TODO | `fix/42-review-fix` | #41 |
| 06 | [#48](https://github.com/daniel4828/AnkiAdvanced/issues/48) | [Story fix: sentences deck + mixed mode](step-06-story-fix.md) | 🔲 TODO | `fix/48-story-fix` | #41 |
| 07 | [#43](https://github.com/daniel4828/AnkiAdvanced/issues/43) | [Trash system: API + DB](step-07-trash-api.md) | 🔲 TODO | `feat/43-trash` | #41 |
| 08 | [#44](https://github.com/daniel4828/AnkiAdvanced/issues/44) | [Suspension toggle: category + deck-wide](step-08-suspension-api.md) | 🔲 TODO | `feat/44-suspension` | #41 |
| 09 | [#51](https://github.com/daniel4828/AnkiAdvanced/issues/51) | [Browse: AI enrich endpoint](step-09-browse-enrich-api.md) | 🔲 TODO | `feat/51-browse-enrich` | #41, #38 |
| 10 | [#52](https://github.com/daniel4828/AnkiAdvanced/issues/52) | [Frontend: story modal model selector](step-10-frontend-story-modal.md) | 🔲 TODO | `feat/52-frontend-story` | #38, #48 |
| 11 | [#49](https://github.com/daniel4828/AnkiAdvanced/issues/49) | [Frontend: trash modal](step-11-frontend-trash.md) | 🔲 TODO | `feat/49-frontend-trash` | #43 |
| 12 | [#50](https://github.com/daniel4828/AnkiAdvanced/issues/50) | [Frontend: suspension badges](step-12-frontend-suspension.md) | 🔲 TODO | `feat/50-frontend-suspension` | #44 |
| 13 | [#45](https://github.com/daniel4828/AnkiAdvanced/issues/45) | [Frontend: import modal (preview + editor)](step-13-frontend-import.md) | 🔲 TODO | `feat/45-frontend-import` | #40 |
| 14 | [#46](https://github.com/daniel4828/AnkiAdvanced/issues/46) | [Frontend: sentence/chengyu card design](step-14-frontend-card-design.md) | 🔲 TODO | `feat/46-frontend-cards` | #41 |
| 15 | [#47](https://github.com/daniel4828/AnkiAdvanced/issues/47) | [Frontend: HSK badge + AI enrich](step-15-frontend-hsk-badge.md) | 🔲 TODO | `feat/47-frontend-hsk` | #51 |

### Status Key
- 🔲 TODO — not started
- 🔄 IN PROGRESS — branch created, work underway
- 👀 REVIEW — PR open, waiting for Daniel
- ✅ DONE — merged to main

---

## Dependency Graph

```
01 (DB)
├── 02 (AI)  ─────────────────────────────── 10 (frontend: story modal)
│                                             └── needs 06 too
├── 03 (Importer)
│   └── 04 (Import API) ─────────────────── 13 (frontend: import modal)
├── 05 (Review fix)
├── 06 (Story fix) ──────────────────────── 10 (frontend: story modal)
├── 07 (Trash API) ──────────────────────── 11 (frontend: trash modal)
├── 08 (Suspension API) ─────────────────── 12 (frontend: suspension)
└── 09 (Browse enrich) ──── needs 02 ────── 15 (frontend: HSK badge)

14 (frontend: card design) needs 01
```

## Recommended Implementation Order

**Parallel batch 1** (no dependencies on each other):
- Step 01 (DB) — do this first, everything else unblocks after

**Parallel batch 2** (all depend only on 01):
- Step 02 (AI provider)
- Step 03 (Importer)
- Step 05 (Review fix)
- Step 06 (Story fix)
- Step 07 (Trash API)
- Step 08 (Suspension API)
- Step 14 (Frontend: card design)

**Parallel batch 3**:
- Step 04 (Import API) — needs 01 + 03
- Step 09 (Browse enrich) — needs 01 + 02

**Parallel batch 4** (all frontend, depend on their backend):
- Step 10, 11, 12, 13, 15

---

## Notes

- Recovery docs are in `recovery/` — each step file lists which ones are relevant
- `recovery/going_manually_through_chats.md` is the most comprehensive (newest-to-oldest)
- The current working DB has live data — schema changes must use `ALTER TABLE IF NOT EXISTS` style migrations in `init_db()`, never drop columns
- All DB access through `database.py` only — no raw SQL elsewhere

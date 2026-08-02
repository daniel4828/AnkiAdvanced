#!/usr/bin/env python3
"""Server side of the offline sync (issue #612).

Runs ON the VPS, piped in over ssh stdin by sync_offline.sh, so it never
depends on the server having deployed the current commit. Standard library
only for the same reason — it must not need the app's virtualenv.

    python3 - prepare <prod_db> <snapshot_out>
        Stamp a fresh sync token + review_log watermark into the production
        database, then snapshot it (WAL-safe) to <snapshot_out>. The snapshot
        carries the same token, which is what lets `merge` later prove that an
        incoming file really came from this server and hasn't been used yet.

    python3 - merge <prod_db> <incoming_db>
        Merge the reviews done offline back into production, then rotate the
        token so the same file can't be merged twice.

Merge scope — deliberately narrow. The server keeps running cron jobs while
Daniel is away (podcast episodes, morning pregen, cost logs), so copying the
whole database back would destroy that work. Offline reviewing can only touch
two tables, and only those two are merged:

    review_log  — rows newer than the watermark are appended (fresh ids)
    cards       — rows whose scheduling state differs are updated in place

Cards that exist offline but not on the server are ignored: the offline
instance cannot create entries or cards.
"""

import os
import sqlite3
import sys
import uuid

TOKEN_KEY = "offline_sync_token"
WATERMARK_KEY = "offline_sync_review_log_max"

# Every card column the review flow can change. `id`, `word_id`, `deck_id` and
# `category` identify the card and are never written.
CARD_FIELDS = [
    "state", "due", "step_index", "interval", "ease", "repetitions", "lapses",
    "stability", "difficulty", "last_review", "learning_again_count",
    "is_leech", "buried_until", "pre_suspend_state", "next_note", "probation",
]

REVIEW_FIELDS = [
    "card_id", "reviewed_at", "rating", "user_response", "ai_score",
    "duration_ms", "state", "last_interval",
]


def _connect(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        sys.exit(f"ERROR: database not found: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_setting(conn: sqlite3.Connection, key: str, schema: str = "main") -> str | None:
    row = conn.execute(
        f"SELECT value FROM {schema}.app_settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def cmd_prepare(prod_db: str, snapshot_out: str) -> None:
    conn = _connect(prod_db)
    token = uuid.uuid4().hex
    watermark = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM review_log").fetchone()["m"]
    _set_setting(conn, TOKEN_KEY, token)
    _set_setting(conn, WATERMARK_KEY, str(watermark))
    conn.commit()

    # sqlite3's backup API is safe against concurrent writers (the live app is
    # still serving requests), unlike copying the file.
    if os.path.exists(snapshot_out):
        os.unlink(snapshot_out)
    dest = sqlite3.connect(snapshot_out)
    conn.backup(dest)
    dest.close()
    conn.close()
    os.chmod(snapshot_out, 0o600)

    print(f"token={token}")
    print(f"review_log_watermark={watermark}")
    print(f"snapshot={snapshot_out} ({os.path.getsize(snapshot_out) // 1024} KB)")


def cmd_merge(prod_db: str, incoming_db: str) -> None:
    conn = _connect(prod_db)
    if not os.path.exists(incoming_db):
        sys.exit(f"ERROR: incoming database not found: {incoming_db}")
    conn.execute("ATTACH ? AS off", (incoming_db,))

    server_token = _get_setting(conn, TOKEN_KEY)
    offline_token = _get_setting(conn, TOKEN_KEY, schema="off")
    if not server_token or not offline_token:
        sys.exit("ERROR: no sync token — this database was never prepared by `pull`.")
    if server_token != offline_token:
        sys.exit("ERROR: sync token mismatch. This offline database was already "
                 "pushed, or it came from a different pull. Refusing to merge.")

    watermark = int(_get_setting(conn, WATERMARK_KEY, schema="off") or 0)

    # ── review_log: append everything recorded offline ────────────────────────
    cols = ", ".join(REVIEW_FIELDS)
    placeholders = ", ".join("?" for _ in REVIEW_FIELDS)
    new_reviews = conn.execute(
        f"SELECT {cols} FROM off.review_log WHERE id > ? ORDER BY id", (watermark,)
    ).fetchall()
    # Skip reviews for cards the server no longer has (deleted while away).
    known = {r["id"] for r in conn.execute("SELECT id FROM main.cards")}
    kept = [tuple(r) for r in new_reviews if r["card_id"] in known]
    conn.executemany(
        f"INSERT INTO main.review_log ({cols}) VALUES ({placeholders})", kept)
    skipped_reviews = len(new_reviews) - len(kept)

    # ── cards: update rows whose scheduling state changed ─────────────────────
    field_list = ", ".join(CARD_FIELDS)
    server_cards = {
        r["id"]: tuple(r)[1:]
        for r in conn.execute(f"SELECT id, {field_list} FROM main.cards")
    }
    changed = []
    for row in conn.execute(f"SELECT id, {field_list} FROM off.cards"):
        before = server_cards.get(row["id"])
        if before is not None and before != tuple(row)[1:]:
            changed.append(tuple(row)[1:] + (row["id"],))
    assignments = ", ".join(f"{f} = ?" for f in CARD_FIELDS)
    conn.executemany(f"UPDATE main.cards SET {assignments} WHERE id = ?", changed)

    # Rotate the token so this same file can never be merged a second time.
    _set_setting(conn, TOKEN_KEY, uuid.uuid4().hex)
    conn.commit()
    conn.execute("DETACH off")
    conn.close()

    print(f"reviews_merged={len(kept)}")
    print(f"reviews_skipped_deleted_card={skipped_reviews}")
    print(f"cards_updated={len(changed)}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    command, db, other = sys.argv[1], sys.argv[2], sys.argv[3]
    if command == "prepare":
        cmd_prepare(db, other)
    elif command == "merge":
        cmd_merge(db, other)
    else:
        sys.exit(f"ERROR: unknown command {command!r}")

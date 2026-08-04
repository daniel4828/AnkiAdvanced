#!/usr/bin/env python3
"""Server side of the offline sync (issue #612).

Runs ON the VPS, piped in over ssh stdin by sync_offline.sh, so it never
depends on the server having deployed the current commit. Standard library
only for the same reason — it must not need the app's virtualenv.

    python3 - prepare <prod_db> <snapshot_out> [--full]
        Stamp a fresh sync token + review_log watermark into the production
        database, then snapshot it (WAL-safe) to <snapshot_out>. The snapshot
        carries the same token, which is what lets `merge` later prove that an
        incoming file really came from this server and hasn't been used yet.
        The snapshot is slimmed by default (see _SLIM_STATEMENTS) because the
        link to the laptop can be very slow; --full keeps every byte.

    python3 - merge <prod_db> <incoming_db>
        Merge the reviews done offline back into production, then rotate the
        token so the same file can't be merged twice.

Merge scope — deliberately narrow. The server keeps running cron jobs while
Daniel is away (podcast episodes, morning pregen, cost logs), so copying the
whole database back would destroy that work. Offline reviewing can only touch
two tables, and only those two are merged:

    review_log  — rows newer than the watermark are appended (fresh ids)
    cards       — rows whose scheduling state differs are updated in place,
                  unless the server's own last_review is newer (#625)

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


def cmd_prepare(prod_db: str, snapshot_out: str, slim: bool = True) -> None:
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

    raw_kb = os.path.getsize(snapshot_out) // 1024
    if slim:
        _slim(snapshot_out)
    os.chmod(snapshot_out, 0o600)

    print(f"token={token}")
    print(f"review_log_watermark={watermark}")
    size_kb = os.path.getsize(snapshot_out) // 1024
    note = f" (slimmed from {raw_kb} KB)" if slim else ""
    print(f"snapshot={snapshot_out} ({size_kb} KB){note}")


# Bulk that costs megabytes and is worthless on a plane. Dropping it from the
# SNAPSHOT only — production is untouched — is safe because `merge` copies back
# nothing but cards and review_log.
_SLIM_STATEMENTS = [
    # full AI prompts + responses, only ever read by the cost page
    "DELETE FROM api_call_log",
    # podcast transcripts are the single biggest table; the German summaries and
    # HSK word lists are small, so they stay readable offline
    "UPDATE podcast_episodes SET transcript_zh = NULL, transcript_de = NULL",
    # the prompt that produced each story, shown only in a debug view
    "UPDATE stories SET prompt_text = NULL",
]


def _slim(path: str) -> None:
    conn = sqlite3.connect(path)
    for statement in _SLIM_STATEMENTS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as e:
            # A table missing on an older schema is not worth failing a sync over.
            print(f"  slim: skipped ({e})", file=sys.stderr)
    conn.commit()
    conn.execute("VACUUM")   # actually reclaim the freed pages
    conn.close()


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
    # Last review wins. The laptop used to overwrite the server unconditionally,
    # which is fine for a flight but silently loses progress once the laptop is
    # an everyday instance and Daniel also reviews on his phone (#625).
    # last_review is an ISO string, so plain string comparison is chronological.
    field_list = ", ".join(CARD_FIELDS)
    lr = CARD_FIELDS.index("last_review")
    server_cards = {
        r["id"]: tuple(r)[1:]
        for r in conn.execute(f"SELECT id, {field_list} FROM main.cards")
    }
    changed = []
    kept_server = 0
    for row in conn.execute(f"SELECT id, {field_list} FROM off.cards"):
        before = server_cards.get(row["id"])
        after = tuple(row)[1:]
        if before is None or before == after:
            continue
        # A card reviewed on the server after the pull is newer than whatever
        # the laptop has. Its own review rows were already appended above, so
        # skipping the card row is all that's needed to preserve it.
        if before[lr] is not None and (after[lr] is None or before[lr] > after[lr]):
            kept_server += 1
            continue
        changed.append(after + (row["id"],))
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
    print(f"cards_kept_server_newer={kept_server}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 3:
        sys.exit(__doc__)
    command, db, other = args
    if command == "prepare":
        cmd_prepare(db, other, slim="--full" not in flags)
    elif command == "merge":
        cmd_merge(db, other)
    else:
        sys.exit(f"ERROR: unknown command {command!r}")

#!/bin/bash
# SQLite backup (runs every 6h via launchd) — keeps last 120 snapshots (~30 days), then auto-prunes.
# Backs up the database only; TTS audio in data/tts/ is intentionally NOT backed up.

DB="/Users/daniel/Documents/AnkiAdvanced/data/srs.db"
BACKUP_DIR="/Users/daniel/Documents/AnkiAdvanced/data/backups"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
DEST="$BACKUP_DIR/srs_$TIMESTAMP.db"

# SQLite online backup (safe even while the server is running)
sqlite3 "$DB" ".backup '$DEST'"

echo "[$(date)] Backed up to $DEST"

# Keep only the 120 most recent backups (~30 days at one snapshot per 6h)
ls -t "$BACKUP_DIR"/srs_*.db | tail -n +121 | xargs rm -f

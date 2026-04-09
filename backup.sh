#!/bin/bash
# Daily SQLite backup — keeps last 30 snapshots, then auto-prunes.

DB="/Users/daniel/Documents/AnkiAdvanced/data/srs.db"
BACKUP_DIR="/Users/daniel/Documents/AnkiAdvanced/data/backups"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
DEST="$BACKUP_DIR/srs_$TIMESTAMP.db"

# SQLite online backup (safe even while the server is running)
sqlite3 "$DB" ".backup '$DEST'"

echo "[$(date)] Backed up to $DEST"

# Keep only the 30 most recent backups
ls -t "$BACKUP_DIR"/srs_*.db | tail -n +31 | xargs rm -f

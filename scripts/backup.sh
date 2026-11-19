#!/usr/bin/env bash
# Backs up KAIROS_DATABASE_URL to a timestamped file under KAIROS_BACKUP_DIR
# (default ./backups). Dialect-aware: SQLite uses `sqlite3 .backup` (a
# consistent live snapshot via SQLite's own backup API, not `cp`, which can
# capture a torn write mid-transaction); Postgres uses `pg_dump --format=custom`
# (pg_restore's preferred format — compressed, supports selective/parallel
# restore, unlike a plain SQL dump).
#
# Usage: ./scripts/backup.sh
#   KAIROS_DATABASE_URL   defaults to sqlite:///./.kairos/kairos.db
#   KAIROS_BACKUP_DIR     defaults to ./backups
set -euo pipefail

DATABASE_URL="${KAIROS_DATABASE_URL:-sqlite:///./.kairos/kairos.db}"
BACKUP_DIR="${KAIROS_BACKUP_DIR:-./backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

if [[ "$DATABASE_URL" == sqlite:///* ]]; then
    DB_PATH="${DATABASE_URL#sqlite:///}"
    if [[ ! -f "$DB_PATH" ]]; then
        echo "error: sqlite database not found at $DB_PATH" >&2
        exit 1
    fi
    OUT="$BACKUP_DIR/kairos-${TIMESTAMP}.sqlite"
    sqlite3 "$DB_PATH" ".backup '$OUT'"
    echo "$OUT"

elif [[ "$DATABASE_URL" == postgresql://* || "$DATABASE_URL" == postgres://* ]]; then
    command -v pg_dump >/dev/null || { echo "error: pg_dump not found on PATH" >&2; exit 1; }
    OUT="$BACKUP_DIR/kairos-${TIMESTAMP}.dump"
    pg_dump --format=custom --file="$OUT" "$DATABASE_URL"
    echo "$OUT"

else
    echo "error: unsupported KAIROS_DATABASE_URL scheme: $DATABASE_URL" >&2
    echo "  (only sqlite:/// and postgresql:// are supported)" >&2
    exit 1
fi

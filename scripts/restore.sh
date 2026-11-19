#!/usr/bin/env bash
# Restores a backup produced by scripts/backup.sh into KAIROS_DATABASE_URL.
# Destructive by nature (replaces the target database's contents) — prompts
# for confirmation unless -y/--yes is passed, same guard for both dialects.
#
# Usage: ./scripts/restore.sh <backup-file> [-y|--yes]
set -euo pipefail

BACKUP_FILE="${1:-}"
CONFIRMED="false"
for arg in "$@"; do
    [[ "$arg" == "-y" || "$arg" == "--yes" ]] && CONFIRMED="true"
done

if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
    echo "usage: $0 <backup-file> [-y|--yes]" >&2
    exit 1
fi

DATABASE_URL="${KAIROS_DATABASE_URL:-sqlite:///./.kairos/kairos.db}"

if [[ "$CONFIRMED" != "true" ]]; then
    read -r -p "This will OVERWRITE the database at '$DATABASE_URL' with '$BACKUP_FILE'. Continue? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || { echo "aborted"; exit 1; }
fi

if [[ "$DATABASE_URL" == sqlite:///* ]]; then
    DB_PATH="${DATABASE_URL#sqlite:///}"
    # Same-filesystem atomic swap: restore into a temp file first so a
    # truncated/failed copy never leaves the live DB half-overwritten.
    cp "$BACKUP_FILE" "${DB_PATH}.restoring"
    mv "${DB_PATH}.restoring" "$DB_PATH"
    echo "restored $DB_PATH from $BACKUP_FILE"

elif [[ "$DATABASE_URL" == postgresql://* || "$DATABASE_URL" == postgres://* ]]; then
    command -v pg_restore >/dev/null || { echo "error: pg_restore not found on PATH" >&2; exit 1; }
    # --clean --if-exists: drops existing objects before recreating them,
    # so this is a full replace, not a merge — matches the SQLite path's
    # semantics (whole-file overwrite) and avoids leftover rows a restore
    # onto a non-empty database would otherwise silently keep.
    pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" "$BACKUP_FILE"
    echo "restored $DATABASE_URL from $BACKUP_FILE"

else
    echo "error: unsupported KAIROS_DATABASE_URL scheme: $DATABASE_URL" >&2
    exit 1
fi

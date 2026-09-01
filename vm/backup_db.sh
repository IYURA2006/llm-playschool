#!/usr/bin/env bash
# Nightly pg_dump of the study database. Installed into cron by setup_vm.sh.
# pg_dump takes a consistent snapshot in one transaction, so it is safe to run
# while annotators are working.
set -euo pipefail

APP_DIR=${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
OUT=${BACKUP_DIR:-$HOME/annotation-backups}
KEEP_DAYS=${KEEP_DAYS:-30}

# Credentials come from the app's own .env — one place to rotate them.
[ -f "$APP_DIR/.env" ] || { echo "no .env at $APP_DIR" >&2; exit 1; }
set -a; . "$APP_DIR/.env"; set +a

mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M)
FILE="$OUT/study-$STAMP.sql.gz"

PGPASSWORD="$DB_PASSWORD" pg_dump \
    --host="$DB_HOST" --port="${DB_PORT:-5432}" \
    --username="$DB_USER" --dbname="$DB_NAME" \
    --no-owner --no-privileges \
    | gzip > "$FILE"

# A pg_dump that fails partway still leaves a gzip file, so a nearly empty
# result means the backup did not happen. Say so, rather than deleting a good
# older snapshot in favour of a broken new one.
if [ "$(stat -f%z "$FILE" 2>/dev/null || stat -c%s "$FILE")" -lt 1024 ]; then
    echo "backup $FILE is suspiciously small — not pruning old snapshots" >&2
    exit 1
fi

echo "$(date -Iseconds) wrote $FILE"
find "$OUT" -name 'study-*.sql.gz' -mtime "+$KEEP_DAYS" -delete

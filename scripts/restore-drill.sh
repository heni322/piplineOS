#!/bin/sh
# Backup -> restore into a fresh database -> compare every table's row count with the source.
# Usage: ADMIN_URL=... SOURCE_DB=pipelineos BACKUP_PASSPHRASE_FILE=... [BACKUP_DIR=/tmp/drill] scripts/restore-drill.sh
set -eu
: "${ADMIN_URL:?}" "${SOURCE_DB:?}" "${BACKUP_PASSPHRASE_FILE:?}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="${ADMIN_URL%/*}"
export BACKUP_DIR="${BACKUP_DIR:-$(mktemp -d)}"
export DATABASE_BACKUP_URL="${DATABASE_BACKUP_URL:-$BASE/$SOURCE_DB}"
sh "$HERE/backup.sh"
FILE="$(ls -t "$BACKUP_DIR"/pipelineos-*.dump.enc | head -1)"
TARGET="drill_$(date -u +%Y%m%d%H%M%S)"
sh "$HERE/restore.sh" "$FILE" "$TARGET"
COUNTS="SELECT string_agg(t || '=' || n, ' ' ORDER BY t) FROM (SELECT c.relname AS t, (xpath('/row/n/text()',
  query_to_xml(format('SELECT count(*) AS n FROM public.%I', c.relname), false, true, '')))[1]::text AS n
  FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace WHERE s.nspname = 'public' AND c.relkind = 'r') x"
A="$(psql "$BASE/$SOURCE_DB" -tAc "$COUNTS")"
B="$(psql "$BASE/$TARGET" -tAc "$COUNTS")"
if [ "$A" = "$B" ]; then echo "RESTORE DRILL OK: $(echo "$A" | wc -w) tables match ($TARGET kept; drop it when done)";
else echo "RESTORE DRILL MISMATCH"; echo "source:  $A"; echo "restore: $B"; exit 1; fi

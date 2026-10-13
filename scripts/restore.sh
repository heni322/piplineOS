#!/bin/sh
# Restores an encrypted backup into a NEW database (never over an existing one). Switching the app to it is a
# separate, deliberate step. Usage: ADMIN_URL=postgresql://postgres:...@host/postgres \
#   BACKUP_PASSPHRASE_FILE=... scripts/restore.sh backups/pipelineos-<stamp>.dump.enc <new_db_name>
set -eu
FILE="$1"; TARGET="$2"
: "${ADMIN_URL:?}" "${BACKUP_PASSPHRASE_FILE:?}"
case "$TARGET" in *[!a-z0-9_]*|"") echo "target must be [a-z0-9_]+"; exit 1;; esac
if [ -f "$FILE.sha256" ]; then sha256sum -c "$FILE.sha256"; fi
BASE="${ADMIN_URL%/*}"
EXISTS="$(psql "$ADMIN_URL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$TARGET'")"
[ -z "$EXISTS" ] || { echo "database $TARGET already exists; refusing to overwrite"; exit 1; }
psql "$ADMIN_URL" -q -c "CREATE DATABASE $TARGET OWNER pipelineos_owner"
psql "$BASE/$TARGET" -q -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
${BACKUPCRYPT_CMD:-python3 /opt/pipelineos/backupcrypt.py} decrypt --passphrase-file "$BACKUP_PASSPHRASE_FILE" < "$FILE" \
  | pg_restore --exit-on-error --no-password --dbname="$BASE/$TARGET"
echo "restored into $TARGET at schema $(psql "$BASE/$TARGET" -tAc 'SELECT version_num FROM alembic_version')"

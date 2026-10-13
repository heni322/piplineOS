#!/bin/sh
# Encrypted database backup. Needs: DATABASE_BACKUP_URL (role pipelineos_backup: read-only + BYPASSRLS),
# BACKUP_DIR, BACKUP_PASSPHRASE_FILE. Optional: BACKUP_UPLOAD_CMD (e.g. an rclone copy to UK object storage),
# RETENTION_DAYS (unset = keep every backup; old backups are only removed if you set it).
set -eu
: "${DATABASE_BACKUP_URL:?}" "${BACKUP_DIR:?}" "${BACKUP_PASSPHRASE_FILE:?}"
URL="$(echo "$DATABASE_BACKUP_URL" | sed 's#^postgresql+psycopg://#postgresql://#')"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/pipelineos-$STAMP.dump.enc"
mkdir -p "$BACKUP_DIR"
umask 077
pg_dump --format=custom --no-password --dbname="$URL" \
  | ${BACKUPCRYPT_CMD:-python3 /opt/pipelineos/backupcrypt.py} encrypt --passphrase-file "$BACKUP_PASSPHRASE_FILE" > "$OUT.partial"
mv "$OUT.partial" "$OUT"
sha256sum "$OUT" > "$OUT.sha256"
echo "backup written: $OUT ($(wc -c < "$OUT") bytes)"
if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then sh -c "$BACKUP_UPLOAD_CMD \"$OUT\" \"$OUT.sha256\""; fi
if [ -n "${RETENTION_DAYS:-}" ]; then
  find "$BACKUP_DIR" -name 'pipelineos-*.dump.enc*' -mtime "+$RETENTION_DAYS" -print -delete
fi

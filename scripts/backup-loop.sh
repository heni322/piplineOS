#!/bin/sh
# Runs scripts/backup.sh once a day at BACKUP_HOUR_UTC (default 02:00 UTC). Used by the `backup` service.
set -eu
HOUR="${BACKUP_HOUR_UTC:-2}"
while true; do
  now=$(date -u +%s); next=$(date -u -d "today ${HOUR}:00" +%s)
  [ "$next" -le "$now" ] && next=$((next + 86400))
  sleep $((next - now))
  sh /scripts/backup.sh || echo "BACKUP FAILED at $(date -u)" >&2
done

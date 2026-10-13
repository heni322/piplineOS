#!/bin/sh
# Production role setup (runs once, on an empty data directory). Passwords come from Docker secrets.
set -eu
OWNER_PW="$(cat "$PIPELINEOS_OWNER_PASSWORD_FILE")"
APP_PW="$(cat "$PIPELINEOS_APP_PASSWORD_FILE")"
psql -v ON_ERROR_STOP=1 --username postgres <<SQL
CREATE ROLE pipelineos_owner LOGIN PASSWORD '${OWNER_PW}' NOSUPERUSER NOCREATEROLE;
CREATE ROLE pipelineos_app LOGIN PASSWORD '${APP_PW}' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE pipelineos OWNER pipelineos_owner;
\connect pipelineos
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
REVOKE ALL ON DATABASE pipelineos FROM PUBLIC;
GRANT CONNECT ON DATABASE pipelineos TO pipelineos_app;
SQL
# Backups: a read-only role that can see every row (pg_dump can't run under forced row-level security).
BACKUP_PW="$(cat "${PIPELINEOS_BACKUP_PASSWORD_FILE:-/dev/null}" 2>/dev/null || true)"
if [ -n "$BACKUP_PW" ]; then
psql -v ON_ERROR_STOP=1 --username postgres <<SQL
CREATE ROLE pipelineos_backup LOGIN PASSWORD '${BACKUP_PW}' BYPASSRLS NOSUPERUSER NOCREATEROLE;
GRANT pg_read_all_data TO pipelineos_backup;
GRANT CONNECT ON DATABASE pipelineos TO pipelineos_backup;
SQL
fi

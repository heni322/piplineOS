#!/bin/bash
# Claude Code cloud sessions: a local PostgreSQL 16 + pgvector on port 5433 (as docker-compose gives on a PC),
# with the two PipelineOS roles. Idempotent: safe to run again whenever the server has stopped.
set -euo pipefail
DATA=/var/lib/postgresql/pipelineos-test
BIN=/usr/lib/postgresql/16/bin
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f /usr/share/postgresql/16/extension/vector.control ]; then
  apt-get install -y -q postgresql-16-pgvector >/dev/null 2>&1 \
    || { apt-get update -q >/dev/null 2>&1 && apt-get install -y -q postgresql-16-pgvector >/dev/null; }
fi
if [ ! -f "$DATA/PG_VERSION" ]; then
  install -d -o postgres -g postgres "$DATA"
  su postgres -c "$BIN/initdb -D $DATA -A trust" >/dev/null
fi
if ! su postgres -c "$BIN/pg_ctl -D $DATA status" >/dev/null 2>&1; then
  su postgres -c "$BIN/pg_ctl -D $DATA -o '-p 5433 -k /tmp' -l $DATA/server.log -w start" >/dev/null
fi
if ! psql -h 127.0.0.1 -p 5433 -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'pipelineos_app'" | grep -q 1; then
  psql -h 127.0.0.1 -p 5433 -U postgres -q -f "$ROOT/docker/postgres/01-roles.sql" >/dev/null
fi
echo "PostgreSQL ready on 127.0.0.1:5433"

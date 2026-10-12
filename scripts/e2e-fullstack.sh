#!/usr/bin/env bash
# Full-stack browser test: fresh database -> migrate -> seed + run the demo pipeline -> API + web -> Playwright.
# Everything is synthetic and dry-run. Requires a local PostgreSQL 16 (+pgvector) superuser at $TEST_PG_ADMIN_URL.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADMIN="${TEST_PG_ADMIN_URL:-postgresql://postgres@127.0.0.1:5433/postgres}"
HOSTPORT="$(echo "$ADMIN" | sed -E 's#.*@([^/]+)/.*#\1#')"
DB=pipelineos_e2e
psql "$ADMIN" -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB OWNER pipelineos_owner"
psql "${ADMIN%/*}/$DB" -q -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
cd "$ROOT/backend"
export ENVIRONMENT=dev LOG_LEVEL=WARNING
export DATABASE_URL="postgresql+psycopg://pipelineos_app:app_dev_only@$HOSTPORT/$DB"
export DATABASE_OWNER_URL="postgresql+psycopg://pipelineos_owner:owner_dev_only@$HOSTPORT/$DB"
eval "$(uv run pipelineos keys generate | sed 's/^/export /')"
export API_BASE_URL=http://localhost:8100 PUBLIC_BASE_URL=http://localhost:3100
uv run alembic upgrade head >/dev/null
CREDS="$(uv run pipelineos demo seed 2>/dev/null)"
uv run pipelineos demo run >/dev/null
export E2E_EMAIL="$(echo "$CREDS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["operator_email"])')"
export E2E_PASSWORD="$(echo "$CREDS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["operator_password"])')"
export E2E_TOTP_SECRET="$(echo "$CREDS" | python3 -c 'import json,sys,urllib.parse as u; q=u.urlparse(json.load(sys.stdin)["operator_totp_uri"]).query; print(u.parse_qs(q)["secret"][0])')"
uv run uvicorn app.main:app --port 8100 --log-level warning & API_PID=$!
trap 'kill $API_PID $WEB_PID 2>/dev/null || true' EXIT
cd "$ROOT/frontend"
API_BASE_URL=http://localhost:8100 pnpm build >/dev/null
pnpm exec next start -p 3100 >/dev/null 2>&1 & WEB_PID=$!
for _ in $(seq 1 60); do curl -sf http://localhost:3100/login >/dev/null && curl -sf http://localhost:8100/api/v1/health >/dev/null && break; sleep 1; done
pnpm exec playwright test --reporter=line

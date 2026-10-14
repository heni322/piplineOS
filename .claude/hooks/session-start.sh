#!/bin/bash
# Cloud sessions only: everything `scripts/ci.sh` needs, so tests and linters run straight away.
set -euo pipefail
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR"

bash scripts/cloud-postgres.sh
(cd backend && uv sync --frozen)
(cd frontend && pnpm install --frozen-lockfile --prefer-offline)

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo 'export TEST_PG_ADMIN_URL="postgresql://postgres@127.0.0.1:5433/postgres"'
    if [ -x /opt/pw-browsers/chromium ]; then echo 'export PW_CHROMIUM_PATH="/opt/pw-browsers/chromium"'; fi
  } >> "$CLAUDE_ENV_FILE"
fi

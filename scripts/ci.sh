#!/usr/bin/env bash
# Full local CI: lint, types, import contracts, tests, dependency audits, secret scan.
# Needs: uv, pnpm, a PostgreSQL 16 with pgvector reachable at $TEST_PG_ADMIN_URL
# (default postgresql://postgres@127.0.0.1:5433/postgres — e.g. `docker compose up -d postgres`).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "== backend =="
cd "$ROOT/backend"
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run python -c "from app.llm.prompts import check_lock; import sys; p = check_lock(); print(*p, sep='\n'); sys.exit(1 if p else 0)"
uv run pytest -q
uv run pip-audit --progress-spinner off --skip-editable

echo "== frontend =="
cd "$ROOT/frontend"
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm e2e
# Accepted, documented exception: braces (dev-only, via eslint-config-next) has no patched release.
pnpm audit --audit-level critical

echo "== secrets =="
if command -v gitleaks >/dev/null; then gitleaks detect --source "$ROOT" --no-banner; else echo "gitleaks not installed (pre-commit hook runs it)"; fi
echo "CI OK"

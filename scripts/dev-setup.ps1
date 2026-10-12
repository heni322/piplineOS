# Windows developer setup for PipelineOS (run from the repository root in PowerShell).
# Requires Docker Desktop running, uv and pnpm. Everything runs in DRY-RUN mode with mock adapters.
$ErrorActionPreference = "Stop"
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Push-Location backend
  $keys = uv run pipelineos keys generate
  Pop-Location
  foreach ($line in $keys) {
    $name = $line.Split("=")[0]
    (Get-Content .env) -replace "^$name=.*", $line | Set-Content .env
  }
  Write-Host "Created .env with fresh keys (never commit it)."
}
docker compose up -d postgres valkey mailpit
Push-Location backend
uv sync
$env:DATABASE_OWNER_URL = "postgresql+psycopg://pipelineos_owner:owner_dev_only@localhost:5433/pipelineos"
uv run alembic upgrade head
uv run pipelineos config sync
Pop-Location
Push-Location frontend
pnpm install
Pop-Location
Write-Host "Ready. API: cd backend; uv run uvicorn app.main:app --reload   Web: cd frontend; pnpm dev"

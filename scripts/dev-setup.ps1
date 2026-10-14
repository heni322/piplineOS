# Windows developer setup for PipelineOS (run from the repository root in PowerShell).
# Requires Docker Desktop running, uv and pnpm. Everything runs in DRY-RUN mode with mock adapters.
#
# Native tools (docker, uv, pnpm) write progress to stderr. Windows PowerShell 5.1 turns that into errors under
# "Stop", so each step checks the tool's exit code instead.
$ErrorActionPreference = "Continue"

function Step([string]$what, [scriptblock]$run) {
  Write-Host "== $what"
  & $run
  if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $what (exit code $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
}

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Push-Location backend
  $keys = uv run pipelineos keys generate 2>$null
  $code = $LASTEXITCODE
  Pop-Location
  if ($code -ne 0) { Write-Host "FAILED: generating keys" -ForegroundColor Red; exit 1 }
  foreach ($line in $keys) {
    $name = $line.Split("=")[0]
    (Get-Content .env) -replace "^$name=.*", $line | Set-Content .env
  }
  Write-Host "Created .env with fresh keys (never commit it)."
}
Step "Start PostgreSQL, Valkey and Mailpit" { docker compose up -d --wait postgres valkey mailpit }
Push-Location backend
Step "Install backend packages" { uv sync }
$env:DATABASE_OWNER_URL = "postgresql+psycopg://pipelineos_owner:owner_dev_only@localhost:5433/pipelineos"
Step "Migrate the database" { uv run alembic upgrade head }
Step "Sync versioned configuration" { uv run pipelineos config sync }
Pop-Location
Push-Location frontend
Step "Install frontend packages" { pnpm install }
Pop-Location
Write-Host "Ready. API: cd backend; uv run uvicorn app.main:app --reload   Web: cd frontend; pnpm dev"
Write-Host "(The backend reads the .env in the repository root, so commands work from backend/ too.)"

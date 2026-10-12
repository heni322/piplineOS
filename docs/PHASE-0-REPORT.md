# Phase 0 — Investigation Report (2026-10-12)

- `C:\Users\heniz\Desktop\piplineOS` existed and was **empty** (0 items, not a git repo) → greenfield. Test baseline: **0 tests**.
- Environment: Windows 11 (26200), PowerShell 5.1, 15.8 GB RAM, 30.8 GB free on C:. Python 3.13.2 + uv 0.9.26; Node 22.14 + npm 10.9 + pnpm 10.28; Git 2.49 (user `heni322`); Docker 28.3.2 + Compose 2.39 installed but **engine not running**; WSL2 (Ubuntu, docker-desktop).
- Local PostgreSQL 17 Windows service on port 5432, no pgvector → PipelineOS uses its own PG16 + pgvector container on **5433**; local PG untouched.
- No provider credentials in environment → all adapters mock-only.

Risks raised: R1 Docker not running · R2 port 5432 · R3 disk · R4 cross-tenant global suppression (legal) · R5 sender identity vs AI disclosure · R6 unverified legal/tax facts · R7 Windows/Linux drift · R8 `linkedin_url` sourcing.

Open questions Q1–Q11 answered with the recommended defaults on 2026-10-12 (user instruction "start doing the project"). See PHASE-1-REPORT.md §3.

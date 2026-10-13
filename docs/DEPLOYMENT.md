# Deployment (UK hosting)

Everything here keeps PipelineOS in dry-run. Real sending still needs, on top of a running stack:
- `EMAIL_ADAPTER_MODE=live`
- a client in live mode
- `GLOBAL_GO_LIVE=true`
- the go-live checklist signed off by the owner

## 1. Hosting options (UK region), prices read 2026-10-13

List prices in USD, excluding VAT, from the providers' own pages. They change: re-check before buying.

| Option | What you run | Monthly (list) | Backups | Notes |
|---|---|---|---|---|
| **A. One VM** (recommended to start) | DigitalOcean London, Basic 8 GiB / 4 vCPU / 160 GiB ($48) **or** AWS Lightsail London, 8 GB / 2 vCPU / 160 GB ($44). Everything in `docker-compose.prod.yml` | ≈ $44–48 + object storage for off-site backups | Ours: encrypted nightly `pg_dump`, tested restore (§4) | Cheapest; you own the database operations. Fine for the first clients |
| **B. VM + managed PostgreSQL** | DigitalOcean Basic 4 GiB ($24) + Managed PostgreSQL 2 vCPU / 4 GiB ($60.90 + storage) | ≈ $85–95 | Provider-managed (confirm point-in-time recovery and pgvector before choosing) | Less database work; check the managed service allows our two roles and forced row-level security |
| **C. AWS Lightsail + Lightsail database** | 4 GB instance ($24) + 4 GB database ($60 standard / $120 high availability) | ≈ $84–144 | Provider-managed | Confirm pgvector support on the Lightsail database before choosing |

Sources:
- [DigitalOcean Droplet pricing](https://www.digitalocean.com/pricing/droplets)
- [DigitalOcean Managed Database pricing](https://www.digitalocean.com/pricing/managed-databases)
- [AWS Lightsail pricing](https://aws.amazon.com/lightsail/pricing/)

Neither provider's pricing page shows London-specific instance prices; the figures above are their standard list prices.

**Recommendation:** start with option A in a London region. Move the database to option B when the first paying clients justify the extra cost. Either way the data stays in the UK, which the brief requires.

## 2. One-time server setup (option A)
1. Create the VM in London, Ubuntu 24.04 LTS, SSH keys only, and a firewall allowing 22 (your IP), 80 and 443.
2. Install Docker Engine and the compose plugin. Enable unattended security upgrades.
3. Clone the repository to `/srv/pipelineos`. Create `secrets/`, `chmod 700`, containing one file per secret:
   - `postgres_superuser`
   - `pipelineos_owner`
   - `pipelineos_app`
   - `pipelineos_backup`
   - `backup_passphrase` (≥ 20 characters)
4. Copy `.env.prod.example` to `.env.prod` (`chmod 600`) and fill it.
   - Generate the four application keys with `pipelineos keys generate`.
   - **Store the keys and the backup passphrase offline, in two places, separate from the backups.** Without `PII_MASTER_KEY` a backup can't be read. `SUPPRESSION_HASH_KEY` must never change.
5. Point the DNS `A` record of `APP_DOMAIN` at the VM.
6. Build and start the stack. Caddy obtains the TLS certificate automatically:
   ```sh
   docker compose -f docker-compose.prod.yml build
   docker compose -f docker-compose.prod.yml up -d
   ```
7. Check `https://APP_DOMAIN/api/v1/ready`. It should return `{"ready": true, ...}`.
8. Create your operator account (MFA is mandatory):
   ```sh
   docker compose -f docker-compose.prod.yml run --rm api pipelineos users create-operator you@yourcompany.co.uk
   ```

## 3. Monitoring
- Prometheus scrapes `http://api:8000/metrics` on the internal network with `Authorization: Bearer $METRICS_TOKEN`. Caddy never serves `/metrics` publicly.
- Load the alert rules from `ops/prometheus/alerts.yml`. They cover:
  - go-live switched on
  - live sends without go-live
  - a paused inbox
  - overdue or soon-due data-protection requests
  - the VAT threshold
  - reply SLA
  - a stuck outbox
  - AI failures
- Uptime check: `GET /api/v1/ready` every minute from outside the VM.
- Logs: JSON on stdout (Docker rotates at 20 MB × 10). They contain no personal data by design.

## 4. Backups and restore
- The `backup` service runs `scripts/backup.sh` daily at 02:00 UTC:
  - `pg_dump` as the read-only `pipelineos_backup` role (it needs BYPASSRLS because every table has forced row-level security)
  - AES-256-GCM encryption (`backend/app/ops/backupcrypt.py`)
  - a SHA-256 file
- Off-site copy: set `BACKUP_UPLOAD_CMD`, e.g. an `rclone copy` to UK object storage.
- Old backups are only deleted if you set `RETENTION_DAYS`. The default keeps everything.
- **Restore** never overwrites. It restores into a new database; switching the app over is a separate, deliberate step:
  ```sh
  ADMIN_URL=... BACKUP_PASSPHRASE_FILE=... scripts/restore.sh <file> <new_db>
  ```
- **Restore drill:** `scripts/restore-drill.sh` backs up, restores into a fresh database and compares every table's row count. Run it monthly and after every schema migration.
  - Phase 6 run: 52 tables matched.
  - All encrypted prospect emails decrypted correctly from the restored copy.

## 5. CI
`.github/workflows/ci.yml` runs on every push:
- backend: lint, types, import contracts, prompt lock, tests, `pip-audit`
- frontend: lint, types, tests, build, audit
- the full-stack browser tests
- security: gitleaks over the full history, and Trivy scans of the images and configuration

`.github/dependabot.yml` opens weekly update pull requests. Once the repository is on GitHub, pin each action to a commit SHA.

## 6. Upgrades
1. `git pull`.
2. Rebuild with a new `VERSION`.
3. `docker compose -f docker-compose.prod.yml up -d`. The `migrate` service applies migrations before the API starts.
4. Every migration has a tested downgrade. Downgrades refuse rather than delete data.

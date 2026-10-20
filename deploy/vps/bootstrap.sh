#!/usr/bin/env bash
# One-time setup of PipelineOS secrets and configuration ON THE SERVER (voyz.fr, shared Contabo VPS).
#
#   cd /srv/pipelineos && bash deploy/vps/bootstrap.sh
#
# - Generates every password and key here, on the server, with openssl: they never travel through a chat, an email
#   or the repository. Writes ./secrets/* (chmod 600) and ./.env.prod (chmod 600).
# - Asks for YOUR API keys (Gemini, Google Places, Companies House) without echoing them. Press Enter to skip one
#   and add it later in .env.prod.
# - Prints the keys you must store OFFLINE (password manager + a second place), once.
# - Refuses to run twice: re-generating would make the database and every encrypted field unreadable, and the
#   suppression list must never change key. To start again on an EMPTY server, remove .env.prod and secrets/ yourself.
#
# Everything stays in dry-run: nothing is sent, booked, written to a CRM or paid for until the three live keys are
# on and you have signed the go-live checklist.
set -euo pipefail
umask 077

DOMAIN="${DOMAIN:-voyz.fr}"
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

if [ -e .env.prod ] || [ -e secrets/pipelineos_app ]; then
  echo "Refusing: .env.prod or secrets/ already exist in $ROOT. Secrets are generated once; never overwrite them." >&2
  exit 1
fi
command -v openssl >/dev/null || { echo "openssl is required (apt install openssl)" >&2; exit 1; }

pw() { openssl rand -hex 24; }  # 192 bits, URL-safe (goes into database URLs)
key32() { openssl rand 32 | base64 | tr '+/' '-_' | tr -d '\n'; }   # urlsafe base64 of 32 random bytes

ask() {  # ask VAR "label" [secret]
  local __v
  if [ "${3:-}" = secret ]; then read -rsp "$2 (Enter to skip): " __v; echo; else read -rp "$2: " __v; fi
  printf -v "$1" '%s' "$__v"
}

echo "PipelineOS setup for https://$DOMAIN"
ask ADMIN_EMAIL "Your email (operator login, alerts; not published)"
ask GEMINI_API_KEY "Gemini API key (paid tier only; used only if you set LLM_PROVIDER=gemini after go-live)" secret
ask GOOGLE_PLACES_API_KEY "Google Places API key (restrict it to the Places API in Google Cloud)" secret
ask COMPANIES_HOUSE_API_KEY "Companies House API key (free, UK register)" secret

PG_SUPER=$(pw); PG_OWNER=$(pw); PG_APP=$(pw); PG_BACKUP=$(pw); VALKEY=$(pw)
BACKUP_PASSPHRASE=$(pw)
PII=$(key32); BIDX=$(key32); SUPP=$(key32); UNSUB=$(key32); METRICS=$(pw)

mkdir -p secrets backups
chmod 700 secrets
printf '%s' "$PG_SUPER" > secrets/postgres_superuser
printf '%s' "$PG_OWNER" > secrets/pipelineos_owner
printf '%s' "$PG_APP" > secrets/pipelineos_app
printf '%s' "$PG_BACKUP" > secrets/pipelineos_backup
printf '%s' "$BACKUP_PASSPHRASE" > secrets/backup_passphrase
# Docker mounts these files as they are on disk. The postgres and backup images read them as the postgres user
# (uid 999), so they belong to that uid and nobody else can read them.
chown 999:999 secrets/*
chmod 400 secrets/*

cp .env.prod.example .env.prod
chmod 600 .env.prod
set_kv() {  # replace KEY=... (or append it); values never pass through sed
  python3 - "$1" "$2" <<'PY'
import sys
k, v = sys.argv[1], sys.argv[2]
lines = open(".env.prod", encoding="utf-8").read().splitlines()
out, done = [], False
for line in lines:
    if line.split("=", 1)[0] == k and not line.startswith("#"):
        if not done:
            out.append(f"{k}={v}")
            done = True
        continue
    out.append(line)
if not done:
    out.append(f"{k}={v}")
open(".env.prod", "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
}

set_kv VERSION "vps-$(date +%Y%m%d)"
set_kv REGISTRY pipelineos
set_kv APP_DOMAIN "$DOMAIN"
set_kv ACME_EMAIL "${ADMIN_EMAIL:-admin@$DOMAIN}"
set_kv PUBLIC_BASE_URL "https://$DOMAIN"
set_kv API_BASE_URL "https://$DOMAIN"
set_kv CRAWLER_CONTACT "https://$DOMAIN"
set_kv DATABASE_URL "postgresql+psycopg://pipelineos_app:$PG_APP@postgres:5432/pipelineos"
set_kv DATABASE_OWNER_URL "postgresql+psycopg://pipelineos_owner:$PG_OWNER@postgres:5432/pipelineos"
set_kv DATABASE_BACKUP_URL "postgresql://pipelineos_backup:$PG_BACKUP@postgres:5432/pipelineos"
set_kv VALKEY_PASSWORD "$VALKEY"
set_kv REDIS_URL "redis://:$VALKEY@valkey:6379/0"
set_kv PII_MASTER_KEY "$PII"
set_kv BLIND_INDEX_KEY "$BIDX"
set_kv SUPPRESSION_HASH_KEY "$SUPP"
set_kv UNSUBSCRIBE_SIGNING_KEY "$UNSUB"
set_kv METRICS_TOKEN "$METRICS"
set_kv BACKUP_HOST_DIR "$ROOT/backups"
set_kv SYSTEM_MAIL_FROM "no-reply@$DOMAIN"
set_kv SYSTEM_MAIL_NAME "Voyz"
set_kv NEXT_PUBLIC_LOCALE fr-FR  # French interface on voyz.fr
set_kv NEXT_PUBLIC_TIME_ZONE Europe/Paris
set_kv GLOBAL_GO_LIVE false
# Providers: the free register finds decision-makers; the free local model does the AI work; Google Places (paid)
# is switched on but stays behind the three live keys and a monthly cap at Google's free allowance.
set_kv FREE_ONLY false
set_kv ENRICHMENT_PROVIDER companies_house
set_kv LLM_PROVIDER local
set_kv LLM_ADAPTER_MODE sandbox
set_kv LOCAL_LLM_URL http://ollama:11434
set_kv LOCAL_LLM_MODEL gemma4:12b
set_kv OLLAMA_MEM_LIMIT 10g
set_kv OLLAMA_CPUS 6
set_kv GEMINI_API_KEY "$GEMINI_API_KEY"
set_kv GEMINI_SEARCH_MODE live  # still behind the three live keys: nothing is called before go-live
set_kv GOOGLE_PLACES_API_KEY "$GOOGLE_PLACES_API_KEY"
set_kv PLACES_ADAPTER_MODE live
set_kv PLACES_MONTHLY_CAP 1000
set_kv COMPANIES_HOUSE_API_KEY "$COMPANIES_HOUSE_API_KEY"
unset GEMINI_API_KEY GOOGLE_PLACES_API_KEY COMPANIES_HOUSE_API_KEY

cat <<EOF

Done. Written: $ROOT/.env.prod and $ROOT/secrets/* (readable by root only).

==================== STORE THESE OFFLINE NOW (shown once) ====================
Put them in your password manager AND a second place (printed, or an encrypted USB key), never next to the
backups. Without PII_MASTER_KEY and the backup passphrase, backups cannot be read. SUPPRESSION_HASH_KEY must never
change. Database passwords are recoverable from this server, but keep them too.

PII_MASTER_KEY=$PII
BLIND_INDEX_KEY=$BIDX
SUPPRESSION_HASH_KEY=$SUPP
UNSUBSCRIBE_SIGNING_KEY=$UNSUB
BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE
POSTGRES_SUPERUSER_PASSWORD=$PG_SUPER
PIPELINEOS_OWNER_PASSWORD=$PG_OWNER
PIPELINEOS_APP_PASSWORD=$PG_APP
PIPELINEOS_BACKUP_PASSWORD=$PG_BACKUP
VALKEY_PASSWORD=$VALKEY
METRICS_TOKEN=$METRICS
===============================================================================
Then clear this terminal (Ctrl+L, and clear the scrollback in your SSH client).
Next: docs/DEPLOY-VPS.md, step 4.
EOF

# Backup/restore image: the PostgreSQL 16 client tools (matching the server) plus the backup encryptor.
FROM postgres:18-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-cryptography \
    && rm -rf /var/lib/apt/lists/*
COPY backend/app/ops/backupcrypt.py /opt/pipelineos/backupcrypt.py
COPY scripts/backup.sh scripts/backup-loop.sh scripts/restore.sh /scripts/
USER postgres
ENTRYPOINT []

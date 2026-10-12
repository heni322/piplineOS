-- Local development only. Creates the two database roles used by PipelineOS.
-- pipelineos_owner: owns tables, runs migrations. pipelineos_app: runtime role, NO BYPASSRLS, not owner.
-- Passwords here are for the local Docker network only; production uses the secret manager.
CREATE ROLE pipelineos_owner LOGIN PASSWORD 'owner_dev_only' NOSUPERUSER NOCREATEROLE;
CREATE ROLE pipelineos_app   LOGIN PASSWORD 'app_dev_only'   NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE pipelineos OWNER pipelineos_owner;
\connect pipelineos
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
GRANT CONNECT ON DATABASE pipelineos TO pipelineos_app;

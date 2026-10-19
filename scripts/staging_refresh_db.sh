#!/usr/bin/env bash
# Copy the live database into staging (replaces staging's data), then cut staging off from the real channels:
# channel tokens are removed and every channel is disabled, and monitoring alerts are switched off, so nothing in
# staging can message a guest or page the team. Run from the project folder: bash scripts/staging_refresh_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."

LIVE="docker compose"
STAGING="docker compose -f docker-compose.staging.yml"

$STAGING up -d postgres redis
until $STAGING exec -T postgres pg_isready -U app -d assistant >/dev/null 2>&1; do sleep 2; done
$STAGING stop backend >/dev/null 2>&1 || true

$STAGING exec -T postgres psql -v ON_ERROR_STOP=1 -U app -d postgres -q \
  -c "DROP DATABASE IF EXISTS assistant WITH (FORCE)" -c "CREATE DATABASE assistant"
$LIVE exec -T postgres pg_dump -U app -d assistant -Fc \
  | $STAGING exec -T postgres pg_restore -U app -d assistant --no-owner --no-privileges --exit-on-error

$STAGING exec -T postgres psql -v ON_ERROR_STOP=1 -U app -d assistant -q <<'SQL'
UPDATE channel_configs SET enabled = false, access_token_enc = NULL, app_secret_enc = NULL,
       status = 'staging', status_detail = 'disabled in staging';
DELETE FROM app_settings WHERE key LIKE 'alert%' OR key LIKE 'monitor%';
SQL

$STAGING up -d backend frontend
echo "staging ready: http://localhost:3001 (live data copied, channels cut off)"

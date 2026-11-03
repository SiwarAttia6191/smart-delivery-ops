#!/usr/bin/env bash
# Server-side bootstrap for the public demo (decision 0004, phase B).
# Run ONCE on a fresh ~2 GB VPS as a sudo-capable user:
#
#   ./deploy/deploy_demo.sh [git-url]
#
# Idempotent: re-running brings the stack to the same state (docker install
# skips if present, secrets skip if present, compose up reconciles).
# What it does NOT do: DNS (point the domain at this box first), and the
# VPS-specific PostgreSQL/OSRM sizing lives in docker-compose.demo.yml.
set -euo pipefail

REPO_URL="${1:-https://github.com/SiwarAttia6191/smart-delivery-ops.git}"
APP_DIR="${APP_DIR:-/opt/smart-delivery-ops}"
DEMO_DOMAIN="${DEMO_DOMAIN:-}"

echo "── 1/6 docker engine"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi
docker info >/dev/null

echo "── 2/6 repository at ${APP_DIR}"
if [ ! -d "${APP_DIR}/.git" ]; then
    git clone "${REPO_URL}" "${APP_DIR}"
fi
cd "${APP_DIR}"
git pull --ff-only origin main

echo "── 3/6 secrets (skips existing)"
python3 scripts/ensure_secrets.py || .venv/bin/python scripts/ensure_secrets.py 2>/dev/null \
    || docker compose -f docker-compose.demo.yml run --rm --entrypoint python api scripts/ensure_secrets.py

echo "── 4/6 OSRM road network (~100 MB download + prepare, skips if prepared)"
if [ ! -f "data/osrm/${OSRM_EXTRACT_ID:-tunisia}-latest.osrm.cell_metrics" ]; then
    python3 scripts/fetch_osrm_extract.py "${OSRM_EXTRACT_ID:-tunisia}"
fi

echo "── 5/6 demo unit up (migrate bootstraps the sdo_app role)"
docker compose -f docker-compose.demo.yml up -d --build

echo "── 6/6 seed + wait for the api"
for i in $(seq 1 30); do
    sleep 5
    curl -fsS -m 3 localhost:"${DEMO_API_PORT:-8010}/health" >/dev/null 2>&1 && break
done
docker compose -f docker-compose.demo.yml run --rm api python scripts/seed_demo.py

if [ -n "${DEMO_DOMAIN}" ]; then
    echo "── TLS front (Caddy override)"
    DEMO_DOMAIN="${DEMO_DOMAIN}" docker compose \
        -f docker-compose.demo.yml -f deploy/caddy.override.yml up -d
    echo "demo live at https://${DEMO_DOMAIN}/storefront/ordering-demo.html"
else
    echo "DEMO_DOMAIN not set — the api answers on :${DEMO_API_PORT:-8010} without TLS."
fi

echo "── nightly reset (03:30, phase D):"
echo "   30 3 * * * cd ${APP_DIR} && docker compose -f docker-compose.demo.yml run --rm api python scripts/reset_demo.py"

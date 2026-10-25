#!/usr/bin/env bash
# Run the API locally with auto-reload (Swagger UI at /docs).
# Override the port with: API_PORT=9000 ./scripts/run_local.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate
python -m uvicorn apps.api.main:app --reload --port "${API_PORT:-8010}"

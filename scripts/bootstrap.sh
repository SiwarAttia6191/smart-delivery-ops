#!/usr/bin/env bash
# One-shot local bootstrap: venv + deps + seed + hint to run the API.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m venv .venv
source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements-dev.txt --quiet
python scripts/seed_demo_data.py

echo
echo "Bootstrap complete. Start the API with:"
echo "  make api   (then open http://127.0.0.1:8000/docs)"

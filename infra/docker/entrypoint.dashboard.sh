#!/bin/sh
# Dashboard entrypoint (#27): assemble the read-only DB URLs from the
# mounted Docker secret (no plaintext credentials in compose config).
set -e
PW=$(cat /run/secrets/db_password | tr -d '\r\n ')
export WAREHOUSE_DATABASE_URL="postgresql+psycopg://smartops:${PW}@postgres:5432/smart_delivery_warehouse"
export OPS_DATABASE_URL="postgresql+psycopg://smartops:${PW}@postgres:5432/smart_delivery_ops"
exec streamlit run /dash/app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true

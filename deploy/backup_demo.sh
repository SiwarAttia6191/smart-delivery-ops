#!/usr/bin/env bash
# Demo-box backup (#193, decision 0004): a plain pg_dump of the demo
# database, timestamped, keeping the last 7. Cron line (03:10 daily):
#   10 3 * * * cd /opt/smart-delivery-ops && ./deploy/backup_demo.sh
# Restore drill (quarterly, documented in docs/runbook.md):
#   docker compose -f docker-compose.demo.yml exec -T postgres \
#     psql -U smartops -d smart_delivery_ops < backups/<file>.sql
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y%m%d_%H%M%S)
docker compose -f docker-compose.demo.yml exec -T postgres \
  pg_dump -U smartops -d smart_delivery_ops > "backups/demo_${STAMP}.sql"
ls -1t backups/demo_*.sql | tail -n +8 | xargs -r rm --
echo "backup written: backups/demo_${STAMP}.sql (keeping the last 7)"

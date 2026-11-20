#!/usr/bin/env bash
# Main-stack backup (#316, decision 0005): a plain pg_dump of the MAIN
# ops database — the one holding tenant data — timestamped, keeping the
# last 7. Cron line (03:20 daily, after the demo box's 03:10):
#   20 3 * * * cd /opt/smart-delivery-ops && ./deploy/backup_main.sh
# The restore drill (quarterly, worked and timed in docs/runbook.md →
# "Backups & the restore drill") restores the newest dump into a
# throwaway container and verifies row counts against the live database.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y%m%d_%H%M%S)
docker compose exec -T postgres \
  pg_dump -U smartops -d smart_delivery_ops > "backups/main_${STAMP}.sql"
ls -1t backups/main_*.sql | tail -n +8 | xargs -r rm --
echo "backup written: backups/main_${STAMP}.sql (keeping the last 7)"

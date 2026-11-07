"""Warehouse orchestration — the Airflow entry point for the data platform.

One idempotent daily DAG: rebuild the marts, then assert freshness. Both
tasks are safe to re-run and backfill (dbt tables are fully rebuilt each
run — no incremental state), which is exactly what makes Airflow's
catchup/backfill machinery safe to lean on.
"""

import logging
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator
from ml_alerts import alert_on_failure

DBT_DIR = "/opt/transform"

logger = logging.getLogger(__name__)

default_args = {
    "owner": "data-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
    "execution_timeout": timedelta(minutes=30),
    "on_failure_callback": alert_on_failure,
}

with DAG(
    dag_id="warehouse_daily_build",
    description="dbt build of the warehouse marts + source freshness check",
    # Timezone-aware on purpose: 03:00 means 03:00 in this zone, not the
    # container's interpretation of a naive datetime.
    start_date=pendulum.datetime(2026, 10, 24, tz="UTC"),
    schedule="0 3 * * *",  # 03:00 UTC daily, after the previous evening's orders
    catchup=False,
    default_args=default_args,
    tags=["warehouse", "dbt", "phase2"],
) as dag:
    # dbt's log/target dirs default into the bind-mounted project, which
    # belongs to another container's uid — redirect via env vars (there are
    # no --log-dir/--target-path CLI flags). The credential comes from the
    # mounted Docker secret (#28 secrets half); profiles.yml reads
    # DBT_PASSWORD, host runs fall back to its documented dev default.
    dbt_env = (
        "DBT_LOG_PATH=/tmp/dbt-logs DBT_TARGET_PATH=/tmp/dbt-target "
        "DBT_PASSWORD=$(cat /run/secrets/db_password | tr -d '\r') "
        "PGPASSWORD=$(cat /run/secrets/db_password | tr -d '\r') "
        # the scheduled lane IS production (#181): without this, profiles.yml's
        # default silently rebuilt analytics_draft every night while the
        # production `analytics` schema froze (2026-11-04 dashboard audit)
        "DBT_SCHEMA=analytics"
    )

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=(
            f"{dbt_env} dbt build --project-dir {DBT_DIR} --profiles-dir {DBT_DIR} --no-use-colors"
        ),
    )

    dbt_source_freshness = BashOperator(
        task_id="dbt_source_freshness",
        bash_command=(
            f"{dbt_env} dbt source freshness --project-dir {DBT_DIR} --profiles-dir {DBT_DIR} "
            "--no-use-colors"
        ),
    )

    # Mart freshness (#181): the source-freshness step covers RAW only —
    # the audit caught five silent days precisely because nothing checked
    # the MARTS. The check is mart-vs-raw LAG: the production summary
    # mart's max order_date must be within 2 days of the raw layer's max
    # ingested_at. A silently-wrong schema (analytics_draft instead of
    # analytics) or a failed build shows up as a growing lag; a missing
    # table/column shows up as an SQL error — empty psql output fails the
    # numeric test and the else branch exits 1. Both failure shapes fail
    # the DAG instead of reporting success, which is the whole point.
    marts_freshness = BashOperator(
        task_id="marts_freshness",
        bash_command=(
            # same secret-read convention as dbt_env: the scheduler has no
            # ambient PGPASSWORD, only the mounted Docker secret; EXPORT it
            # — the psql call below runs inside $() and unexported vars
            # don't cross into command substitutions
            'export PGPASSWORD=$(cat /run/secrets/db_password | tr -d "\r"); '
            "LAG_DAYS=$(psql -h postgres -U smartops -d smart_delivery_warehouse -tAc "
            '"SELECT COALESCE((SELECT max(ingested_at)::date FROM raw.order_events) '
            '- (SELECT max(order_date) FROM analytics.mart_daily_order_summary), 999)"); '
            'if [[ "$LAG_DAYS" =~ ^-?[0-9]+$ ]] && [ "$LAG_DAYS" -le 2 ]; then '
            'echo "marts fresh (lag ${LAG_DAYS}d)"; else '
            'echo "production marts STALE: analytics.mart_daily_order_summary lags raw '
            'by ${LAG_DAYS:-?} days (#181)"; exit 1; fi'
        ),
    )

    # Freshness only means something if the build just succeeded.
    dbt_build >> marts_freshness >> dbt_source_freshness

"""Warehouse orchestration — the Airflow entry point for the data platform.

One idempotent daily DAG: rebuild the marts, then assert freshness. Both
tasks are safe to re-run and backfill (dbt tables are fully rebuilt each
run — no incremental state), which is exactly what makes Airflow's
catchup/backfill machinery safe to lean on.
"""

import logging
import os
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator

DBT_DIR = "/opt/transform"

logger = logging.getLogger(__name__)


def _alert_on_failure(context) -> None:  # noqa: ANN001 - Airflow context dict
    """Failure surface for the pipeline (review finding 5): a DAG/task failure
    must leave the log-only boundary. POSTs to the webhook when configured;
    otherwise logs loudly so the gap is at least discoverable."""
    webhook = os.environ.get("AIRFLOW_ALERT_WEBHOOK")
    payload = {
        "dag": context.get("dag").dag_id if context.get("dag") else None,
        "task": context.get("task").task_id if context.get("task") else None,
        "run_id": context.get("run_id"),
        "exception": str(context.get("exception")),
    }
    if not webhook:
        logger.error("PIPELINE FAILURE and AIRFLOW_ALERT_WEBHOOK is not set: %s", payload)
        return
    import requests

    try:
        requests.post(webhook, json=payload, timeout=10)
    except Exception:
        logger.exception("failed to deliver the pipeline failure alert: %s", payload)


default_args = {
    "owner": "data-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
    "execution_timeout": timedelta(minutes=30),
    "on_failure_callback": _alert_on_failure,
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
    # no --log-dir/--target-path CLI flags).
    dbt_env = "DBT_LOG_PATH=/tmp/dbt-logs DBT_TARGET_PATH=/tmp/dbt-target"

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

    # Freshness only means something if the build just succeeded.
    dbt_build >> dbt_source_freshness

"""Warehouse orchestration — the Airflow entry point for the data platform.

One idempotent daily DAG: rebuild the marts, then assert freshness. Both
tasks are safe to re-run and backfill (dbt tables are fully rebuilt each
run — no incremental state), which is exactly what makes Airflow's
catchup/backfill machinery safe to lean on.
"""

import pendulum

from airflow import DAG
from airflow.operators.bash import BashOperator

DBT_DIR = "/opt/transform"

default_args = {
    "owner": "data-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
    "execution_timeout": timedelta(minutes=30),
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

"""The operational ML loop — hourly forecast serving + daily retrain/benchmark.

This is the scheduled half of the forecasting milestone (the review round's
M1 proved the loop goes DORMANT when the serving table is only refreshed by
hand). Two DAGs, two cadences:

- ``ml_forecast_serving`` (@hourly): refreshes the next-24h zone-hour
  ``demand_forecasts`` the pricing API derives its demand ratio from. The
  surge loop is only as live as this table.
- ``ml_retrain_benchmark`` (daily 04:15 UTC, after the 03:00 warehouse
  build so the marts are fresh): re-runs both benchmarks on the whole
  accumulated history. For the ETA benchmark this is also the promotion
  step — a challenger ships only if it beats the champion on the holdout,
  and the MLflow registry records the verdict. The geo benchmark is NOT
  scheduled on purpose: its pairs barely change day to day, so daily
  reports would be near-duplicates (the live shadow log is the signal).

Execution model (the "scheduler decision" this task settles): tasks run
IN-PROCESS in this image (LocalExecutor) — the same fat-worker pattern the
dbt tasks already use. The ML dependencies (mlflow/lightgbm/prophet) ride
in the airflow image; the ml/ code is bind-mounted, so code edits are live
and only dependency changes need a rebuild. The documented production
upgrade path is KubernetesPodOperator/ECSOperator to isolate training from
the scheduler — deferred deliberately on a single-node stack.

MLflow artifacts flow through the tracking server (proxied artifacts), so
this container needs no artifact volume — just the tracking URI.
"""

import os
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator
from ml_alerts import alert_on_failure

ML_HOME = "/home/airflow/mlrun"  # ./ml mounted at ./ml, ./data/benchmarks at ./data/benchmarks
GIT_DIR = "/opt/git"  # ./.git mounted read-only — per-run SHA goes into reports/run tags
MLFLOW_URI = "http://mlflow:5100"

# The ML stack lives in its own venv (Dockerfile.airflow) — pandas' SQL
# layer needs SQLAlchemy 2.0, Airflow pins 1.4, so the deps CANNOT share
# one environment. Invoke the venv interpreter by absolute path; bare
# `python` would resolve to the dbt venv (first on PATH), which has no ML
# packages at all.
PYTHON = "/home/airflow/ml-venv/bin/python"

# Airflow 2.10 pins SQLAlchemy 1.4 and this image carries only psycopg2 (the
# ml-bench container uses the psycopg3 dialect instead) — task URLs say
# +psycopg2 so the harness's create_engine resolves a driver that exists here.
_OPS_URL = "postgresql+psycopg2://smartops:smartops@postgres:5432/smart_delivery_ops"
_WAREHOUSE_URL = "postgresql+psycopg2://smartops:smartops@postgres:5432/smart_delivery_warehouse"

# Region pack (#78): the manifests + timezone ride the compose env down to
# the tasks, so a deployment's ML loop grains itself like everything else.
_ML_ENV = (
    f"DATABASE_URL={_OPS_URL} "
    f"WAREHOUSE_DATABASE_URL={_WAREHOUSE_URL} "
    f"MLFLOW_TRACKING_URI={MLFLOW_URI} "
    f"MLFLOW_HTTP_REQUEST_MAX_RETRIES=2 "
    f"BENCHMARK_REPORT_DIR={ML_HOME}/data/benchmarks "
    f"CITY={os.environ.get('CITY', 'tunis')} "
    f"BUSINESS_TIMEZONE={os.environ.get('BUSINESS_TIMEZONE', 'Africa/Tunis')} "
    f"OSRM_BASE_URL={os.environ.get('OSRM_BASE_URL', 'http://osrm:5000')} "
)
# Traceability: the exact code that trained/served, stamped on every run.
_GIT_SHA = f"GIT_SHA=$(git --git-dir={GIT_DIR} rev-parse --short HEAD 2>/dev/null || echo unknown)"

default_args = {
    "owner": "ml-engineering",
    "retries": 1,
    "retry_delay": timedelta(minutes=3),
    "execution_timeout": timedelta(minutes=45),
    "on_failure_callback": alert_on_failure,
}


def _ml_task(task_id: str, script: str, description: str) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=f"cd {ML_HOME} && {_GIT_SHA} {_ML_ENV} {PYTHON} {script}",
        doc_md=description,
    )


with DAG(
    dag_id="ml_forecast_serving",
    description="hourly refresh of the zone-hour demand_forecasts the surge loop reads",
    start_date=pendulum.datetime(2026, 10, 29, tz="UTC"),
    schedule="0 * * * *",
    catchup=False,  # backfilling serving runs would recompute history the API never asked for
    max_active_runs=1,  # two concurrent snapshots would race their prune windows
    default_args=default_args,
    tags=["ml", "forecasting", "phase3"],
) as serving_dag:
    _refresh_forecasts = _ml_task(
        "refresh_forecasts",
        "ml/pipelines/forecast_demand.py",
        "Trains the pooled LightGBM on the full demand mart and upserts the "
        "next-24h zone-hour forecasts (per-run snapshot; prune bounds come "
        "from the fresh predictions).",
    )

with DAG(
    dag_id="ml_retrain_benchmark",
    description="daily re-run of both benchmarks; registry promotion stays champion-gated",
    start_date=pendulum.datetime(2026, 10, 29, tz="UTC"),
    schedule="15 4 * * *",  # 04:15 UTC — after the 03:00 warehouse build rebuilt the marts
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["ml", "benchmark", "phase3"],
) as retrain_dag:
    # Sequential on purpose: two LightGBM trainings would share the
    # scheduler process's CPU (LocalExecutor), and the demand benchmark
    # finishes in minutes anyway.
    _demand_benchmark = _ml_task(
        "demand_benchmark",
        "ml/training/benchmark_demand.py",
        "Seasonal-naive vs LightGBM vs Prophet on the full demand history; "
        "one MLflow run per model, snapshot = max(event_id) + git SHA.",
    )
    _eta_benchmark = _ml_task(
        "eta_benchmark",
        "ml/training/benchmark_eta.py",
        "Heuristic champion vs OSRM free-flow vs LightGBM on realized "
        "deliveries; a challenger that beats the champion is promoted to "
        "the eta_model Production stage by the script itself.",
    )
    _demand_benchmark >> _eta_benchmark

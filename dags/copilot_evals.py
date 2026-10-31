"""The scheduled live eval (#123 Phase D): the golden copilot eval set
against the CONFIGURED provider, one MLflow run per batch in the
`copilot_evals` experiment, task fails when the pass rate dips under the
bar (an eval regression pages like any other regression).

Starts PAUSED like every new DAG — flip it on once the provider account
is funded; until then enabling it would just fail honestly (and still
log the evidence).
"""

import os
from datetime import timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from ml_alerts import alert_on_failure
from pendulum import datetime

ML_HOME = "/home/airflow/mlrun"
GIT_DIR = "/opt/git"
MLFLOW_URI = "http://mlflow:5100"
PYTHON = "/home/airflow/ml-venv/bin/python"

# The copilot deps (langgraph, openai, fastapi for the shared factory)
# live in the same ml-venv as the other ML tasks — see Dockerfile.airflow.
_EVAL_ENV = (
    f"DATABASE_URL=postgresql+psycopg2://smartops:smartops@postgres:5432/smart_delivery_ops "
    f"MLFLOW_TRACKING_URI={MLFLOW_URI} "
    f"MLFLOW_HTTP_REQUEST_MAX_RETRIES=2 "
    f"OPENAI_API_KEY_FILE=/run/secrets/openai_api_key "
    f"COPILOT_PROVIDER={os.environ.get('COPILOT_PROVIDER', 'scripted')} "
    f"COPILOT_MODEL={os.environ.get('COPILOT_MODEL', 'gpt-4o-mini')} "
    f"COPILOT_MODEL_MINI={os.environ.get('COPILOT_MODEL_MINI', 'gpt-4o-mini')} "
)
_GIT_SHA = f"GIT_SHA=$(git --git-dir={GIT_DIR} rev-parse --short HEAD 2>/dev/null || echo unknown)"

with DAG(
    dag_id="copilot_evals",
    description="Daily golden-set eval of the Ops Copilot against its configured provider (#123)",
    start_date=datetime(2026, 10, 31, tz="UTC"),
    schedule="0 5 * * *",  # 05:00 UTC — before the business day, after the benchmark loop
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "ml-engineering",
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=30),
        "on_failure_callback": alert_on_failure,
    },
    tags=["copilot", "evals"],
) as dag:
    copilot_eval = BashOperator(
        task_id="copilot_eval",
        bash_command=(
            f"cd {ML_HOME} && {_GIT_SHA} {_EVAL_ENV} "
            f"{PYTHON} ml/evals/run_copilot_eval.py "
            f"--eval-set data/evals/copilot_eval_set.json --min-pass-rate 0.8"
        ),
    )

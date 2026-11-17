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
#
# The subprocess env is passed via BashOperator's env= parameter, NOT the
# command string: the task log echoes the full command, and the earlier
# inline pattern logged the LangSmith key in plaintext (#214 fix). Values
# are read at parse time — the scheduler parses fresh per run.


def _eval_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+psycopg2://smartops:smartops@postgres:5432/smart_delivery_ops",
        "MLFLOW_TRACKING_URI": MLFLOW_URI,
        "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "2",
        "OPENAI_API_KEY_FILE": "/run/secrets/openai_api_key",
        "COPILOT_PROVIDER": os.environ.get("COPILOT_PROVIDER", "scripted"),
        "COPILOT_MODEL": os.environ.get("COPILOT_MODEL", "gpt-4o-mini"),
        "COPILOT_MODEL_MINI": os.environ.get("COPILOT_MODEL_MINI", "gpt-4o-mini"),
        # LangSmith (#214): the eval sink's second destination. The key rides
        # the scheduler's own environment; the agent address routes the
        # traces to the registered agent.
        "LANGSMITH_API_KEY": os.environ.get("LANGSMITH_API_KEY", ""),
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_ENDPOINT": os.environ.get(
            "LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com"
        ),
        "LANGSMITH_AGENT_ID": os.environ.get("LANGSMITH_AGENT_ID", "smart-delivery-copilot"),
        "LANGSMITH_AGENT_ENVIRONMENT": os.environ.get("LANGSMITH_AGENT_ENVIRONMENT", "local"),
        "EVAL_SINK": os.environ.get("EVAL_SINK", "both"),
    }


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
            f"cd {ML_HOME} && {_GIT_SHA} "
            f"{PYTHON} ml/evals/run_copilot_eval.py "
            f"--eval-set data/evals/copilot_eval_set.json --min-pass-rate 0.8"
        ),
        env=_eval_env(),
    )

    # Copilot health (#212): the degradation digest over the copilot
    # experiment. Crossing the threshold FAILS the task - and this DAG's
    # on_failure_callback is the alert (ml_alerts posts it with narration),
    # so a copilot degradation pages like any other regression. The miner
    # (#213) turns the same degraded runs into golden cases.
    copilot_health = BashOperator(
        task_id="copilot_health",
        bash_command=(
            f"cd {ML_HOME} && {_GIT_SHA} "
            f"{PYTHON} ml/evals/copilot_health.py --days 7 --threshold 0.34"
        ),
        env=_eval_env(),
    )

    copilot_eval >> copilot_health

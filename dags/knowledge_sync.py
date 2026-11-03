"""Keep the copilot's knowledge corpus in sync with the shipped docs (#173).

The RAG store goes stale SILENTLY: a new decision record lands in the repo
and the copilot simply doesn't know it until someone re-runs the ingest
(caught live 2026-11-03 — 0003/0004 were merged but absent from the store).
This DAG re-runs the idempotent ingests on a schedule: unchanged documents
cost zero work, deleted sources are tombstoned, and the final census task
prints the store's contents so the run history shows corpus growth next to
code growth.

Deployment knowledge only. The operator's personal Obsidian vault is a
machine-local artifact and stays on the local tooling (`make vault-ingest`,
or the assistant-side sync); on a box where a vault IS mounted, set
KNOWLEDGE_SYNC_VAULT to its path and the vault task joins the sync.
"""

import os
from datetime import timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from ml_alerts import alert_on_failure
from pendulum import datetime

ML_HOME = "/home/airflow/mlrun"
PYTHON = "/home/airflow/ml-venv/bin/python"

# core.database builds its engine from this URL at import time — the
# ml-venv ships psycopg2 (airflow's own driver), so the app's default
# psycopg3 URL is overridden here exactly like the evals DAG does.
_DB = "DATABASE_URL=postgresql+psycopg2://smartops:smartops@postgres:5432/smart_delivery_ops"
_GIT_SHA = "GIT_SHA=$(git --git-dir=/opt/git rev-parse --short HEAD 2>/dev/null || echo unknown)"

with DAG(
    dag_id="knowledge_sync",
    description="Re-ingest the copilot knowledge corpus on a schedule — the store must track the repo (#173)",
    start_date=datetime(2026, 11, 3, tz="UTC"),
    schedule="0 */6 * * *",  # four times a day; the ingests are cheap when nothing changed
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "ml-engineering",
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(minutes=15),
        "on_failure_callback": alert_on_failure,
    },
    tags=["copilot", "rag"],
) as dag:
    sync_docs = BashOperator(
        task_id="sync_docs",
        bash_command=(f"cd {ML_HOME} && {_GIT_SHA} {_DB} {PYTHON} scripts/ingest_docs.py"),
    )

    # Optional vault sync: only when the operator mounts a vault AND points
    # KNOWLEDGE_SYNC_VAULT at it; otherwise the task says so and succeeds —
    # an absent personal vault is not a pipeline failure.
    sync_vault = BashOperator(
        task_id="sync_vault",
        bash_command=(
            f"cd {ML_HOME} && export {_DB}; "
            'if [ -n "$KNOWLEDGE_SYNC_VAULT" ] && [ -d "$KNOWLEDGE_SYNC_VAULT" ]; then '
            f'{PYTHON} scripts/ingest_obsidian.py --vault "$KNOWLEDGE_SYNC_VAULT"; '
            'else echo "vault sync skipped: KNOWLEDGE_SYNC_VAULT not set (personal vault stays on local tooling)"; fi'
        ),
        env={"KNOWLEDGE_SYNC_VAULT": os.environ.get("KNOWLEDGE_SYNC_VAULT", "")},
    )

    # The census is the receipt: what the copilot knows after this sync.
    corpus_census = BashOperator(
        task_id="corpus_census",
        bash_command=(
            f"cd {ML_HOME} && {_DB} {PYTHON} scripts/inspect_knowledge.py --prefix docs/"
        ),
    )

    sync_docs >> sync_vault >> corpus_census

"""Shared failure surface for the platform's DAGs.

A DAG/task failure must leave the log-only boundary (Phase-2 review
finding 5): POSTs to ``AIRFLOW_ALERT_WEBHOOK`` when configured, otherwise
logs loudly so the gap stays discoverable. Imported by every DAG file so
there is exactly one definition of "what happens when a task fails".
"""

import logging
import os

logger = logging.getLogger(__name__)


def alert_on_failure(context) -> None:  # noqa: ANN001 - Airflow context dict
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

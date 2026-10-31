"""Shared failure surface for the platform's DAGs.

A DAG/task failure must leave the log-only boundary (Phase-2 review
finding 5): POSTs to ``AIRFLOW_ALERT_WEBHOOK`` when configured, otherwise
logs loudly so the gap stays discoverable. Imported by every DAG file so
there is exactly one definition of "what happens when a task fails".

Since Phase C (#122), the alert also carries a narrated incident summary
(the copilot's narrator correlates the alert with the platform's own
observability). Narration is fail-open by contract: it never delays or
drops the alert, and SDO_NARRATOR=0 is the kill switch.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

# The narrator lives under apps/ mounted at /home/airflow/mlrun (the
# eval-round mounts); the scheduler's import path doesn't know it — add
# it, guarded, and only when present so environments without the mount
# just skip narration.
_MLRUN = "/home/airflow/mlrun"


def _attach_narration(payload: dict) -> dict:
    if os.environ.get("SDO_NARRATOR", "1") == "0":
        return payload
    try:
        if os.path.isdir(os.path.join(_MLRUN, "apps")) and _MLRUN not in sys.path:
            sys.path.insert(0, _MLRUN)
        from apps.copilot.narrator import attach_narration

        return attach_narration(payload)
    except Exception:  # noqa: BLE001 - the alert must leave on time
        logger.exception("incident narration skipped (fail-open)")
        return payload


def alert_on_failure(context) -> None:  # noqa: ANN001 - Airflow context dict
    webhook = os.environ.get("AIRFLOW_ALERT_WEBHOOK")
    payload = {
        "dag": context.get("dag").dag_id if context.get("dag") else None,
        "task": context.get("task").task_id if context.get("task") else None,
        "run_id": context.get("run_id"),
        "exception": str(context.get("exception")),
    }
    payload = _attach_narration(payload)
    if not webhook:
        logger.error("PIPELINE FAILURE and AIRFLOW_ALERT_WEBHOOK is not set: %s", payload)
        return
    import requests

    try:
        requests.post(webhook, json=payload, timeout=10)
    except Exception:
        logger.exception("failed to deliver the pipeline failure alert: %s", payload)

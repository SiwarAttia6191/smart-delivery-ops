"""Copilot tracing (#120 acceptance): every conversation lands in MLflow
as a run — provider, prompt version, tool-call count, and the full message
log as an artifact. This is what turns #72's empty tabs real: the run
detail page shows the trajectory.

Fail-safe by role, like everything else in the copilot: a tracking-server
outage must never break the answer. The import is lazy so environments
without mlflow (the slim test venvs) skip tracing entirely.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def trace_run(question: str, result: dict[str, Any]) -> None:
    # Opt-in by env (the compose api sets SDO_TRACING=1): the MLflow client
    # retries a dead tracking server for minutes, which would hang the
    # answer — and tests must never wait on network. Fail-open, always.
    if os.environ.get("SDO_TRACING") != "1":
        return
    try:
        import mlflow

        from core.config import get_settings

        mlflow.set_tracking_uri(get_settings().mlflow_tracking_uri)
        mlflow.set_experiment("copilot")
        with mlflow.start_run(run_name=f"copilot_{result.get('provider', 'unknown')}"):
            mlflow.log_params(
                {
                    "provider": result.get("provider"),
                    "prompt_version": result.get("prompt_version"),
                    "tool_calls_used": result.get("tool_calls_used", 0),
                    "degraded": result.get("degraded", False),
                    "question": question[:300],
                }
            )
            mlflow.log_dict({"messages": result.get("messages", [])}, "conversation.json")
    except Exception as exc:  # noqa: BLE001 - the evidence annex, never the answer
        logger.warning("copilot tracing skipped (%s: %s)", type(exc).__name__, exc)

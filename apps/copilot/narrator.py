"""The incident narrator (#122 Phase C): the AI that explains your
pipeline when an alert fires.

When a DAG task fails (drift breach, benchmark collapse, warehouse
build), the shared alert callback (dags/ml_alerts.py) calls
attach_narration: the narrator correlates the alert with the platform's
own observability — the /analytics/metrics snapshot, /health, and the
latest ETA benchmark run from MLflow — and writes a Slack-ready,
plain-text incident summary into the alert payload.

Runs INSIDE the Airflow scheduler process, so it is deliberately
dependency-light: stdlib + requests only, no core.config (the scheduler
env has no app settings), no DB session (the evidence is what on-call
sees, over HTTP). Fails open BY CONTRACT — a narrator outage must never
delay or drop the alert itself, and every remote read is timeout-bounded
with SDO_NARRATOR=0 as the kill switch (the same rule as SDO_TRACING:
anything that can wait on a remote needs a switch).

The narration provider follows the platform's pluggable pattern: the
configured live model narrates when COPILOT_PROVIDER=openai (short
timeout, one retry — the alert is more urgent than the prose), and a
deterministic template narrates otherwise or when the model errors.
"""

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

PROMPT_VERSION = "n1"
_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "narrator.md")

_EVIDENCE_TIMEOUT = 4  # seconds, per source — an alert is already firing
_LIVE_NARRATION_TIMEOUT = 10


def _get_json(url: str, timeout: int = _EVIDENCE_TIMEOUT) -> Any | None:
    """One fail-open GET: None on any error, so a dead source degrades to
    'unknown' in the narration instead of killing it."""
    try:
        import requests

        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 - fail open, per source
        logger.warning("narrator evidence source unavailable (%s): %s", url, exc)
        return None


def _get_prometheus(url: str, timeout: int = _EVIDENCE_TIMEOUT) -> dict[str, Any] | None:
    """Fail-open fetch + parse of the api's Prometheus exposition (the
    /analytics/metrics endpoint is text format, not JSON): gauge name →
    sample value, HELP/TYPE lines and labels dropped."""
    try:
        import requests

        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        samples: dict[str, Any] = {}
        for line in response.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, rest = line.partition(" ")
            samples[name.split("{")[0]] = rest.strip()
        return samples or None
    except Exception as exc:  # noqa: BLE001 - fail open, per source
        logger.warning("narrator evidence source unavailable (%s): %s", url, exc)
        return None


def gather_evidence(alert: dict[str, Any]) -> dict[str, Any]:
    """Correlate the alert with the platform's own observability. Each
    source is best-effort; missing pieces narrate as 'unknown'."""
    api_base = os.environ.get("SDO_API_BASE_URL", "http://api:8000")
    mlflow_base = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5100").rstrip("/")

    evidence: dict[str, Any] = {
        "alert": {
            "dag": alert.get("dag"),
            "task": alert.get("task"),
            "run_id": alert.get("run_id"),
            "exception": str(alert.get("exception"))[:300],
            "observed_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }
    }
    metrics = _get_prometheus(f"{api_base}/analytics/metrics")
    if metrics is not None:
        evidence["ops_metrics"] = metrics
    health = _get_json(f"{api_base}/health")
    if health is not None:
        evidence["api_health"] = health

    # the latest run of the ETA benchmark experiment: the artifact trail
    # that drift watch judges breaches against
    try:
        import requests

        exp = requests.get(
            f"{mlflow_base}/api/2.0/mlflow/experiments/get-by-name",
            params={"experiment_name": "eta_benchmark"},
            timeout=_EVIDENCE_TIMEOUT,
        ).json()
        exp_id = exp.get("experiment", {}).get("experiment_id")
        if exp_id:
            runs = requests.post(
                f"{mlflow_base}/api/2.0/mlflow/runs/search",
                json={
                    "experiment_ids": [exp_id],
                    "max_results": 1,
                    "order_by": ["attributes.start_time DESC"],
                },
                timeout=_EVIDENCE_TIMEOUT,
            ).json()
            runs_found = runs.get("runs") or []
            if runs_found:
                run = runs_found[0]
                evidence["latest_eta_benchmark"] = {
                    "run_id": run["info"].get("run_id"),
                    "status": run["info"].get("status"),
                    "start_time": run["info"].get("start_time"),
                }
    except Exception as exc:  # noqa: BLE001 - fail open, per source
        logger.warning("narrator: MLflow latest-run lookup skipped (%s)", exc)

    return evidence


def _scripted_narration(evidence: dict[str, Any]) -> str:
    """The deterministic fallback: same facts, no model. Numbers first,
    then the alert identity, then the first checks."""
    alert = evidence.get("alert", {})
    metrics = evidence.get("ops_metrics") or {}
    health = evidence.get("api_health")
    bench = evidence.get("latest_eta_benchmark") or {}

    def _num(key: str) -> str:
        value = metrics.get(key)
        return str(value) if value is not None else "unknown"

    api_state = "healthy" if (health or {}).get("status") in (None, "ok", "healthy") else "degraded"
    return (
        f"Incident {alert.get('dag')}/{alert.get('task')} at {alert.get('observed_at')}: "
        f"{alert.get('exception', 'unknown')}. "
        f"Observed now — outbox unpublished {_num('sdo_outbox_unpublished')}, "
        f"relay age {_num('sdo_outbox_last_relay_age_seconds')}s, api {api_state}. "
        f"Latest ETA benchmark run {bench.get('run_id', 'unknown')} "
        f"({bench.get('status', 'unknown')}). "
        f"First checks: the failing task's Airflow log, the relay container, "
        f"postgres health, then docs/runbook.md."
    )


def _prompt_text() -> str:
    try:
        with open(_PROMPT_PATH, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return (
            "You are the incident narrator for a delivery-operations platform. "
            "Given the evidence JSON, write a Slack-ready plain-text summary of at "
            "most 120 words: what broke, when, the key numbers, and the first "
            "checks. No markdown, no tables."
        )


def narrate(evidence: dict[str, Any], provider: Any = None) -> str:
    """One narration. `provider` is injectable for tests; without one,
    the live model narrates when COPILOT_PROVIDER=openai (bounded timeout,
    one retry) and the deterministic template narrates otherwise or when
    the model errors."""
    if provider is None and os.environ.get("COPILOT_PROVIDER") == "openai":
        provider = _live_provider()
    if provider is not None:
        try:
            reply = provider.chat(
                [
                    {"role": "system", "content": _prompt_text()},
                    {"role": "user", "content": json.dumps(evidence)[:8000]},
                ],
                [],
            )
            if reply.content:
                return reply.content
        except Exception as exc:  # noqa: BLE001 - fail open to the template
            logger.warning("narrator: live model failed (%s); using the template", exc)
    return _scripted_narration(evidence)


def _live_provider() -> Any | None:
    """The live model for narration only: bounded timeout, one retry —
    the alert is already firing and must not wait on a chatty API."""
    key_file = os.environ.get("OPENAI_API_KEY_FILE")
    api_key = os.environ.get("OPENAI_API_KEY") or (
        open(key_file, encoding="utf-8").read().strip()
        if key_file and os.path.exists(key_file)
        else ""
    )
    if not api_key or api_key == "xxx":
        return None
    try:
        from apps.copilot.providers.openai_client import OpenAIProvider

        return OpenAIProvider(
            api_key=api_key,
            model=os.environ.get("COPILOT_MODEL", "gpt-4o-mini"),
            timeout_seconds=_LIVE_NARRATION_TIMEOUT,
            max_retries=1,
        )
    except Exception as exc:  # noqa: BLE001 - fail open to the template
        logger.warning("narrator: live provider unavailable (%s)", exc)
        return None


def attach_narration(payload: dict[str, Any]) -> dict[str, Any]:
    """The alert callback's integration point: add `summary` (and the
    prompt version) to the alert payload. Never raises — the contract is
    that the alert leaves with or without a narration, on time."""
    try:
        evidence = gather_evidence(payload)
        payload["summary"] = narrate(evidence)
        payload["narrator_prompt_version"] = PROMPT_VERSION
    except Exception as exc:  # noqa: BLE001 - fail open, by contract
        logger.warning("narrator: incident narration skipped (%s)", exc)
    return payload

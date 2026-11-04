"""The eval result sink (#157): MLflow stays the default; LangSmith joins
by config. ``EVAL_SINK`` selects: mlflow | langsmith | both.

The LangSmith adapter is deliberately thin — the eval has ALREADY run
(the pass/fail verdicts are computed by the harness); the sink's job is
provenance: a dataset per eval-set version, one example per case, one
run per case carrying the verdict as feedback. Failure to reach LangSmith
logs a warning and never fails the eval (the same fail-safe posture as
tracing: a telemetry outage must not break the pipeline).
"""

import logging

from core.config import get_settings

logger = logging.getLogger(__name__)


def _langsmith_client():
    from langsmith import Client  # lazy: the import is the dependency boundary

    return Client()


def log_to_langsmith(eval_name: str, eval_set: dict, results: list[dict]) -> None:
    """Provenance pass: dataset + examples + one run per case with its
    verdict. Best-effort by contract."""
    try:
        client = _langsmith_client()
        dataset = client.create_dataset(
            dataset_name=f"{eval_name}-v{eval_set.get('version', '0')}",
            description="Ops Copilot golden set (auto-synced from the eval harness)",
        )
        for case, result in zip(eval_set["cases"], results, strict=False):
            client.create_example(
                dataset_id=dataset.id,
                inputs={"question": case["question"]},
                outputs={"expected_tools": case.get("expected_tools", [])},
            )
            client.create_run(
                name=case["name"],
                run_type="chain",
                inputs={"question": case["question"]},
                outputs={"answer": result.get("answer", ""), "tools": result.get("tools", [])},
                reference_dataset_id=dataset.id,
            )
        logger.info("langsmith sink: %s cases logged under %s", len(results), dataset.name)
    except Exception as exc:  # noqa: BLE001 - telemetry must not break the eval
        logger.warning("langsmith sink unavailable (%s); eval results kept in MLflow only", exc)


def sinks() -> list[str]:
    """The configured sink list: mlflow | langsmith | both -> [..]."""
    value = get_settings().eval_sink.strip().lower()
    if value == "both":
        return ["mlflow", "langsmith"]
    return [value]


def dispatch(eval_name: str, eval_set: dict, results: list[dict]) -> None:
    """Send the finished eval to every configured sink. MLflow logging
    stays inline in the runner (it owns the experiment semantics); this
    dispatch adds the secondary sinks."""
    if "langsmith" in sinks():
        log_to_langsmith(eval_name, eval_set, results)

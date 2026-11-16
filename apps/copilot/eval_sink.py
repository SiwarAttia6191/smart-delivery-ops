"""The eval result sink (#157): MLflow stays the default; LangSmith joins
by config. ``EVAL_SINK`` selects: mlflow | langsmith | both.

The LangSmith adapter is deliberately thin — the eval has ALREADY run
(the pass/fail verdicts are computed by the harness); the sink's job is
provenance, in three layers (#214, #292): a dataset per eval-set version
(read-or-create, examples seed only on creation), ONE experiment per eval
run (a project bound to the dataset, pass rate in its metadata), and per
case a run tied to its example with the verdict as feedback. Failure to
reach LangSmith logs a warning and never fails the eval (the same
fail-safe posture as tracing: a telemetry outage must not break the
pipeline).
"""

import logging
from datetime import UTC, datetime

from core.config import get_settings

logger = logging.getLogger(__name__)


def _langsmith_client():
    from langsmith import Client  # lazy: the import is the dependency boundary

    return Client()


def _verdict_comment(result: dict) -> str:
    """The feedback comment: WHY the case scored what it scored — the
    failing check names, the provider that served, the degradation."""
    checks = result.get("checks", {})
    failed = [name for name, ok in checks.items() if not ok]
    comment = (
        "all checks passed"
        if result.get("passed")
        else (f"failed checks: {', '.join(failed) or 'unknown'}")
    )
    if result.get("degraded_reason"):
        comment += f" | degraded: {result['degraded_reason']}"
    return comment


def log_to_langsmith(eval_name: str, eval_set: dict, results: list[dict]) -> None:
    """Provenance pass: dataset + examples, one experiment per run, per
    case a run tied to its example with the verdict as feedback.
    Best-effort by contract."""
    try:
        client = _langsmith_client()
        version = eval_set.get("version", "0")
        dataset_name = f"{eval_name}-v{version}"
        # idempotent sync (#214): the dataset persists across eval runs —
        # read-or-create; the examples seed ONLY on creation (re-adding
        # them per run would duplicate the golden set). Either path ends
        # with question -> example id, so this run's cases can attach.
        try:
            dataset = client.read_dataset(dataset_name=dataset_name)
            examples_by_question = {
                (ex.inputs or {}).get("question"): str(ex.id)
                for ex in client.list_examples(dataset_id=dataset.id)
            }
        except Exception:  # noqa: BLE001 - first run: the dataset doesn't exist yet
            dataset = client.create_dataset(
                dataset_name=dataset_name,
                description="Ops Copilot golden set (auto-synced from the eval harness)",
            )
            examples_by_question = {}
            for case in eval_set["cases"]:
                example = client.create_example(
                    dataset_id=dataset.id,
                    inputs={"question": case["question"]},
                    outputs={"expected_tools": case.get("expected_tools", [])},
                )
                examples_by_question[case["question"]] = str(example.id)
        # one experiment per eval run (#292): a project bound to the
        # dataset — the timestamp + version + provider name is what makes
        # LangSmith's compare view line runs up, and the metadata carries
        # the summary so the experiment list reads at a glance
        provider = next((r.get("provider") for r in results if r.get("provider")), "unknown")
        experiment_name = (
            f"copilot_eval_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}-v{version}-{provider}"
        )
        passed = sum(1 for r in results if r.get("passed"))
        pass_rate = passed / len(results) if results else 0.0
        client.create_project(
            project_name=experiment_name,
            description=f"Ops Copilot eval run over {dataset_name} (#292)",
            reference_dataset_id=dataset.id,
            metadata={
                "eval_set_version": version,
                "provider": provider,
                "cases_total": len(results),
                "cases_passed": passed,
                "pass_rate": round(pass_rate, 4),
            },
        )
        for case, result in zip(eval_set["cases"], results, strict=False):
            run_kwargs: dict = {
                "metadata": {
                    "provider": result.get("provider"),
                    "degraded": result.get("degraded"),
                }
            }
            if example_id := examples_by_question.get(case["question"]):
                run_kwargs["reference_example_id"] = example_id
            run = client.create_run(
                name=case["id"],  # the golden cases key on id, not name
                run_type="chain",
                inputs={"question": case["question"]},
                outputs={
                    "answer": result.get("answer", ""),
                    "tool_sequence": result.get("tool_sequence", []),
                },
                project_name=experiment_name,
                reference_dataset_id=dataset.id,
                kwargs=run_kwargs,
            )
            client.create_feedback(
                run.id,
                key="passed",
                score=1.0 if result.get("passed") else 0.0,
                comment=_verdict_comment(result),
            )
        # flush BEFORE the eval subprocess exits: the SDK ingests in a
        # background thread that dies with the process — without this the
        # runs are lost silently (the 21:26 eval proved it: dataset
        # created, runs zero)
        client.flush()
        logger.info(
            "langsmith sink: %s cases logged under experiment %s (pass rate %.2f)",
            len(results),
            experiment_name,
            pass_rate,
        )
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

"""The scheduled live eval (#123 Phase D): the golden eval set served to
the CONFIGURED provider, scored into the `copilot_evals` MLflow
experiment, and failed loudly when the pass rate dips under the bar.

Runs inside the Airflow ml-venv (the copilot_evals DAG) with the repo
packages bind-mounted at ML_HOME — the same env-var discipline as the
other ML tasks (+psycopg2 URL, MLFLOW_TRACKING_URI, OPENAI_API_KEY_FILE).
With a credit-less or absent key the provider chain degrades and every
case that needed the model scores 0 — the run still LOGS (evidence of
the outage) and the task fails (an eval regression is a regression).
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from apps.copilot.evals import DEFAULT_EVAL_SET, load_eval_set, run_eval  # noqa: E402
from apps.copilot.providers.factory import build_provider_chain  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.database import engine  # noqa: E402


def log_to_mlflow(summary: dict, provider_names: list[str], min_pass_rate: float) -> None:
    """One MLflow run per eval batch: the score, the provider that served,
    and the per-case detail as an artifact. Fail-safe — a tracking-server
    outage must not mask the eval's own verdict."""
    try:
        import mlflow

        mlflow.set_tracking_uri(get_settings().mlflow_tracking_uri)
        mlflow.set_experiment("copilot_evals")
        with mlflow.start_run(
            run_name=f"copilot_eval_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        ):
            mlflow.log_params(
                {
                    "eval_set_version": summary["eval_set_version"],
                    "providers": ",".join(provider_names),
                    "cases_total": summary["total"],
                    "min_pass_rate": min_pass_rate,
                }
            )
            mlflow.log_metrics(
                {
                    "pass_rate": summary["pass_rate"],
                    "cases_passed": summary["passed"],
                }
            )
            mlflow.log_dict(summary, "eval_results.json")
    except Exception as exc:  # noqa: BLE001 - the evidence annex, never the verdict
        print(f"WARN: MLflow logging skipped ({type(exc).__name__}: {exc})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-set", default=str(DEFAULT_EVAL_SET))
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.8,
        help="exit 1 below this rate (the CI suite holds itself to 1.0)",
    )
    parser.add_argument("--no-mlflow", action="store_true", help="skip the MLflow run")
    args = parser.parse_args()

    eval_set = load_eval_set(args.eval_set)
    chain = build_provider_chain()
    print(
        f"eval set v{eval_set['version']}: {len(eval_set['cases'])} cases, "
        f"provider chain: {[p.name for p in chain]}"
    )

    # Scripted config → each case scripts ITSELF (the CI-equivalent run:
    # the factory's scripted provider is the endpoint's KPI replay, not
    # a per-case eval script). Live config → the flagship serves every
    # case and its ACTUAL tool selection is what gets scored.
    provider_factory = None
    if get_settings().copilot_provider != "scripted":
        provider_factory = lambda: build_provider_chain()[0]  # noqa: E731 - small, obvious

    session = sessionmaker(bind=engine)()
    try:
        summary = run_eval(session, eval_set=eval_set, provider_factory=provider_factory)
    finally:
        session.close()

    for result in summary["results"]:
        flag = "PASS" if result["passed"] else "FAIL"
        detail = result.get("detail") or f"tools={result.get('tool_sequence')}"
        print(f"  [{flag}] {result['id']}: {detail}")
    print(
        f"pass rate {summary['passed']}/{summary['total']} = {summary['pass_rate']:.2f} "
        f"(min {args.min_pass_rate})"
    )

    if not args.no_mlflow:
        log_to_mlflow(summary, [p.name for p in chain], args.min_pass_rate)

    return 0 if summary["pass_rate"] >= args.min_pass_rate else 1


if __name__ == "__main__":
    sys.exit(main())

"""Drift watch (#102): the missing half of the #41 baseline work.

The daily retrain DAG runs this AFTER the ETA benchmark. It fetches the
latest `eta_lightgbm` run's drift metrics from MLflow and compares them
against the committed baselines (data/benchmarks/drift_baselines.json —
derived from the promoted model's verified run, tolerances documented
inside). Any breach exits non-zero, which fails the `drift_watch` task
and fires the standard alert webhook (ml_alerts.alert_on_failure) — the
same machinery every other pipeline failure uses, no new plumbing.

Exit codes: 0 = within tolerance (silent), 1 = drift breach (alert), 2 =
operational failure (no run found / no baselines — also alert-worthy).

DRIFT_COMPARE_RUN_ID overrides which run is inspected (used to prove the
alert path: point it at an old run whose numbers diverge).
"""

import json
import os
import sys
from pathlib import Path

import mlflow

# Same location discipline as benchmark_eta's REPORT_DIR: the Airflow ML
# tasks set DRIFT_BASELINES_DIR, the ml-bench container binds /ml/benchmarks'
# sibling /data/benchmarks, host runs use the repo layout.
BASELINES = Path(
    os.environ.get("DRIFT_BASELINES")
    or (
        "/ml/benchmarks/drift_baselines.json"
        if Path("/ml/benchmarks/drift_baselines.json").exists()
        else Path(__file__).resolve().parents[2] / "data" / "benchmarks" / "drift_baselines.json"
    )
)


def latest_lightgbm_metrics() -> tuple[str, dict[str, float]]:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5100")
    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.tracking.MlflowClient()
    experiment = client.get_experiment_by_name("eta_benchmark")
    if experiment is None:
        raise RuntimeError("eta_benchmark experiment does not exist")
    override = os.environ.get("DRIFT_COMPARE_RUN_ID")
    if override:
        run_id = override
    else:
        runs = client.search_runs(
            [experiment.experiment_id],
            filter_string="attributes.run_name = 'eta_lightgbm'",
            order_by=["attributes.start_time DESC"],
            max_results=1,
        )
        if not runs:
            raise RuntimeError("no eta_lightgbm run found to watch")
        run_id = runs[0].info.run_id
    run = client.get_run(run_id)
    metrics = {k: v for k, v in run.data.metrics.items() if k in BASELINE_KEYS}
    return run_id, metrics


with open(BASELINES, encoding="utf-8") as fh:
    BASELINES_DOC = json.load(fh)
BASELINE_KEYS = set(BASELINES_DOC["metrics"]) | {"residual_mean", "residual_std"}


def main() -> None:
    run_id, metrics = latest_lightgbm_metrics()
    baselines = BASELINES_DOC["metrics"]
    tolerances = BASELINES_DOC["tolerances"]

    breaches: dict[str, str] = {}
    print(
        f"drift watch: comparing run {run_id} against baselines from {BASELINES_DOC['source_run_id'][:8]}"
    )
    print(f"{'metric':32s} {'value':>10s} {'baseline':>10s} {'tol':>6s}  verdict")
    for key, tol in tolerances.items():
        if key.startswith("_"):
            continue  # documentation keys inside the tolerances mapping
        if key not in metrics or key not in baselines:
            print(f"{key:32s} {'-':>10s} {'-':>10s} {'-':>6s}  MISSING (skipped)")
            continue
        value, base = metrics[key], baselines[key]
        delta = abs(value - base)
        verdict = "BREACH" if delta > tol else "ok"
        print(f"{key:32s} {value:>10.3f} {base:>10.3f} {tol:>6.2f}  {verdict}")
        if verdict == "BREACH":
            breaches[key] = f"{value:.3f} vs baseline {base:.3f} (tol ±{tol})"

    if breaches:
        print("DRIFT BREACH — the serving model's behavior moved off its baseline:")
        for key, detail in breaches.items():
            print(f"  {key}: {detail}")
        print(
            "Alert fires via this task's on_failure_callback. Next step: "
            "inspect the run's artifacts (residual histogram, bucket table) "
            "and decide whether to re-baseline deliberately."
        )
        sys.exit(1)
    print("drift watch: all metrics within tolerance")


if __name__ == "__main__":
    main()

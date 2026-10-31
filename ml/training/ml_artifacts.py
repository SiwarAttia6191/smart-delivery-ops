"""Per-run evaluation artifacts (#71): the visual evidence lands in MLflow
next to the metrics, so the run detail page shows WHAT a model got wrong,
not only by how much. Both harnesses call these INSIDE their start_run
blocks; every helper is fail-safe — an artifact failure must never fail a
benchmark run (the metrics and the committed report are the deliverable;
these are the evidence annex).

Aggregation-free by convention: per-bucket / per-hour tables keep the
by-bucket convention the geo/ETA benchmarks established (an average hides
the shape).
"""

import json


def _safe(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - evidence annex, not the deliverable
        import mlflow

        mlflow.set_tag("artifacts_error", f"{type(exc).__name__}: {exc}"[:200])


def _matplotlib():
    import matplotlib

    matplotlib.use("Agg")  # headless container — never open a display
    import matplotlib.pyplot as plt

    return plt


def _slug(run_name: str) -> str:
    return run_name.split(" ")[0].replace("(", "").replace(")", "")


def log_demand_artifacts(run_name: str, actual: list, predicted: list, index) -> None:
    """Forecast-vs-actual plot, residual histogram, per-hour-of-day error
    table (JSON) for one demand-model run."""
    import mlflow
    import pandas as pd

    def _figures() -> None:
        plt = _matplotlib()
        fig, ax = plt.subplots(figsize=(11, 4))
        ax.plot(index, actual, label="actual", linewidth=1.2)
        ax.plot(index, predicted, label="predicted", linewidth=1.2)
        ax.set_title(f"{run_name}: holdout forecast vs actual")
        ax.set_ylabel("orders / hour")
        ax.legend()
        fig.tight_layout()
        mlflow.log_figure(fig, f"{_slug(run_name)}_forecast_vs_actual.png")
        plt.close(fig)

        residuals = pd.Series(predicted, index=index) - pd.Series(actual, index=index)
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.hist(residuals.dropna(), bins=24)
        ax.set_title(f"{run_name}: residuals (predicted - actual)")
        ax.set_xlabel("error (orders)")
        fig.tight_layout()
        mlflow.log_figure(fig, f"{_slug(run_name)}_residual_histogram.png")
        plt.close(fig)

    def _hour_table() -> None:
        residuals = (pd.Series(predicted, index=index) - pd.Series(actual, index=index)).dropna()
        by_hour = residuals.groupby(residuals.index.hour)
        table = {
            str(int(h)): {
                "n": int(len(g)),
                "mae": round(float(g.abs().mean()), 3),
                "bias": round(float(g.mean()), 3),
            }
            for h, g in by_hour
        }
        mlflow.log_dict(
            json.loads(json.dumps({"run": run_name, "per_hour_of_day": table})),
            f"{_slug(run_name)}_hour_of_day_errors.json",
        )

    _safe(_figures)
    _safe(_hour_table)


def log_eta_artifacts(run_name: str, actual, predicted, distances) -> None:
    """Predicted-vs-actual scatter and the per-distance-bucket error table
    (JSON) for one ETA-model run."""
    import mlflow
    import pandas as pd
    from benchmark_eta import bucket_of

    def _scatter() -> None:
        plt = _matplotlib()
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.scatter(actual, predicted, s=6, alpha=0.35)
        lim = float(max(actual.max(), predicted.max())) * 1.05
        ax.plot([0, lim], [0, lim], "r--", linewidth=1)
        ax.set_xlabel("actual minutes")
        ax.set_ylabel("predicted minutes")
        ax.set_title(f"{run_name}: predicted vs actual")
        fig.tight_layout()
        mlflow.log_figure(fig, f"{_slug(run_name)}_predicted_vs_actual.png")
        plt.close(fig)

    def _bucket_table() -> None:
        errors = pd.Series(predicted) - pd.Series(actual)
        buckets = pd.Series(distances).map(bucket_of)
        table = {}
        for name in sorted(buckets.unique()):
            idx = buckets[buckets == name].index
            table[str(name)] = {
                "n": int(len(idx)),
                "mae": round(float(errors.loc[idx].abs().mean()), 3),
                "bias": round(float(errors.loc[idx].mean()), 3),
            }
        mlflow.log_dict(
            {"run": run_name, "per_distance_bucket": table}, f"{_slug(run_name)}_bucket_errors.json"
        )

    _safe(_scatter)
    _safe(_bucket_table)

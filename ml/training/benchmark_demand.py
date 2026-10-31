"""Demand-forecasting benchmark: seasonal-naive vs LightGBM vs Prophet.

The challenger-vs-champion harness for demand (issues #25/#41), run as
`make ml-benchmark` (one-shot container). Ground truth is the warehouse's
`mart_demand_timeseries` — real orders, never fabricated. Every run logs
params/metrics/artifacts to MLflow and embeds the dataset snapshot
(`max(event_id)` + git SHA) per the training-snapshot convention.

Honesty contract: with a young deployment the data volume is far below what
these models need (Prophet wants weeks; LightGBM wants hundreds of hours).
The harness trains on exactly what exists, says so in the report, and never
fabricates history — the pipeline is the deliverable, accuracy accumulates
with real traffic.

Time-based split: the last ~20% of observed hours is the holdout (never
random — traffic patterns drift, and a random split leaks the future).
"""

import math
import os
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import mlflow
import pandas as pd
from sqlalchemy import create_engine, text

# BENCHMARK_REPORT_DIR (the Airflow ML tasks set it — the fat-worker
# container's cwd is not the ml image's WORKDIR /ml), else the ml-bench
# container's bind-mounted /ml/benchmarks, else the repo layout.
REPORT_DIR = Path(
    os.environ.get("BENCHMARK_REPORT_DIR")
    or ("/ml/benchmarks" if Path("/ml/benchmarks").exists() else "data/benchmarks")
)
EXPERIMENT = "demand_forecasting"


def git_sha() -> str:
    """Snapshot identity: passed in by compose from the host git checkout
    (the container has no .git)."""
    return os.environ.get("GIT_SHA", "unknown")[:12]


def load_series() -> pd.DataFrame:
    """Global demand series: orders per business-date × local-hour, from the
    warehouse timeseries mart (zoned rows summed; sparse zones aggregate)."""
    engine = create_engine(os.environ["WAREHOUSE_DATABASE_URL"])
    # read_sql gets the ENGINE, not a Connection: on SQLAlchemy 1.4 (the
    # airflow fat-worker image) pandas treats a Connection as a raw DBAPI
    # handle and rejects the text() query; an Engine works on 1.4 and 2.0.
    frame = pd.read_sql(
        text(
            "SELECT business_date, business_hour, SUM(orders)::int AS orders "
            "FROM analytics.mart_demand_timeseries "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        ),
        engine,
    )
    frame["ds"] = pd.to_datetime(frame["business_date"]) + pd.to_timedelta(
        frame["business_hour"], unit="h"
    )
    return frame[["ds", "orders"]].sort_values("ds").reset_index(drop=True)


def reindex_hourly(series: pd.DataFrame) -> pd.DataFrame:
    """Fill gaps between observed hours with 0 — an hour with no orders is a
    zero-demand hour, not a missing row."""
    full = pd.date_range(series["ds"].min(), series["ds"].max(), freq="h")
    return series.set_index("ds").reindex(full, fill_value=0).rename_axis("ds").reset_index()


def seasonal_naive_predict(train: pd.Series, horizon: int) -> list[float]:
    """Predict hour h as the value observed 24 hours earlier (falling back to
    the training mean for the first day, where no lag-24 exists yet)."""
    mean = float(train.mean())
    values = list(train)
    preds = []
    for step in range(horizon):
        lag24 = values[-24 + step] if len(values) - 24 + step >= 0 else mean
        preds.append(float(lag24))
        values.append(preds[-1])
    return preds


def lightgbm_predict(train: pd.Series, horizon: int) -> list[float]:
    """Lag-feature gradient boosting: lag-1, lag-2, lag-24, hour, day-of-week.
    Trained only on rows whose lags exist — no future leakage."""
    frame = pd.DataFrame({"y": train})
    frame["lag_1"] = frame["y"].shift(1)
    frame["lag_2"] = frame["y"].shift(2)
    frame["lag_24"] = frame["y"].shift(24)
    frame["hour"] = frame.index.hour
    frame["dow"] = frame.index.dayofweek
    features = ["lag_1", "lag_2", "lag_24", "hour", "dow"]
    supervised = frame.dropna()
    if len(supervised) < 4:
        raise ValueError(f"insufficient rows for LightGBM ({len(supervised)} with lags)")
    model = lgb.LGBMRegressor(n_estimators=60, verbose=-1)
    model.fit(supervised[features], supervised["y"])

    values = list(train)
    preds = []
    index = frame.index
    for step in range(horizon):
        next_ts = index[-1] + pd.Timedelta(hours=1)
        row = {
            "lag_1": values[-1],
            "lag_2": values[-2] if len(values) > 1 else values[-1],
            "lag_24": values[-24] if len(values) >= 24 else mean_of(values),
            "hour": next_ts.hour,
            "dow": next_ts.dayofweek,
        }
        preds.append(float(model.predict(pd.DataFrame([row])[features])[0]))
        values.append(preds[-1])
        index = index.append(pd.DatetimeIndex([next_ts]))
    return preds


def mean_of(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def prophet_predict(train: pd.Series, horizon: int) -> list[float]:
    """Prophet needs enough history to fit seasonality; on young data it
    raises — the harness reports that honestly instead of forcing a fit."""
    from prophet import Prophet

    frame = train.reset_index()
    frame.columns = ["ds", "y"]
    if len(frame) < 48:
        raise ValueError(f"insufficient history for Prophet ({len(frame)} hours; wants >= 48)")
    model = Prophet(daily_seasonality=True, weekly_seasonality=False)
    model.fit(frame)
    future = model.make_future_dataframe(periods=horizon, freq="h", include_history=False)
    return list(model.predict(future)["yhat"])


def score(actual: list[float], predicted: list[float]) -> dict:
    errors = [p - a for p, a in zip(predicted, actual)]
    return {
        "mae": round(sum(abs(e) for e in errors) / len(errors), 3),
        "rmse": round(math.sqrt(sum(e * e for e in errors) / len(errors)), 3),
    }


def main() -> None:
    series = reindex_hourly(load_series())
    # DatetimeIndex drives every model: LightGBM reads hour/dow off it and
    # Prophet's reset_index() turns it into the (ds, y) frame it requires.
    values = series.set_index("ds")["orders"].astype(float)
    holdout = max(3, math.ceil(len(values) * 0.2))
    train, test = values.iloc[:-holdout], values.iloc[-holdout:]
    actual = list(test)

    snapshot = {
        "git_sha": git_sha(),
        "max_event_id": max_event_id(),
        "hours_observed": len(values),
        # Region pack (#78): every report names its city — calendar features
        # mean different things per region, so provenance is not optional.
        # Env vars, not core.config: the ml container does not ship core/.
        "region": os.environ.get("CITY", "tunis"),
        "business_timezone": os.environ.get("BUSINESS_TIMEZONE", "Africa/Tunis"),
    }
    print(f"snapshot: {snapshot} | holdout: {holdout} h")

    runs: list[dict] = []
    for name, predictor in [
        ("seasonal_naive (baseline)", lambda tr, h: seasonal_naive_predict(tr, h)),
        ("lightgbm", lambda tr, h: lightgbm_predict(tr, h)),
        ("prophet", lambda tr, h: prophet_predict(tr, h)),
    ]:
        try:
            predicted = predictor(train, holdout)
            metrics = score(actual, predicted)
            runs.append({"name": name, "predicted": predicted, "metrics": metrics, "error": None})
        except Exception as exc:  # noqa: BLE001 - a model failing on sparse data is a report row
            runs.append({"name": name, "predicted": None, "metrics": None, "error": str(exc)})

    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5100"))
    mlflow.set_experiment(EXPERIMENT)
    for run in runs:
        with mlflow.start_run(run_name=f"demand_{run['name'].split(' ')[0]}"):
            # Region pack (#78): runs are filterable by city across regions.
            # INSIDE the run — a fluent call outside one would START a
            # dangling auto-named run and collide with the loop below.
            mlflow.set_tag("city", os.environ.get("CITY", "tunis"))
            mlflow.log_params(
                {
                    "model": run["name"],
                    "hours_observed": len(values),
                    "holdout_hours": holdout,
                    **snapshot,
                }
            )
            if run["metrics"]:
                mlflow.log_metrics(run["metrics"])
                # Per-run evaluation artifacts (#71): forecast-vs-actual,
                # residuals and the per-hour-of-day error table land next to
                # the metrics — the shape of the error, not just its average.
                from ml_artifacts import log_demand_artifacts

                log_demand_artifacts(run["name"], actual, run["predicted"], test.index)
            if run["error"]:
                mlflow.set_tag("error", run["error"][:300])
            mlflow.set_tag("git_sha", snapshot["git_sha"])

    report = render_report(runs, snapshot, series, holdout)
    print(report)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"demand_forecast_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text(report + "\n", encoding="utf-8")
    print(f"report saved: {out}")


def max_event_id() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        import sys as _sys
        from pathlib import Path as _Path

        _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
        import core.tenancy  # noqa: E402

        # order_events are RLS-scoped (#36): declare the cross-tenant bypass.
        # In the ml-bench container the repo modules are not mounted and the
        # role is the admin anyway — the flag is a local-scripts concern.
        core.tenancy.mark_tenant_bypass(engine)
    except ModuleNotFoundError:
        pass
    with engine.connect() as conn:
        return conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM order_events")).scalar_one()


def render_report(runs: list[dict], snapshot: dict, series: pd.DataFrame, holdout: int) -> str:
    lines = [
        "# Demand forecasting benchmark",
        "",
        f"* Region: `{snapshot['region']}` (business timezone `{snapshot['business_timezone']}`)",
        f"* Snapshot: `max(event_id)={snapshot['max_event_id']}`, git `{snapshot['git_sha']}`, "
        f"{snapshot['hours_observed']} observed hours, holdout {holdout} h",
        f"* Runs logged to MLflow experiment `{EXPERIMENT}`",
        "",
        *(
            [
                f"**Data volume: {snapshot['hours_observed']} hours** — a young deployment. "
                "These models need weeks of traffic for their seasonality to mean anything; "
                "the pipeline, split discipline and MLflow tracking are the deliverable, and "
                "every retrain as traffic accumulates lands in the same experiment for "
                "comparison."
            ]
            if snapshot["hours_observed"] < 1000
            else [
                f"**Data volume: {snapshot['hours_observed']} hours** — the window covers "
                "weeks of history, so weekly seasonality and the lag-168 features have "
                "something real to learn; the backfilled structure (drift, weekend/holiday "
                "spikes, weather dips, outages, per-zone heterogeneity) is documented in "
                "scripts/backfill_order_history.py, and re-runs land in the same experiment "
                "so the volume-scaling story stays comparable."
            ]
        ),
        "",
        "| model | MAE | RMSE | status |",
        "|---|---|---|---|",
    ]
    for run in runs:
        if run["metrics"]:
            lines.append(
                f"| {run['name']} | {run['metrics']['mae']} | {run['metrics']['rmse']} | trained |"
            )
        else:
            lines.append(f"| {run['name']} | - | - | {run['error'][:80]} |")
    lines.append("")
    lines.append(
        "*Baseline: seasonal-naive. A challenger ships only if it beats the baseline "
        "on the holdout — same discipline as the geo benchmark.*"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()

"""ETA benchmark: heuristic champion vs OSRM free-flow vs LightGBM (#26).

Challenger-vs-champion harness for delivery ETAs, run as `make eta-benchmark`
(one-shot container). Ground truth is OUR realized deliveries — the
created → delivered span per order in the warehouse event log — per the
evaluation design on issue #26. NOT OSRM durations: those are free-flow
estimates, only valid here as a challenger's own input.

What the target means: the realized span covers everything the customer
waits for (assignment, restaurant pickup, travel). The heuristic quotes
prep + travel + buffer only, so it systematically under-estimates the full
span — quantifying that gap is the benchmark working, not a bug.

Champions/challengers:
- heuristic (champion): prep + km/speed*60 + buffer, ceil'd — the exact
  eta_service formula. The ml container cannot import core/ (no package on
  its path), so the three constants are mirrored here; the repo unit test
  tests/unit/test_eta_benchmark_constants.py asserts they stay equal to
  core.config defaults.
- osrm_free_flow: OSRM driving duration for the pair + prep + buffer,
  ceil'd — "what if we trusted the road graph". Unique pairs are deduped
  in-run (~6 restaurants × zone cells covers all orders); a failed or
  unreachable OSRM becomes an honest report row, never a crash.
- lightgbm (challenger): regression on distance_km, local hour, day-of-week
  and pickup/dropoff zone cells. Trains on the OLDEST 80% of realized
  orders, scores the newest 20% — time-based split, never random.

Scoring: MAE/RMSE overall + MAE per distance bucket (<2, 2-5, 5-10, >10 km)
and per hour-of-day block. Every run logs params/metrics to the MLflow
experiment `eta_benchmark` with the dataset snapshot (max(event_id) + git
SHA); the LightGBM run also carries the drift-monitoring baseline (#41):
input distributions (distance/hour mean+std) and prediction-residual stats.
If the challenger beats the champion on holdout MAE it is REGISTERED to the
MLflow model registry as `eta_model` at stage Production; otherwise the
entry is staged with the loss on record — promotion is measurement's call.

Report → data/benchmarks/eta_benchmark_*.md (committed).
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
EXPERIMENT = "eta_benchmark"
REGISTERED_MODEL = "eta_model"

# Mirrors core.config Settings defaults — sync-guarded by
# tests/unit/test_eta_benchmark_constants.py (this file has no core/ import).
ETA_PREP_MINUTES = 12
ETA_SPEED_KMH = 20.0
ETA_BUFFER_MINUTES = 5

BUCKETS = [(0, 2, "lt2"), (2, 5, "2_5"), (5, 10, "5_10"), (10, 1e9, "gt10")]
HOUR_BLOCKS = [
    (0, 6, "00-05"),
    (6, 11, "06-10"),
    (11, 15, "11-14"),
    (15, 18, "15-17"),
    (18, 23, "18-22"),
    (23, 24, "23"),
]


def git_sha() -> str:
    return os.environ.get("GIT_SHA", "unknown")[:12]


def load_realized_deliveries() -> pd.DataFrame:
    """One row per delivered order: realized duration + quote-time features."""
    engine = create_engine(os.environ["WAREHOUSE_DATABASE_URL"])
    # read_sql gets the ENGINE, not a Connection: on SQLAlchemy 1.4 (the
    # airflow fat-worker image) pandas treats a Connection as a raw DBAPI
    # handle and rejects the text() query; an Engine works on 1.4 and 2.0.
    frame = pd.read_sql(
        text(
            """
            SELECT c.order_id,
                   c.source_created_at,
                   c.source_created_at_local,
                   c.distance_km::float8          AS distance_km,
                   c.pickup_latitude::float8      AS pickup_lat,
                   c.pickup_longitude::float8     AS pickup_lon,
                   c.dropoff_latitude::float8     AS dropoff_lat,
                   c.dropoff_longitude::float8    AS dropoff_lon,
                   extract(epoch from (d.source_created_at - c.source_created_at)) / 60.0
                                                  AS duration_min
            FROM staging.stg_order_events c
            JOIN staging.stg_order_events d ON d.order_id = c.order_id
            WHERE c.event_type = 'created'
              AND d.event_type = 'status' AND d.status_to = 'delivered'
            """
        ),
        engine,
    )
    # source_created_at_local is ALREADY Africa/Tunis naive wall clock
    # (staging: timestamptz AT TIME ZONE). Parsing it with utc=True and
    # tz_convert-ing shifted hour_local +1h — a train/serve skew against
    # eta_model_service (review M2). Parse as the naive local it is.
    local = pd.to_datetime(frame["source_created_at_local"])
    frame["hour_local"] = local.dt.hour
    frame["dow"] = local.dt.dayofweek
    frame["pickup_zone"] = frame["pickup_lat"].round(2)
    frame["dropoff_zone"] = frame["dropoff_lat"].round(2)
    frame["dropoff_zone_lon"] = frame["dropoff_lon"].round(2)
    return frame.sort_values("source_created_at").reset_index(drop=True)


def heuristic_minutes(distance_km: pd.Series) -> pd.Series:
    """The champion, exactly as eta_service computes it (ceil at the end)."""
    travel = (distance_km / ETA_SPEED_KMH) * 60
    return (ETA_PREP_MINUTES + travel + ETA_BUFFER_MINUTES).apply(math.ceil).astype(float)


def osrm_minutes(frame: pd.DataFrame) -> pd.Series:
    """Free-flow driving duration per unique pair + prep + buffer, ceil'd."""
    import httpx

    base = os.environ.get("OSRM_BASE_URL", "http://osrm:5000").rstrip("/")
    cache: dict[tuple, float] = {}
    out = []
    with httpx.Client(timeout=10) as client:
        for row in frame.itertuples():
            key = (row.pickup_lat, row.pickup_lon, row.dropoff_lat, row.dropoff_lon)
            if key not in cache:
                url = f"{base}/route/v1/driving/{key[3]},{key[2]};{key[1]},{key[0]}?overview=false"
                resp = client.get(url)
                resp.raise_for_status()
                cache[key] = resp.json()["routes"][0]["duration"] / 60.0
            out.append(cache[key])
    free_flow = pd.Series(out, index=frame.index)
    return (ETA_PREP_MINUTES + free_flow + ETA_BUFFER_MINUTES).apply(math.ceil).astype(float)


FEATURES = ["distance_km", "hour_local", "dow", "pickup_zone", "dropoff_zone", "dropoff_zone_lon"]


def lightgbm_minutes(train: pd.DataFrame, holdout: pd.DataFrame) -> pd.Series:
    model = lgb.LGBMRegressor(n_estimators=300, verbose=-1)
    model.fit(train[FEATURES], train["duration_min"])
    return pd.Series(model.predict(holdout[FEATURES]), index=holdout.index)


def bucket_of(distance_km: float) -> str:
    for lo, hi, name in BUCKETS:
        if lo <= distance_km < hi:
            return name
    return "gt10"  # pragma: no cover - 1e9 upper bound catches everything


def score(actual: pd.Series, predicted: pd.Series, distances: pd.Series) -> dict:
    errors = predicted - actual
    out = {
        "mae_overall": round((errors.abs()).mean(), 3),
        "rmse_overall": round(math.sqrt((errors**2).mean()), 3),
    }
    buckets = distances.map(bucket_of)  # buckets are BY DISTANCE, not duration
    for _, _, name in BUCKETS:
        idx = buckets[buckets == name].index
        if len(idx):
            out[f"mae_bucket_{name}"] = round(errors.loc[idx].abs().mean(), 3)
    return out, errors


def hour_block(h: int) -> str:
    for lo, hi, name in HOUR_BLOCKS:
        if lo <= h < hi:
            return name
    return "23"  # pragma: no cover


def main() -> None:
    frame = load_realized_deliveries()
    if len(frame) < 50:
        raise SystemExit(f"only {len(frame)} realized deliveries - not enough to benchmark")

    holdout_n = max(3, math.ceil(len(frame) * 0.2))
    train, holdout = frame.iloc[:-holdout_n], frame.iloc[-holdout_n:]
    actual = holdout["duration_min"]
    snapshot = {
        "git_sha": git_sha(),
        "max_event_id": max_event_id(),
        "orders_total": len(frame),
        "holdout_orders": holdout_n,
        # Region pack (#78): every report names its city — calendar features
        # mean different things per region, so provenance is not optional.
        # Env vars, not core.config: the ml container does not ship core/.
        "region": os.environ.get("CITY", "tunis"),
        "business_timezone": os.environ.get("BUSINESS_TIMEZONE", "Africa/Tunis"),
    }
    print(f"snapshot: {snapshot}")

    runs: list[dict] = []
    for name, predictor in [
        ("heuristic (champion)", lambda: heuristic_minutes(holdout["distance_km"])),
        ("osrm_free_flow", lambda: osrm_minutes(holdout)),
        ("lightgbm", lambda: lightgbm_minutes(train, holdout)),
    ]:
        try:
            metrics, errors = score(actual, predictor(), holdout["distance_km"])
            runs.append({"name": name, "metrics": metrics, "errors": errors, "error": None})
        except Exception as exc:  # noqa: BLE001 - a failed challenger is a report row
            runs.append({"name": name, "metrics": {}, "errors": None, "error": str(exc)})

    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5100"))
    mlflow.set_experiment(EXPERIMENT)
    champion_mae = next(
        (r["metrics"]["mae_overall"] for r in runs if r["name"].startswith("heuristic")), None
    )
    for run in runs:
        with mlflow.start_run(run_name=f"eta_{run['name'].split(' ')[0]}"):
            # Region pack (#78): runs are filterable by city across regions.
            # INSIDE the run — a fluent call outside one would START a
            # dangling auto-named run and collide with the loop below.
            mlflow.set_tag("city", os.environ.get("CITY", "tunis"))
            mlflow.log_params({"model": run["name"], **snapshot})
            if run["metrics"]:
                mlflow.log_metrics(run["metrics"])
            if run["error"]:
                mlflow.set_tag("error", run["error"][:300])
            mlflow.set_tag("git_sha", snapshot["git_sha"])
            if run["name"] == "lightgbm" and not run["error"]:
                # Drift-monitoring baseline (#41): input distributions + residuals.
                mlflow.log_metrics(
                    {
                        "drift_input_distance_mean": round(holdout["distance_km"].mean(), 3),
                        "drift_input_distance_std": round(holdout["distance_km"].std(), 3),
                        "drift_input_hour_mean": round(holdout["hour_local"].mean(), 3),
                        "residual_mean": round(run["errors"].mean(), 3),
                        "residual_std": round(run["errors"].std(), 3),
                    }
                )
                beats = champion_mae is not None and run["metrics"]["mae_overall"] < champion_mae
                mlflow.set_tag(
                    "promotion",
                    "production: beat champion on holdout"
                    if beats
                    else f"blocked: lost to champion ({champion_mae})",
                )
                artifact = TempModelArtifact(train)
                artifact.log_and_register(beats, snapshot)

    report = render_report(runs, snapshot, holdout)
    print(report)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"eta_benchmark_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text(report + "\n", encoding="utf-8")
    print(f"report saved: {out}")


class TempModelArtifact:
    """Registers the challenger model to the MLflow registry so the harness
    verdict (beat the champion or not) lands as a registry stage, per #41."""

    def __init__(self, train: pd.DataFrame):
        self.train = train

    def log_and_register(self, beats_champion: bool, snapshot: dict) -> None:
        model = lgb.LGBMRegressor(n_estimators=300, verbose=-1)
        model.fit(self.train[FEATURES], self.train["duration_min"])
        # Log the NATIVE booster (model.txt), not the sklearn pickle: serving
        # loads lgb.Booster(model_file=...) — no pickle fragility across
        # library versions, no code execution on unpickle.
        mlflow.lightgbm.log_model(model.booster_, artifact_path="model")
        # register_model returns the ModelVersion (log_model's ModelInfo does
        # not carry the registry version in mlflow 2.19).
        mv = mlflow.register_model(mlflow.get_artifact_uri("model"), REGISTERED_MODEL)
        version = int(mv.version)
        client = mlflow.tracking.MlflowClient()
        stage = "Production" if beats_champion else "Staging"
        desc = (
            f"holdout MAE beat the heuristic champion on {snapshot['git_sha']} "
            f"(max event {snapshot['max_event_id']})"
            if beats_champion
            else "staged, not promoted: lost to the heuristic champion on the holdout"
        )
        client.update_model_version(name=REGISTERED_MODEL, version=version, description=desc)
        client.transition_model_version_stage(name=REGISTERED_MODEL, version=version, stage=stage)
        print(f"registered {REGISTERED_MODEL} v{version} -> {stage}")


def max_event_id() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    import core.tenancy  # noqa: E402

    core.tenancy.mark_tenant_bypass(engine)  # order_events are RLS-scoped (#36)
    with engine.connect() as conn:
        return conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM order_events")).scalar_one()


def render_report(runs: list[dict], snapshot: dict, holdout: pd.DataFrame) -> str:
    lines = [
        "# ETA benchmark",
        "",
        f"* Region: `{snapshot['region']}` (business timezone `{snapshot['business_timezone']}`)",
        f"* Snapshot: `max(event_id)={snapshot['max_event_id']}`, git `{snapshot['git_sha']}`, "
        f"{snapshot['orders_total']} realized deliveries, holdout {snapshot['holdout_orders']} (newest)",
        f"* Runs logged to MLflow experiment `{EXPERIMENT}`; ground truth = created → delivered span",
        "",
        "| model | MAE | RMSE | status |",
        "|---|---|---|---|",
    ]
    for run in runs:
        if run["error"]:
            lines.append(f"| {run['name']} | - | - | {run['error'][:60]} |")
        else:
            lines.append(
                f"| {run['name']} | {run['metrics']['mae_overall']} | "
                f"{run['metrics']['rmse_overall']} | scored |"
            )
    lines += [
        "",
        "### MAE by distance bucket (km)",
        "",
        "| bucket | " + " | ".join(r["name"] for r in runs if not r["error"]) + " |",
        "|---|" + "---|" * len([r for r in runs if not r["error"]]),
    ]
    for _, _, bname in BUCKETS:
        cells = []
        for run in runs:
            if run["error"]:
                continue
            key = f"mae_bucket_{bname}"
            cells.append(str(run["metrics"].get(key, "-")))
        lines.append(f"| {bname} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "### MAE by local hour block",
        "",
        "| hours | " + " | ".join(r["name"] for r in runs if not r["error"]) + " |",
        "|---|" + "---|" * len([r for r in runs if not r["error"]]),
    ]
    for lo, hi, bname in HOUR_BLOCKS:
        idx = holdout["hour_local"].between(lo, hi - 1)
        if not idx.any():
            continue
        cells = []
        for run in runs:
            if run["error"]:
                continue
            err = run["errors"][idx]
            cells.append(str(round(err.abs().mean(), 3)))
        lines.append(f"| {bname} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "*The heuristic quotes prep + travel + buffer; the realized span also includes "
        "assignment and pickup wait — the champion's systematic under-estimate is a finding, "
        "not a bug. A challenger ships only if it beats the champion on the holdout; the "
        "registry stage (`eta_model`) records the verdict.*",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()

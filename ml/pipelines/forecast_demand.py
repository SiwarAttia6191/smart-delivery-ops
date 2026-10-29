# mypy: ignore-errors
# ml/ sits outside the typing gate (same as dags/)
"""Serving-side demand forecast (#25): pooled LightGBM → ops demand_forecasts.

The benchmark (ml/training/benchmark_demand.py) decides which model family
wins on the holdout. This job makes the SERVING choice explicitly: a pooled
LightGBM across all zones, not the benchmark's global winner (Prophet, ahead
by a 0.012 MAE margin - noise). Reason: pricing needs a PER ZONE-HOUR
forecast, and ~27 zone cells over weeks of history is far too sparse for one
univariate fit per zone. Pooling every zone into a single gradient-boosted
model with lag + zone-coordinate + calendar features is the standard
architecture at this density; the margin above does not justify losing
per-zone resolution.

Writes the next 24 hours of zone-hour forecasts into the ops database's
``demand_forecasts`` table (upsert, idempotent), where
domain/services/demand_service.py picks them up on the quote path. Runs in
the ml-bench container (make ml-forecast), AFTER the warehouse marts are
built - it reads analytics.mart_demand_timeseries like the benchmark does.

Time policy: the mart's business_date/business_hour are LOCAL wall clock in
the configured BUSINESS_TIMEZONE; forecast_hour_utc converts them through
the tz database so the API can look rows up with UTC wall-clock hours.
"""

import os

import lightgbm as lgb
import mlflow
import pandas as pd
from sqlalchemy import create_engine, text

HORIZON_HOURS = 24
# Local↔UTC conversion goes through the tz database (#78): forecast rows
# are keyed by UTC wall clock, the mart's business hours are LOCAL (the
# configured BUSINESS_TIMEZONE). REVIEW LESSON: hand arithmetic (+1/-1)
# has bitten twice — localize then convert, never offset math.
TZ_NAME = os.environ.get("BUSINESS_TIMEZONE", "Africa/Tunis")


def local_hours_to_utc(ds_local: pd.Timestamp) -> pd.Timestamp:
    """Naive local business hour -> tz-aware UTC hour (DST-safe)."""
    return ds_local.tz_localize(TZ_NAME).tz_convert("UTC")


LAGS = (1, 2, 24)
CALENDAR = ("hour", "dow")
ZONE_FEATURES = ("zone_lat", "zone_lon")
FEATURES = [f"lag_{n}" for n in LAGS] + list(CALENDAR) + list(ZONE_FEATURES)


def load_zone_frames() -> dict[tuple[float, float], pd.DataFrame]:
    """One hourly-reindexed (ds, orders) frame per zone cell.

    Every zone is reindexed to the SAME global span, not its own: a zone
    whose last order was weeks ago would otherwise forecast "next 24h" from
    that stale endpoint. An hour with no orders is zero demand, not missing
    data — same convention as the benchmark's global reindex.
    """
    engine = create_engine(os.environ["WAREHOUSE_DATABASE_URL"])
    # read_sql gets the ENGINE, not a Connection: on SQLAlchemy 1.4 (the
    # airflow fat-worker image) pandas treats a Connection as a raw DBAPI
    # handle and rejects the text() query; an Engine works on 1.4 and 2.0.
    frame = pd.read_sql(
        text(
            "SELECT zone_lat, zone_lon, business_date, business_hour, orders "
            "FROM analytics.mart_demand_timeseries ORDER BY 1, 2, 3, 4"
        ),
        engine,
    )
    frame["ds"] = pd.to_datetime(frame["business_date"]) + pd.to_timedelta(
        frame["business_hour"], unit="h"
    )
    global_start = frame["ds"].min()
    # Anchor the forecast window at wall-clock NOW (review M1): the series
    # ends at the previous CONFIGURED-zone hour so the 24h horizon always
    # covers the hours the API will actually look up.
    now_local_hour = pd.Timestamp.now(tz="UTC").tz_convert(TZ_NAME).floor("h").tz_localize(None)
    # The OBSERVED series always ends at the PREVIOUS local hour: the
    # current hour is partial (its realized count is incomplete), so it is
    # PREDICTED, never observed — the recursive loop then starts at
    # origin+1h = the current local hour, which is exactly the hour
    # derive_demand_ratio looks up, whenever the job ran. Gap hours since
    # the last mart build are zero-filled (pessimistic lags; the next run
    # corrects them); a partial current hour in the mart is truncated.
    observed_end = now_local_hour - pd.Timedelta(hours=1)
    zones: dict[tuple[float, float], pd.DataFrame] = {}
    for (lat, lon), zone_frame in frame.groupby(["zone_lat", "zone_lon"]):
        series = zone_frame.set_index("ds")["orders"].astype(float)
        full = pd.date_range(global_start, observed_end, freq="h")
        zones[(float(lat), float(lon))] = series.reindex(full, fill_value=0.0).rename_axis("ds")
    return zones


def build_pooled_training(zones: dict[tuple[float, float], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for (lat, lon), series in zones.items():
        frame = pd.DataFrame({"y": series})
        for n in LAGS:
            frame[f"lag_{n}"] = frame["y"].shift(n)
        frame["hour"] = frame.index.hour
        frame["dow"] = frame.index.dayofweek
        frame["zone_lat"] = lat
        frame["zone_lon"] = lon
        rows.append(frame.dropna())
    return pd.concat(rows, ignore_index=True)


def forecast_zones(
    zones: dict[tuple[float, float], pd.DataFrame], model: lgb.LGBMRegressor
) -> list[dict]:
    """Recursive one-step-ahead forecast per zone; each zone consumes its own
    lag history so a busy zone and a quiet zone diverge naturally."""
    predictions = []
    for (lat, lon), series in zones.items():
        values = list(series)
        index = series.index
        for _ in range(HORIZON_HOURS):
            next_ts = index[-1] + pd.Timedelta(hours=1)
            # Young zones may have < 24h of history: fall back like the
            # benchmark's predictors do (mean for the missing lag-24) rather
            # than letting one sparse cell kill the whole run.
            row = {
                "lag_1": values[-1],
                "lag_2": values[-2] if len(values) > 1 else values[-1],
                "lag_24": values[-24] if len(values) >= 24 else sum(values) / len(values),
                "hour": next_ts.hour,
                "dow": next_ts.dayofweek,
                "zone_lat": lat,
                "zone_lon": lon,
            }
            pred = max(0.0, float(model.predict(pd.DataFrame([row])[FEATURES])[0]))
            predictions.append(
                {"zone_lat": lat, "zone_lon": lon, "ds": next_ts, "predicted_orders": pred}
            )
            values.append(pred)
            index = index.append(pd.DatetimeIndex([next_ts]))
    return predictions


def store_forecasts(predictions: list[dict]) -> int:
    """Upsert into ops.demand_forecasts; prune rows older than 48h."""
    engine = create_engine(os.environ["DATABASE_URL"])
    upsert = text(
        """
        INSERT INTO demand_forecasts
            (zone_lat, zone_lon, forecast_hour_utc, predicted_orders, created_at)
        VALUES
            (:zlat, :zlon, :hour, :pred, now())
        ON CONFLICT (zone_lat, zone_lon, forecast_hour_utc)
        DO UPDATE SET predicted_orders = EXCLUDED.predicted_orders, created_at = now()
        """
    )
    window_start = local_hours_to_utc(min(p["ds"] for p in predictions))
    window_end = local_hours_to_utc(max(p["ds"] for p in predictions))
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS demand_forecasts (
                    zone_lat DOUBLE PRECISION NOT NULL,
                    zone_lon DOUBLE PRECISION NOT NULL,
                    forecast_hour_utc TIMESTAMP WITH TIME ZONE NOT NULL,
                    predicted_orders DOUBLE PRECISION NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                    PRIMARY KEY (zone_lat, zone_lon, forecast_hour_utc)
                )
                """
            )
        )
        for p in predictions:
            # local business hour → UTC wall-clock hour (tz database, #78)
            hour_utc = local_hours_to_utc(p["ds"])
            conn.execute(
                upsert,
                {
                    "zlat": p["zone_lat"],
                    "zlon": p["zone_lon"],
                    "hour": hour_utc.to_pydatetime(),
                    "pred": round(p["predicted_orders"], 3),
                },
            )
        # The table is a SNAPSHOT of this run's window, not history: prune
        # everything outside the fresh window's bounds, computed from the
        # predictions themselves (a table-derived MAX includes the stale
        # rows and lets them survive - bit on the first attempt).
        conn.execute(
            text(
                "DELETE FROM demand_forecasts WHERE forecast_hour_utc < :ws OR forecast_hour_utc > :we"
            ),
            {
                "ws": window_start.to_pydatetime(),
                "we": window_end.to_pydatetime(),
            },
        )
    return len(predictions)


def main() -> None:
    zones = load_zone_frames()
    if not zones:
        raise SystemExit("mart_demand_timeseries is empty - run `make dbt` first")

    training = build_pooled_training(zones)
    model = lgb.LGBMRegressor(n_estimators=120, verbose=-1)
    model.fit(training[FEATURES], training["y"])

    predictions = forecast_zones(zones, model)
    written = store_forecasts(predictions)

    git_sha = os.environ.get("GIT_SHA", "unknown")[:12]
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5100"))
    mlflow.set_experiment("demand_forecasting")
    with mlflow.start_run(run_name="demand_forecast_serving"):
        # Traceability (same tags the benchmarks set): which code and which
        # city produced this serving run — the runs table grows hourly once
        # the Airflow loop is live, so provenance is what keeps it readable.
        mlflow.set_tag("git_sha", git_sha)
        mlflow.set_tag("city", os.environ.get("CITY", "tunis"))
        mlflow.log_params(
            {
                "model": "pooled_lightgbm",
                "zones": len(zones),
                "training_rows": len(training),
                "horizon_hours": HORIZON_HOURS,
                "forecasts_written": written,
                "git_sha": git_sha,
            }
        )
        mlflow.log_metric(
            "mean_predicted_hourly_orders",
            sum(p["predicted_orders"] for p in predictions) / len(predictions),
        )

    total = sum(p["predicted_orders"] for p in predictions)
    print(
        f"forecast: {written} zone-hours written across {len(zones)} zones (next {HORIZON_HOURS}h)"
    )
    print(f"expected demand: {total:.1f} orders / {len(zones)} zones / {HORIZON_HOURS}h")
    print("serving: the API derives demand_ratio from demand_forecasts on the quote path")


if __name__ == "__main__":
    main()

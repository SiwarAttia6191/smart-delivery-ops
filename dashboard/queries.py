"""Dashboard data layer — pure SQL-over-marts, no Streamlit imports.

The Phase 4 dashboard reads the WAREHOUSE marts only (#27's acceptance);
the one deliberate exception is the ops `demand_forecasts` table for the
forecast-vs-actual chart (the serving DAG writes it to the ops DB) and the
`tenants` table for human-readable tenant names. Everything else is dbt
output: if a number shows up here, a mart built it.

Kept import-light (pandas + sqlalchemy only) so the query layer is
testable and reusable without a UI process.
"""

import os

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

_engines: dict[str, Engine] = {}


def warehouse_engine() -> Engine:
    if "wh" not in _engines:
        _engines["wh"] = create_engine(os.environ["WAREHOUSE_DATABASE_URL"], pool_pre_ping=True)
    return _engines["wh"]


def ops_engine() -> Engine:
    if "ops" not in _engines:
        _engines["ops"] = create_engine(os.environ["OPS_DATABASE_URL"], pool_pre_ping=True)
    return _engines["ops"]


def _read(engine: Engine, sql: str, **params) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or None)


def daily_summary(wh: Engine) -> pd.DataFrame:
    """mart_daily_order_summary: one row per day — the volume/revenue/speed
    backbone of the KPI cards."""
    return _read(
        wh,
        """
        select order_date, orders_created, orders_delivered, avg_delivery_minutes,
               revenue, avg_distance_km, avg_surge_multiplier
        from analytics.mart_daily_order_summary
        order by order_date
        """,
    )


def tenant_daily(wh: Engine) -> pd.DataFrame:
    """mart_tenant_daily: per-tenant daily funnel — the tenant filter's data."""
    return _read(
        wh,
        """
        select tenant_id, business_date, orders, delivered, revenue, fulfillment_pct,
               avg_delivery_minutes, avg_surge_multiplier
        from analytics.mart_tenant_daily
        order by business_date
        """,
    )


def tenant_names(ops: Engine) -> dict[int, str]:
    rows = _read(ops, "select id, slug, name from tenants order by id")
    return {int(r.id): str(r.name) for r in rows.itertuples()}


def demand_supply_by_zone(wh: Engine) -> pd.DataFrame:
    """Aggregated to the zone level: total orders and average online supply."""
    return _read(
        wh,
        """
        select zone_lat, zone_lon,
               sum(orders)                as orders,
               round(avg(available_drivers), 1) as avg_available_drivers,
               round(avg(drivers_per_order), 2) as avg_drivers_per_order
        from analytics.mart_demand_supply_by_zone_hour
        where orders > 0
        group by 1, 2
        order by orders desc
        """,
    )


def driver_utilization(wh: Engine) -> pd.DataFrame:
    return _read(
        wh,
        """
        select driver_id, orders_assigned, orders_delivered,
               avg_match_distance_km, avg_assignment_to_delivery_minutes
        from analytics.mart_driver_utilization
        order by orders_delivered desc
        """,
    )


def forecast_vs_actual(
    ops: Engine, wh: Engine, business_timezone: str = "Africa/Tunis"
) -> pd.DataFrame:
    """The serving loop's next-24h predictions against realized demand.

    Forecasts live in ops.demand_forecasts (UTC hour, per zone); actuals
    grain on the LOCAL hour in the marts — the UTC->local conversion happens
    here, in one place, with the tz database."""
    forecasts = _read(
        ops,
        """
        select zone_lat, zone_lon, forecast_hour_utc, predicted_orders
        from demand_forecasts
        where forecast_hour_utc >= date_trunc('hour', now()) - interval '48 hours'
        """,
    )
    if forecasts.empty:
        return pd.DataFrame(
            columns=[
                "zone_lat",
                "zone_lon",
                "business_date",
                "business_hour",
                "predicted",
                "actual",
            ]
        )
    actuals = _read(
        wh,
        """
        select zone_lat, zone_lon, business_date, business_hour, sum(orders) as actual
        from analytics.mart_demand_timeseries
        group by 1, 2, 3, 4
        """,
    )
    forecasts["local_hour"] = pd.to_datetime(
        forecasts["forecast_hour_utc"], utc=True
    ).dt.tz_convert(business_timezone)
    forecasts["business_hour"] = forecasts["local_hour"].dt.hour
    forecasts["business_date"] = forecasts["local_hour"].dt.date.astype(str)
    # the two sides store zone coordinates in different types (float8 vs
    # numeric) — the merge keys must match exactly or every actual comes
    # back NaN and the chart silently shows one line
    for frame in (forecasts, actuals):
        frame["zone_lat"] = frame["zone_lat"].astype(float)
        frame["zone_lon"] = frame["zone_lon"].astype(float)
    actuals["business_date"] = actuals["business_date"].astype(str)
    merged = forecasts.merge(
        actuals, on=["zone_lat", "zone_lon", "business_date", "business_hour"], how="left"
    )
    merged["hour"] = pd.to_datetime(merged["local_hour"].astype(str))
    return (
        merged.groupby(["hour"], as_index=False)[["predicted_orders", "actual"]]
        .sum()
        .rename(columns={"predicted_orders": "predicted"})
    )

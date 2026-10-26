"""Live demand ratio from the forecast table — the last hop of the #25 loop.

The forecast batch job (ml/pipelines/forecast_demand.py) writes per zone-hour
expected demand into ``demand_forecasts``. When a client quotes or places an
order without an explicit demand_ratio, this service derives it: observed
orders in the current zone-hour so far, divided by the forecast's expectation
for the elapsed part of the hour, clamped to the surge tier range.

Falls back to 1.0 (no surge, no discount) whenever anything is missing:
no forecast covers the zone-hour, the hour just started (too little signal),
or nothing is expected in this zone. Neutral is the honest default — a
missing forecast must never spike a customer's fee.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domain.models import DemandForecast, Order, OrderStatus

ZONE_DECIMALS = 2  # same zone cells the warehouse marts grain on
HALF_CELL = 0.5 / (10**ZONE_DECIMALS)  # range predicate, not float equality:
# Postgres compares numeric = float8 exactly, so round(col, 2) == 36.84 can
# silently miss. [cell - 0.005, cell + 0.005) is the same set, safely.
MIN_ELAPSED_MINUTES = 5.0  # below this the intra-hour rate is noise
RATIO_FLOOR = 0.5  # never discount below half of expected
RATIO_CAP = 2.5  # pricing tiers hard-cap at 2.0; leave headroom above it


def derive_demand_ratio(
    session: Session,
    zone_lat: float,
    zone_lon: float,
    now: datetime | None = None,
) -> float:
    now = now or datetime.now(UTC)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    elapsed_minutes = (now - hour_start).total_seconds() / 60.0

    zone_lat, zone_lon = round(zone_lat, ZONE_DECIMALS), round(zone_lon, ZONE_DECIMALS)

    forecast = session.get(DemandForecast, (zone_lat, zone_lon, hour_start))
    if forecast is None or forecast.predicted_orders <= 0:
        return 1.0
    if elapsed_minutes < MIN_ELAPSED_MINUTES:
        return 1.0

    expected_so_far = forecast.predicted_orders * (elapsed_minutes / 60.0)
    if expected_so_far < 0.05:
        return 1.0

    observed = session.scalar(
        select(func.count())
        .select_from(Order)
        .where(
            Order.dropoff_latitude >= zone_lat - HALF_CELL,
            Order.dropoff_latitude < zone_lat + HALF_CELL,
            Order.dropoff_longitude >= zone_lon - HALF_CELL,
            Order.dropoff_longitude < zone_lon + HALF_CELL,
            Order.created_at >= hour_start,
            Order.created_at < hour_start + timedelta(minutes=60),
            Order.status != OrderStatus.cancelled,
        )
    )
    ratio = float(observed or 0) / expected_so_far
    return round(min(max(ratio, RATIO_FLOOR), RATIO_CAP), 2)

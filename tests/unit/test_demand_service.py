"""demand_service.derive_demand_ratio — the forecast→pricing last hop."""

from datetime import UTC, datetime, timedelta

from domain.models import DemandForecast, Order, OrderStatus
from domain.services import demand_service

ZONE = (36.8381, 10.2385)  # rounds to the (36.84, 10.24) cell


def _seed_forecast(db, predicted: float, hour_start: datetime) -> None:
    db.add(
        DemandForecast(
            zone_lat=36.84,
            zone_lon=10.24,
            forecast_hour_utc=hour_start,
            predicted_orders=predicted,
        )
    )
    db.commit()


def _seed_order(db, created_at: datetime, lat: float = 36.8381, lon: float = 10.2385) -> None:
    db.add(
        Order(
            tenant_id=1,
            customer_id=1,
            restaurant_id=1,
            status=OrderStatus.delivered,
            pickup_latitude=36.8,
            pickup_longitude=10.18,
            dropoff_latitude=lat,
            dropoff_longitude=lon,
            distance_km=3.0,
            delivery_fee=5.0,
            eta_minutes=20,
            created_at=created_at,
        )
    )
    db.commit()


def test_no_forecast_is_neutral(db):
    now = datetime(2026, 10, 26, 12, 30, tzinfo=UTC)
    assert demand_service.derive_demand_ratio(db, *ZONE, now=now) == 1.0


def test_hour_just_started_is_neutral(db):
    now = datetime(2026, 10, 26, 12, 2, tzinfo=UTC)
    _seed_forecast(db, predicted=10.0, hour_start=now.replace(minute=0))
    assert demand_service.derive_demand_ratio(db, *ZONE, now=now) == 1.0


def test_zero_forecast_is_neutral(db):
    now = datetime(2026, 10, 26, 12, 30, tzinfo=UTC)
    _seed_forecast(db, predicted=0.0, hour_start=now.replace(minute=0))
    assert demand_service.derive_demand_ratio(db, *ZONE, now=now) == 1.0


def test_ratio_scales_with_elapsed_hour(db):
    now = datetime(2026, 10, 26, 12, 30, tzinfo=UTC)
    hour_start = now.replace(minute=0)
    _seed_forecast(db, predicted=8.0, hour_start=hour_start)
    # half the hour gone, forecast says 8/hour → expected_so_far = 4
    _seed_order(db, created_at=hour_start + timedelta(minutes=10))
    _seed_order(db, created_at=hour_start + timedelta(minutes=20))
    _seed_order(db, created_at=hour_start + timedelta(minutes=29))
    assert demand_service.derive_demand_ratio(db, *ZONE, now=now) == 0.75


def test_ratio_is_capped_and_floored(db):
    now = datetime(2026, 10, 26, 12, 30, tzinfo=UTC)
    hour_start = now.replace(minute=0)
    _seed_forecast(db, predicted=0.1, hour_start=hour_start)
    for i in range(3):
        _seed_order(db, created_at=hour_start + timedelta(minutes=5 + i))
    # 3 observed / 0.05 expected ≈ 60 → capped
    assert demand_service.derive_demand_ratio(db, *ZONE, now=now) == demand_service.RATIO_CAP

    _seed_forecast(
        db, predicted=100.0, hour_start=hour_start.replace(minute=0) + timedelta(hours=1)
    )
    later = hour_start + timedelta(hours=1, minutes=30)
    # 0 observed / 50 expected → floored
    assert demand_service.derive_demand_ratio(db, *ZONE, now=later) == demand_service.RATIO_FLOOR


def test_orders_outside_hour_or_zone_are_ignored(db):
    now = datetime(2026, 10, 26, 12, 30, tzinfo=UTC)
    hour_start = now.replace(minute=0)
    _seed_forecast(db, predicted=4.0, hour_start=hour_start)
    # previous hour (counts nothing: expected_so_far=2, observed=0 → floor)
    _seed_order(db, created_at=hour_start - timedelta(minutes=5))
    # same hour, different zone cell (36.81/10.17)
    _seed_order(db, created_at=hour_start + timedelta(minutes=15), lat=36.8065, lon=10.1815)
    assert demand_service.derive_demand_ratio(db, *ZONE, now=now) == demand_service.RATIO_FLOOR


def test_cancelled_orders_do_not_count(db):
    now = datetime(2026, 10, 26, 12, 30, tzinfo=UTC)
    hour_start = now.replace(minute=0)
    _seed_forecast(db, predicted=4.0, hour_start=hour_start)
    db.add(
        Order(
            tenant_id=1,
            customer_id=1,
            restaurant_id=1,
            status=OrderStatus.cancelled,
            pickup_latitude=36.8,
            pickup_longitude=10.18,
            dropoff_latitude=36.8381,
            dropoff_longitude=10.2385,
            distance_km=3.0,
            delivery_fee=5.0,
            eta_minutes=20,
            created_at=hour_start + timedelta(minutes=10),
        )
    )
    db.commit()
    assert demand_service.derive_demand_ratio(db, *ZONE, now=now) == demand_service.RATIO_FLOOR

"""Make any database demo-ready in one command (#170, decision 0004 phase A).

Idempotent: every step is create-if-missing or a window refresh, so the
same command prepares a fresh VPS database, tops up a local one, and is
the body of the nightly reset. Layers ON TOP of scripts/seed_demo_data.py
(restaurants/drivers/customers from the city manifest) the four things
that seeder deliberately doesn't own:

  1. the demo sign-in trio (admin/admin · ops/ops · view/view)
  2. the storefront coupon (STOREDEMO10, 10%)
  3. one dispatchable driver parked near the first restaurant
  4. 24h of zone-hour demand forecasts (a demo profile has no Airflow —
     without these the live surge derivation stays neutral forever)

Usage:
    python scripts/seed_demo.py                       # against DATABASE_URL
    DATABASE_URL=postgresql+psycopg://... python scripts/seed_demo.py
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import engine  # noqa: E402
from core.security import hash_password  # noqa: E402
from domain.city_config import load_city_config  # noqa: E402
from domain.models import (  # noqa: E402
    Coupon,
    Customer,
    DemandForecast,
    Driver,
    Restaurant,
    User,
)
from domain.services import tenant_service  # noqa: E402
from domain.services.demand_service import ZONE_DECIMALS  # noqa: E402

COUPON_CODE = "STOREDEMO10"
FORECAST_HOURS = 24


def _ensure_users(session) -> int:
    created = 0
    for username, password, role in (
        ("admin", "admin", "admin"),
        ("ops", "ops", "ops"),
        ("view", "view", "viewer"),
    ):
        if session.query(User).filter_by(username=username).one_or_none() is None:
            session.add(User(username=username, password_hash=hash_password(password), role=role))
            created += 1
    session.commit()
    return created


def _ensure_coupon(session, tenant_id: int) -> bool:
    exists = (
        session.query(Coupon)
        .filter(Coupon.tenant_id == tenant_id, Coupon.code == COUPON_CODE)
        .one_or_none()
    )
    if exists is not None:
        return False
    session.add(
        Coupon(
            tenant_id=tenant_id,
            code=COUPON_CODE,
            discount_kind="percent",
            discount_value=10,
        )
    )
    session.commit()
    return True


def _ensure_dispatchable_driver(session) -> int:
    """The first restaurant needs ONE available driver outside its door —
    the backfill leaves drivers offline, and an honest dispatcher correctly
    reports nobody available (a demo killer §41 documents)."""
    restaurant = session.query(Restaurant).order_by(Restaurant.id).first()
    if restaurant is None:
        return 0
    driver = session.query(Driver).order_by(Driver.id).first()
    if driver is None:
        driver = Driver(
            tenant_id=restaurant.tenant_id,
            name="Karim",
            vehicle="motorcycle",
            latitude=restaurant.latitude + 0.005,
            longitude=restaurant.longitude + 0.005,
        )
        session.add(driver)
    driver.is_available = True
    driver.latitude = restaurant.latitude + 0.005
    driver.longitude = restaurant.longitude + 0.005
    session.commit()
    return 1


def _seed_forecasts(session) -> tuple[int, int]:
    """24h of zone-hour rows for every cell the demo's customers and
    restaurants live in — a realistic day curve (lunch + dinner peaks,
    Africa/Tunis = fixed UTC+1) stored in UTC like the serving loop does.
    The window refreshes on every run: delete the zones' rows from the
    current hour on, insert fresh."""
    coords = [(c.latitude, c.longitude) for c in session.query(Customer).all()] + [
        (r.latitude, r.longitude) for r in session.query(Restaurant).all()
    ]
    zones = sorted({(round(lat, ZONE_DECIMALS), round(lon, ZONE_DECIMALS)) for lat, lon in coords})
    if not zones:
        return 0, 0

    now_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    session.query(DemandForecast).filter(
        DemandForecast.forecast_hour_utc >= now_hour,
        DemandForecast.zone_lat.in_([z[0] for z in zones]),
        DemandForecast.zone_lon.in_([z[1] for z in zones]),
    ).delete(synchronize_session=False)

    rows: list[DemandForecast] = []
    for offset in range(FORECAST_HOURS):
        hour_utc = now_hour + timedelta(hours=offset)
        local_hour = (hour_utc.hour + 1) % 24  # Africa/Tunis = fixed UTC+1
        predicted = round(
            2.0
            + 2.5 * pow(2.718, -((local_hour - 13) ** 2) / 6.0)
            + 3.5 * pow(2.718, -((local_hour - 20) ** 2) / 8.0),
            1,
        )
        rows.extend(
            DemandForecast(
                zone_lat=lat,
                zone_lon=lon,
                forecast_hour_utc=hour_utc,
                predicted_orders=predicted,
            )
            for lat, lon in zones
        )
    session.add_all(rows)
    session.commit()
    return len(rows), len(zones)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--region", default=get_settings().city, help="city manifest (default: the CITY setting)"
    )
    args = parser.parse_args()

    config = load_city_config(args.region)
    session = sessionmaker(bind=engine)()
    try:
        # base fixtures from the city manifest (skips itself if seeded)
        if session.query(Customer).count() == 0:
            from scripts.seed_demo_data import seed as seed_city

            seed_city(config)

        tenant_id = tenant_service.default_id(session)
        users = _ensure_users(session)
        coupon = _ensure_coupon(session, tenant_id)
        driver = _ensure_dispatchable_driver(session)
        forecasts, zones = _seed_forecasts(session)

        print(
            f"demo seed: users +{users} · coupon {'created' if coupon else 'present'} · "
            f"driver {'ready' if driver else 'n/a'} · {forecasts} forecast rows "
            f"across {zones} zones"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()

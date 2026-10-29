"""Seed the local database with realistic demo data for the configured city.

Coordinates come from the city manifest (config/cities/<region>.json,
#78 region pack) - one deployment = one city = one business timezone.

Usage (repo root):
    python scripts/seed_demo_data.py                 # seeds if empty (city from CITY env, default tunis)
    python scripts/seed_demo_data.py --region paris  # explicit city manifest
    python scripts/seed_demo_data.py --reset         # drops and reseeds
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import engine  # noqa: E402
from domain.city_config import CityConfig, load_city_config  # noqa: E402
from domain.models import Base, Customer, Driver, Restaurant  # noqa: E402


def reset() -> None:
    Base.metadata.drop_all(engine)


def seed(config: CityConfig) -> None:
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        if session.query(Customer).count() > 0:
            print("Database already seeded — use --reset to reseed.")
            return
        session.add_all(
            [
                Restaurant(name=r.name, latitude=r.latitude, longitude=r.longitude)
                for r in config.restaurants
            ]
            + [
                Driver(name=d.name, vehicle=d.vehicle, latitude=d.latitude, longitude=d.longitude)
                for d in config.drivers
            ]
            + [
                Customer(name=c.name, phone=c.phone, latitude=c.latitude, longitude=c.longitude)
                for c in config.customers
            ]
        )
        session.commit()
        first_customer = config.customers[0]
        print(
            f"Seeded {len(config.restaurants)} restaurants, {len(config.drivers)} drivers, "
            f"{len(config.customers)} customers ({config.label} metro area, "
            f"tz {config.business_timezone})."
        )
        print(
            "Try: curl -X POST localhost:8010/pricing/quote -H 'Content-Type: application/json'"
            f' -d \'{{"restaurant_id": 1, "dropoff_latitude": {first_customer.latitude},'
            f' "dropoff_longitude": {first_customer.longitude}}}\''
        )
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=get_settings().city)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if args.reset:
        reset()
    seed(load_city_config(args.region))

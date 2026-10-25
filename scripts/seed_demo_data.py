"""Seed the local database with realistic demo data around Tunis.

Usage (repo root):
    python scripts/seed_demo_data.py          # seeds if empty
    python scripts/seed_demo_data.py --reset  # drops and reseeds
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.database import engine  # noqa: E402
from domain.models import Base, Customer, Driver, Restaurant  # noqa: E402

RESTAURANTS = [
    ("Chez Slah — Tunis Centre", 36.8065, 10.1815),
    ("Le Bambino — La Marsa", 36.8786, 10.3247),
    ("Dar El Jeld — Medina", 36.7988, 10.1718),
    ("Pizza Roma — Lac 2", 36.8381, 10.2385),
    ("Chez Ali — Ariana", 36.8625, 10.1956),
    ("Sidi Bou Grill — Sidi Bou Said", 36.8713, 10.3429),
]

DRIVERS = [
    ("Karim", "motorcycle", 36.8100, 10.1900),
    ("Mongi", "motorcycle", 36.8400, 10.2300),
    ("Sami", "car", 36.8700, 10.3300),
    ("Hédi", "bicycle", 36.8050, 10.1750),
    ("Foued", "motorcycle", 36.8600, 10.2000),
    ("Amine", "car", 36.8350, 10.2600),
    ("Walid", "motorcycle", 36.7990, 10.1800),
    ("Nizar", "bicycle", 36.8720, 10.3410),
]

CUSTOMERS = [
    ("Aziza", "+216 20 111 111", 36.8381, 10.2385),
    ("Mehdi", "+216 20 222 222", 36.8786, 10.3247),
    ("Rym", "+216 20 333 333", 36.8100, 10.1700),
    ("Youssef", "+216 20 444 444", 36.8625, 10.1956),
    ("Ines", "+216 20 555 555", 36.8713, 10.3429),
    ("Bilel", "+216 20 666 666", 36.7988, 10.1718),
]


def reset() -> None:
    Base.metadata.drop_all(engine)


def seed() -> None:
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        if session.query(Customer).count() > 0:
            print("Database already seeded — use --reset to reseed.")
            return
        session.add_all(
            [Restaurant(name=n, latitude=lat, longitude=lon) for n, lat, lon in RESTAURANTS]
            + [Driver(name=n, vehicle=v, latitude=lat, longitude=lon) for n, v, lat, lon in DRIVERS]
            + [
                Customer(name=n, phone=p, latitude=lat, longitude=lon)
                for n, p, lat, lon in CUSTOMERS
            ]
        )
        session.commit()
        print(
            f"Seeded {len(RESTAURANTS)} restaurants, {len(DRIVERS)} drivers, "
            f"{len(CUSTOMERS)} customers (Tunis metro area)."
        )
        print(
            "Try: curl -X POST localhost:8000/pricing/quote -H 'Content-Type: application/json'"
            ' -d \'{"restaurant_id": 1, "dropoff_latitude": 36.8381, "dropoff_longitude": 10.2385}\''
        )
    finally:
        session.close()


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset()
    seed()

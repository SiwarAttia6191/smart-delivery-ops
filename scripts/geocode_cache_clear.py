"""Clear the geocoder's address cache (#232) — the city-update path.

Addresses don't move, so the cache never expires on its own: any address
is geocoded exactly once ever. But city DATA does change (OSM corrections,
a municipality renames a street, a bad first resolution). When that
happens, clear the cache and the next lookup re-resolves against the live
geocoder and re-caches.

Usage (repo root):
    python scripts/geocode_cache_clear.py            # show cache size only
    python scripts/geocode_cache_clear.py --yes      # actually clear it

Inside the stack (the API container's database):
    docker compose exec api python scripts/geocode_cache_clear.py --yes
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.database import engine  # noqa: E402
from domain.models import GeocodeCache  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually clear (without this flag the script only reports)",
    )
    args = parser.parse_args()

    session = sessionmaker(bind=engine)()
    try:
        size = session.scalar(select(func.count()).select_from(GeocodeCache))
        print(f"geocode_cache rows: {size}")
        if not args.yes:
            print("(report only — pass --yes to clear)")
            return
        session.execute(delete(GeocodeCache))
        session.commit()
        print(f"cleared {size} rows — the next address lookup re-resolves and re-caches")
    finally:
        session.close()


if __name__ == "__main__":
    main()

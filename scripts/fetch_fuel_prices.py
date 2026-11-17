"""Fetch and cache station-level fuel prices for a deployment (#274 V2).

    python scripts/fetch_fuel_prices.py --city paris
    python scripts/fetch_fuel_prices.py --city paris --source-file /tmp/prix.xml

The flow: the city manifest declares the country code → the adapter
registry picks the country's open-data parser → the source is fetched
(or read from --source-file for offline replay) → normalized into
StationPrice rows → the country's cached rows are REPLACED (the
vault-bridge pattern: refresh-on-fetch, no duplicates). The near-driver
query then reads the cache; the manifest constant stays the fallback
wherever the cache holds no stations.

Scheduling: daily (prices are daily). The Airflow wiring is a single
BashOperator mirroring the fuel-free quote contract; a manual run is
the demo path.
"""

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import urllib.request  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from domain.city_config import load_city_config  # noqa: E402
from domain.models import FuelPriceStation  # noqa: E402
from domain.services import fuel_adapters  # noqa: E402


def fetch_source(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "smart-delivery-ops/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default=get_settings().city)
    parser.add_argument(
        "--source-file", type=Path, default=None, help="offline replay of the source payload"
    )
    args = parser.parse_args()

    config = load_city_config(args.city)
    fuel = config.fuel
    if fuel is None or not fuel.country_code:
        raise SystemExit(f"city '{args.city}' declares no fuel country_code — nothing to fetch")
    adapter = fuel_adapters.adapter_for(fuel.country_code)
    if adapter is None:
        raise SystemExit(
            f"no station-price adapter for country '{fuel.country_code}' — "
            "the manifest constant remains the quote's source (V1 behavior)"
        )

    if args.source_file:
        payload = args.source_file.read_bytes()
        print(f"replaying source file: {args.source_file} ({len(payload)} bytes)")
    else:
        source_url = f"https://donnees.securite-routiere.gouv.fr/fr/points-de-reactualisation-des-prix/{fuel.country_code}"
        print(f"fetching {source_url} ...")
        payload = fetch_source(source_url)

    prices = adapter(payload, source=fuel.source)
    if not prices:
        raise SystemExit("the source parsed to zero prices — refusing to wipe the cache")

    session = SessionLocal()
    try:
        # replace-on-refresh per country (the vault-bridge pattern)
        session.query(FuelPriceStation).filter(
            FuelPriceStation.country == fuel.country_code
        ).delete()
        for price in prices:
            session.add(
                FuelPriceStation(
                    country=fuel.country_code,
                    station_id=price.station_id,
                    name=price.name,
                    latitude=price.latitude,
                    longitude=price.longitude,
                    fuel_kind=price.fuel_kind,
                    price=price.price,
                    currency=price.currency,
                    updated_at=price.updated_at,
                    source=price.source,
                )
            )
        session.commit()
        print(
            f"cached {len(prices)} station prices for '{fuel.country_code}' (currency {fuel.currency})"
        )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Fuel economics tests: the self-deliverer's margin view.

The guarantees: the quote carries the fuel estimate grounded in the
manifest's OFFICIAL city price (liters from the dominant vehicle's
consumption, cost = liters × price, CO₂ = liters × the combustion
factor); a bicycle delivery's fuel cost is honestly ZERO; and the sales
snapshot sums the ACTUAL burn over today's delivered orders using each
assigned driver's vehicle.
"""

from datetime import UTC, datetime

import pytest

from domain.city_config import load_city_config
from domain.services import fuel_service

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT_LAT, RESTAURANT_LON = 36.8065, 10.1815
RESTAURANT = {"name": "Chez Slah", "latitude": RESTAURANT_LAT, "longitude": RESTAURANT_LON}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


class TestFuelEstimate:
    def test_quote_carries_the_fuel_estimate(self, authed_client):
        authed_client.post("/customers", json=CUSTOMER)
        restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
        quote = authed_client.post(
            "/pricing/quote",
            json={
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
                "demand_ratio": 0,
            },
        ).json()

        fuel = quote["fuel_estimate"]
        assert fuel is not None  # the tunis manifest declares fuel economics
        assert fuel["basis"] == "estimate"
        assert fuel["vehicle"] == "motorcycle"
        expected_cost = round(quote["distance_km"] / 100 * 3.5 * 2.5, 2)
        assert fuel["cost"] == expected_cost  # liters × the official price
        assert fuel["currency"] == "TND"
        assert fuel["co2_kg"] == round(fuel["liters"] * 2.31, 3)

    def test_estimate_is_absent_when_the_manifest_declares_no_fuel(self, monkeypatch):
        from domain.city_config import load_city_config

        config = load_city_config("tunis")
        monkeypatch.setattr(config, "fuel", None)
        assert fuel_service.estimate(config, 10.0) is None


class TestFuelActuals:
    def test_bicycle_burns_zero_honestly(self):
        config = load_city_config("tunis")
        fuel = fuel_service.actual(config, 10.0, "bicycle")
        assert fuel is not None  # the tunis manifest declares fuel economics
        assert fuel["liters"] == 0.0
        assert fuel["cost"] == 0.0
        assert fuel["co2_kg"] == 0.0
        assert fuel["vehicle"] == "bicycle"

    def test_motorcycle_actual(self):
        config = load_city_config("tunis")
        fuel = fuel_service.actual(config, 10.0, "motorcycle")
        assert fuel is not None
        assert fuel["liters"] == 0.35
        assert fuel["cost"] == round(0.35 * 2.5, 2)


class TestSnapshotFuel:
    def test_snapshot_sums_the_actual_burn(self, authed_client):
        customer = authed_client.post("/customers", json=CUSTOMER).json()
        restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
        authed_client.post("/drivers", json=DRIVER)
        for _ in range(2):
            order = authed_client.post(
                "/orders",
                json={
                    "customer_id": customer["id"],
                    "restaurant_id": restaurant["id"],
                    "dropoff_latitude": CUSTOMER["latitude"],
                    "dropoff_longitude": CUSTOMER["longitude"],
                    "demand_ratio": 0,
                },
            ).json()
            authed_client.post(f"/orders/{order['id']}/assign")
            authed_client.post(f"/orders/{order['id']}/status", json={"status": "in_transit"})
            authed_client.post(f"/orders/{order['id']}/status", json={"status": "delivered"})

        snap = authed_client.get(f"/restaurants/{restaurant['id']}/sales-snapshot").json()
        assert snap["fuel"] is not None
        assert snap["fuel"]["currency"] == "TND"
        # two motorcycle deliveries: the actual burn, not a guess
        assert snap["fuel"]["cost"] > 0
        expected = round(
            sum(
                round(o["distance_km"] / 100 * 3.5, 3) * 2.5
                for o in authed_client.get("/orders").json()
                if o["restaurant_id"] == restaurant["id"]
            ),
            2,
        )
        assert snap["fuel"]["cost"] == pytest.approx(expected, abs=0.05)


class TestNearestStationPrice:
    """The V2 near-driver query (#274): the quote's estimate upgrades to
    the NEAREST CACHED STATION's price when the cache holds stations for
    the deployment's country — the manifest constant remains the
    fallback. No external calls: the cache is populated by the fetch
    script (tests seed it directly)."""

    def test_quote_uses_the_nearest_cached_station(self, authed_client, db):
        from domain.models import FuelPriceStation

        authed_client.post("/customers", json=CUSTOMER)
        restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
        # the restaurant's coordinates get two cached stations: one nearer
        # with a HIGHER price (nearest wins, the price is the station's truth)
        db.add(
            FuelPriceStation(
                country="tn",
                station_id="ST-1",
                name="Station near",
                latitude=RESTAURANT_LAT + 0.01,
                longitude=RESTAURANT_LON + 0.01,
                fuel_kind="diesel",
                price=2.65,
                currency="TND",
                updated_at=datetime.now(UTC),
                source="test",
            )
        )
        db.add(
            FuelPriceStation(
                country="tn",
                station_id="ST-2",
                name="Station far",
                latitude=RESTAURANT_LAT + 0.2,
                longitude=RESTAURANT_LON + 0.2,
                fuel_kind="diesel",
                price=2.2,
                currency="TND",
                updated_at=datetime.now(UTC),
                source="test",
            )
        )
        db.commit()

        quote = authed_client.post(
            "/pricing/quote",
            json={
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
                "demand_ratio": 0,
            },
        ).json()
        fuel = quote["fuel_estimate"]
        assert fuel["price_source"].startswith("station")
        assert fuel["station"] == "Station near"  # nearest wins
        assert fuel["price"] == 2.65

    def test_quote_falls_back_to_the_manifest_without_cached_stations(self, authed_client, db):
        restaurant = authed_client.post(
            "/restaurants", json={"name": "No Cache Kitchen", "latitude": 40.0, "longitude": 2.0}
        ).json()
        quote = authed_client.post(
            "/pricing/quote",
            json={
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": 40.4,
                "dropoff_longitude": 2.1,
                "demand_ratio": 0,
            },
        ).json()
        fuel = quote["fuel_estimate"]
        assert fuel is not None
        assert "price_source" not in fuel  # the manifest constant served

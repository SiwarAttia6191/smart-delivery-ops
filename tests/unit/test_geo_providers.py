"""Unit tests for the distance-provider layer (domain/rules/geo.py).

Fully hermetic: OSRM is exercised through httpx.MockTransport, so no broker,
no container, no network. The live OSRM path is verified by
scripts/benchmark_geo.py against the real container.
"""

import json

import httpx
import pytest

from core.config import Settings
from domain.rules import geo


class FixedProvider:
    """Test double: always returns the same distance."""

    name = "fixed"

    def __init__(self, km: float) -> None:
        self.km = km

    def distance_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return self.km


def osrm_payload(distance_m: float) -> bytes:
    return json.dumps({"code": "Ok", "routes": [{"distance": distance_m}]}).encode()


def mock_osrm_client(distance_m: float) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=osrm_payload(distance_m))
        )
    )


def test_haversine_provider_matches_pure_function():
    provider = geo.HaversineProvider()
    assert provider.distance_km(36.8065, 10.1815, 36.8381, 10.2385) == geo.haversine_km(
        36.8065, 10.1815, 36.8381, 10.2385
    )


def test_circuity_provider_multiplies_inner():
    provider = geo.CircuityAdjustedProvider(FixedProvider(10.0), factor=1.3)
    assert provider.distance_km(0, 0, 1, 1) == 13.0
    assert provider.name == "circuity(fixed, x1.3)"


def test_osrm_provider_parses_metres_to_km():
    provider = geo.OsrmProvider("http://osrm.test", client=mock_osrm_client(6171.6))
    assert provider.distance_km(36.8065, 10.1815, 36.8381, 10.2385) == 6.1716


def test_osrm_provider_raises_on_empty_route():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'{"code": "Ok", "routes": []}')
        )
    )
    provider = geo.OsrmProvider("http://osrm.test", client=client)
    with pytest.raises(ValueError, match="no route"):
        provider.distance_km(0, 0, 1, 1)


def test_build_provider_registry():
    settings = Settings(distance_provider="circuity", database_url="sqlite://")
    assert isinstance(geo.build_provider("haversine", settings), geo.HaversineProvider)
    circuity = geo.build_provider("circuity", settings)
    assert isinstance(circuity, geo.CircuityAdjustedProvider)
    assert circuity.factor == 1.3
    osrm = geo.build_provider("osrm", settings)
    assert isinstance(osrm, geo.OsrmProvider)
    with pytest.raises(ValueError):
        geo.build_provider("teleport", settings)


def test_resolve_distance_uses_champion_and_ignores_shadow_value(caplog):
    settings = Settings(database_url="sqlite://", distance_shadow_provider="circuity")
    champion = FixedProvider(10.0)
    with caplog.at_level("INFO", logger="geo.shadow"):
        distance = geo.resolve_distance(0, 0, 1, 1, provider=champion, settings=settings)
    # The shadow value is logged but NEVER returned - business decisions
    # keep using the champion.
    assert distance == 10.0
    assert "distance shadow" in caplog.text
    assert "circuity" in caplog.text


def test_resolve_distance_survives_shadow_failure():
    class BrokenProvider:
        name = "broken"

        def distance_km(self, lat1, lon1, lat2, lon2):
            raise RuntimeError("osrm down")

    settings = Settings(database_url="sqlite://", distance_shadow_provider="circuity")
    # champion fixed; shadow built from settings is circuity - force a shadow
    # failure by making the shadow provider construction succeed but the call
    # fail: use a settings string that resolves to a broken provider via monkey
    # construction is not possible, so instead patch get_shadow_provider.
    original = geo.get_shadow_provider
    geo.get_shadow_provider = lambda settings=None: BrokenProvider()
    try:
        assert (
            geo.resolve_distance(0, 0, 1, 1, provider=FixedProvider(10.0), settings=settings)
            == 10.0
        )
    finally:
        geo.get_shadow_provider = original


def test_no_shadow_configured_is_silent(caplog):
    settings = Settings(database_url="sqlite://")
    with caplog.at_level("INFO", logger="geo.shadow"):
        distance = geo.resolve_distance(0, 0, 1, 1, provider=FixedProvider(5.0), settings=settings)
    assert distance == 5.0
    assert "distance shadow" not in caplog.text


class TestServiceInjection:
    def test_pricing_uses_injected_provider(self):
        from domain.services import pricing_service

        quote = pricing_service.quote_delivery(
            36.8065, 10.1815, 36.8381, 10.2385, provider=FixedProvider(10.0)
        )
        assert quote.distance_km == 10.0
        assert quote.breakdown.distance_fee == 9.0  # 0.9 * 10

    def test_matching_uses_injected_provider(self, db):
        from domain.models import Driver
        from domain.services import driver_matching_service

        db.add(Driver(tenant_id=1, name="D", latitude=36.9, longitude=10.3, is_available=True))
        db.commit()

        match = driver_matching_service.find_nearest_driver(
            db, 36.8065, 10.1815, provider=FixedProvider(7.5)
        )
        assert match is not None
        assert match.distance_km == 7.5  # the provider's answer, not haversine's

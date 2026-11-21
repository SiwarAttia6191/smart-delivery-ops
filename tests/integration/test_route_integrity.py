"""The route-capture integrity gate (#311 companion, route-divergence
investigation): a corrupt router's million-meter capture must never
poison route_cache. The gate rejects captures whose distance exceeds
30x the pair's straight-line distance — the measured legitimate max
(Madrid Centro one-way loops) is 11.8x."""

import logging

import pytest

from domain.models import RouteCache
from domain.services import route_service

PICKUP = (40.4256, -3.7043)  # Casa Toni, Malasaña
DROPOFF = (40.4221, -3.7008)  # Fuencarral 24, Chueca — 0.48 km straight-line


def _geometry(distance_m: float, points: int = 30) -> dict:
    """A plausible GeoJSON capture with an IMPLAUSIBLE distance."""
    return {
        "coordinates": [[-3.7043 + i / 1000, 40.4256 + i / 1000] for i in range(points)],
        "distance_m": distance_m,
        "duration_s": distance_m / 10.0,
    }


def test_sane_route_is_cached(db, monkeypatch):
    straight = 480.0  # meters, the pair's real straight-line
    monkeypatch.setattr(
        route_service, "_fetch_geometry", lambda base_url, key: _geometry(straight * 1.2)
    )
    route_service.capture_for_order(db, *PICKUP, *DROPOFF)
    rows = db.query(RouteCache).all()
    assert len(rows) == 1
    assert rows[0].distance_m == pytest.approx(straight * 1.2)


def test_corrupt_route_is_rejected_not_cached(db, monkeypatch, caplog):
    # the live signature: 4,199,948 m for a 0.48 km pair (~8.7 MILLION x)
    monkeypatch.setattr(
        route_service, "_fetch_geometry", lambda base_url, key: _geometry(4_199_948.0)
    )
    with caplog.at_level(logging.WARNING):
        route_service.capture_for_order(db, *PICKUP, *DROPOFF)
    assert db.query(RouteCache).count() == 0  # the poison never persists
    assert any("route capture REJECTED" in r.message for r in caplog.records)


def test_legitimate_one_way_loop_survives_the_gate(db, monkeypatch):
    # the measured legitimate max: Madrid Centro one-ways at 11.8x —
    # INSIDE the 30x gate, so real loops are never rejected
    straight = 480.0
    monkeypatch.setattr(
        route_service, "_fetch_geometry", lambda base_url, key: _geometry(straight * 11.8)
    )
    route_service.capture_for_order(db, *PICKUP, *DROPOFF)
    assert db.query(RouteCache).count() == 1

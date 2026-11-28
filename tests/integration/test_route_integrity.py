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
    """A plausible capture: the line STARTS at the pickup and ENDS at the
    dropoff (the honest endpoint shape — the endpoint gate #371 demands
    it, exactly as OSRM's snapped output does)."""
    coords = []
    for i in range(points):
        t = i / (points - 1)
        coords.append(
            [
                PICKUP[1] + (DROPOFF[1] - PICKUP[1]) * t,
                PICKUP[0] + (DROPOFF[0] - PICKUP[0]) * t,
            ]
        )
    return {
        "coordinates": coords,
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


# ── the endpoint-integrity gate (#371): the line must connect the points ──


def test_capture_stores_the_endpoint_gaps(db, monkeypatch):
    # the road's reach, measured at capture (#371): OSRM snapped the points
    # tens of meters away - the LIVE measured shape from order 64b675e7
    # (13 m at the pickup, 48 m at the customer's dragged pin)
    def snapped_geometry(base_url, key):
        p_lat, p_lon, d_lat, d_lon = key
        return {
            "coordinates": [
                [p_lon + 0.0001, p_lat - 0.0001],  # ~14 m from the pickup pin
                [d_lon + 0.0003, d_lat + 0.0003],  # ~41 m from the dropoff pin
            ],
            "distance_m": 480.0,
            "duration_s": 48.0,
        }

    monkeypatch.setattr(route_service, "_fetch_geometry", snapped_geometry)
    route_service.capture_for_order(db, *PICKUP, *DROPOFF)
    row = db.query(RouteCache).one()
    assert row.pickup_snap_m == pytest.approx(14.0, abs=6.0)
    assert row.dropoff_snap_m == pytest.approx(41.0, abs=8.0)


def test_endpoints_far_from_the_points_are_rejected(db, monkeypatch, caplog):
    # the mixed-generation-graph signature: a line that does not START near
    # its pickup cannot be trusted, whatever its distance says
    import logging

    def runaway_geometry(base_url, key):
        p_lat, p_lon, d_lat, d_lon = key
        return {
            "coordinates": [
                [p_lon + 0.01, p_lat + 0.01],  # ~1.4 km from the requested pickup
                [d_lon, d_lat],
            ],
            "distance_m": 480.0,
            "duration_s": 48.0,
        }

    monkeypatch.setattr(route_service, "_fetch_geometry", runaway_geometry)
    with caplog.at_level(logging.WARNING):
        route_service.capture_for_order(db, *PICKUP, *DROPOFF)
    assert db.query(RouteCache).count() == 0  # the poison never persists
    assert any("endpoints land" in r.message for r in caplog.records)


def test_geojson_exposes_the_gaps(db, monkeypatch):
    def snapped_geometry(base_url, key):
        p_lat, p_lon, d_lat, d_lon = key
        return {
            "coordinates": [[p_lon, p_lat], [d_lon, d_lat]],
            "distance_m": 480.0,
            "duration_s": 48.0,
        }

    monkeypatch.setattr(route_service, "_fetch_geometry", snapped_geometry)
    route_service.capture_for_order(db, *PICKUP, *DROPOFF)
    geojson = route_service.geojson_for_order(db, *PICKUP, *DROPOFF)
    assert geojson is not None
    props = geojson["features"][0]["properties"]
    assert props["pickup_snap_m"] == pytest.approx(0.0, abs=1.0)
    assert props["dropoff_snap_m"] == pytest.approx(0.0, abs=1.0)

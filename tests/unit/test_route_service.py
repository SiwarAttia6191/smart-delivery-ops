"""route_service — per-pair capture, dedupe, and GeoJSON serving (#58)."""

from domain.models import RouteCache
from domain.services import route_service

PAIR = (36.806522, 10.181621, 36.838100, 10.238500)
GEOMETRY = {
    "coordinates": [[10.181621, 36.806522], [10.21, 36.82], [10.2385, 36.8381]],
    "distance_m": 7645.2,
    "duration_s": 510.6,
}


def test_capture_dedupes_by_rounded_pair(db, monkeypatch):
    calls = []

    def fake_fetch(base_url, key):
        calls.append(key)
        return GEOMETRY

    monkeypatch.setattr(route_service, "_fetch_geometry", fake_fetch)

    route_service.capture_for_order(db, *PAIR)
    route_service.capture_for_order(db, *PAIR)  # identical pair: no second fetch
    # near-identical pair within ~1 m rounds to the same key too
    route_service.capture_for_order(db, PAIR[0] + 1e-6, PAIR[1], PAIR[2], PAIR[3])

    assert len(calls) == 1
    rows = db.query(RouteCache).all()
    assert len(rows) == 1
    assert rows[0].pickup_lat == round(PAIR[0], 5)


def test_capture_survives_osrm_outage(db, monkeypatch):
    def failing_fetch(base_url, key):
        raise ConnectionError("osrm down")

    monkeypatch.setattr(route_service, "_fetch_geometry", failing_fetch)

    route_service.capture_for_order(db, *PAIR)  # must not raise
    assert db.query(RouteCache).count() == 0


def test_geojson_for_order(db, monkeypatch):
    monkeypatch.setattr(route_service, "_fetch_geometry", lambda base_url, key: GEOMETRY)
    route_service.capture_for_order(db, *PAIR)
    geojson = route_service.geojson_for_order(db, *PAIR)
    assert geojson is not None
    assert geojson["type"] == "FeatureCollection"
    feature = geojson["features"][0]
    assert feature["geometry"]["type"] == "LineString"
    assert feature["geometry"]["coordinates"] == GEOMETRY["coordinates"]
    assert feature["properties"] == {"distance_m": 7645.2, "duration_s": 510.6}


def test_geojson_missing_pair_returns_none(db):
    assert route_service.geojson_for_order(db, 1.0, 1.0, 2.0, 2.0) is None

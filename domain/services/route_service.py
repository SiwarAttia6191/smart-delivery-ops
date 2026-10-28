"""Route geometry for real orders (#58).

Fetches the OSRM road geometry for an order's pickup→dropoff pair once,
caches it per pair, and serves it as GeoJSON. Best-effort by contract: the
route is a feature for later (tracking UI, delivery proof, benchmark pairs)
— an OSRM outage must never block an order. Everything degrades to None and
the caller moves on.

Dedupe: pairs are rounded to ~1 m; restaurants sit still and customers
cluster, so the same pair recurs constantly and one fetch serves them all.
"""

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from core.config import get_settings
from domain.models import RouteCache
from domain.models.route_cache import COORD_DECIMALS

logger = logging.getLogger(__name__)


def _rounded(pickup_lat: float, pickup_lon: float, dropoff_lat: float, dropoff_lon: float):
    return (
        round(pickup_lat, COORD_DECIMALS),
        round(pickup_lon, COORD_DECIMALS),
        round(dropoff_lat, COORD_DECIMALS),
        round(dropoff_lon, COORD_DECIMALS),
    )


def _fetch_geometry(base_url: str, key: tuple) -> dict[str, Any] | None:
    """OSRM /route with full GeoJSON geometry. None on any failure."""
    p_lat, p_lon, d_lat, d_lon = key
    url = f"{base_url.rstrip('/')}/route/v1/driving/{p_lon},{p_lat};{d_lon},{d_lat}"
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(url, params={"overview": "full", "geometries": "geojson"})
            response.raise_for_status()
        route = response.json()["routes"][0]
        return {
            "coordinates": route["geometry"]["coordinates"],
            "distance_m": route["distance"],
            "duration_s": route["duration"],
        }
    except Exception as exc:  # noqa: BLE001 - route capture is best-effort
        logger.warning("route geometry unavailable (%s); order proceeds without it", exc)
        return None


def capture_for_order(
    session: Session,
    pickup_lat: float,
    pickup_lon: float,
    dropoff_lat: float,
    dropoff_lon: float,
) -> None:
    """Cache the pair's geometry if not already cached. Never raises; called
    inside create_order AFTER the order commit so an OSRM stall cannot hold
    the order transaction open."""
    settings = get_settings()
    key = _rounded(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon)
    try:
        if session.get(RouteCache, key) is not None:
            return
        geometry = _fetch_geometry(settings.osrm_base_url, key)
        if geometry is None:
            return
        session.add(
            RouteCache(
                pickup_lat=key[0],
                pickup_lon=key[1],
                dropoff_lat=key[2],
                dropoff_lon=key[3],
                **geometry,
            )
        )
        session.commit()
        logger.info("route geometry cached for pair %s", key)
    except Exception as exc:  # noqa: BLE001 - cache write must never break orders
        logger.warning("route cache write failed (%s); order proceeds without it", exc)


def geojson_for_order(
    session: Session, pickup_lat: float, pickup_lon: float, dropoff_lat: float, dropoff_lon: float
) -> dict | None:
    """GeoJSON Feature for the order's pair, or None if never captured."""
    key = _rounded(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon)
    cache = session.get(RouteCache, key)
    if cache is None:
        return None
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": cache.coordinates},
                "properties": {
                    "distance_m": cache.distance_m,
                    "duration_s": cache.duration_s,
                },
            }
        ],
    }

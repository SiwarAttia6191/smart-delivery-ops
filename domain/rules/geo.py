"""Geographic helpers: pure math plus pluggable distance providers.

The distance method is an interface, not a decision (Phase 3, issue #24).
The champion is the pure haversine - instant, offline, but ~25-35% below
real road distance. Challengers (circuity-adjusted, OSRM road routing) are
benchmarked against ground truth by scripts/benchmark_geo.py and migrated
only with shadow-mode evidence - never by faith.
"""

import logging
from math import asin, cos, radians, sin, sqrt
from typing import Protocol

import httpx

from core.config import Settings, get_settings

EARTH_RADIUS_KM = 6371.0088

logger = logging.getLogger("geo.shadow")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two (lat, lon) points, in kilometres."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * asin(sqrt(a)), 4)


class DistanceProvider(Protocol):
    """Road-aware (or naive) distance between two WGS84 points, in km."""

    name: str

    def distance_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float: ...


class HaversineProvider:
    """The incumbent: pure great-circle math. Offline, exact as a formula,
    systematically short vs roads."""

    name = "haversine"

    def distance_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return haversine_km(lat1, lon1, lat2, lon2)


class CircuityAdjustedProvider:
    """Haversine × an urban circuity factor: one line, kills most of the
    systematic bias, still offline. Wraps any inner provider."""

    def __init__(self, inner: DistanceProvider, factor: float = 1.3) -> None:
        self._inner = inner
        self.factor = factor
        self.name = f"circuity({inner.name}, x{factor})"

    def distance_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return round(self._inner.distance_km(lat1, lon1, lat2, lon2) * self.factor, 4)


class OsrmProvider:
    """Road-network distance via a self-hosted OSRM container (no API key,
    no cost). Fails loudly - callers decide their own fallback policy."""

    name = "osrm"

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=5.0)

    def distance_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        url = f"{self._base_url}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
        response = self._client.get(url, params={"overview": "false"})
        response.raise_for_status()
        return parse_route_distance(response.json())


def parse_route_distance(payload: dict) -> float:
    """OSRM /route response -> kilometres (routes[0].distance is metres)."""
    routes = payload.get("routes")
    if not routes:
        raise ValueError(f"OSRM returned no route: {str(payload)[:120]}")
    return round(routes[0]["distance"] / 1000.0, 4)


def build_provider(name: str, settings: Settings) -> DistanceProvider:
    """Provider registry - the only place that maps config strings to classes."""
    if name == "haversine":
        return HaversineProvider()
    if name == "circuity":
        return CircuityAdjustedProvider(HaversineProvider(), settings.distance_circuity_factor)
    if name == "osrm":
        return OsrmProvider(settings.osrm_base_url)
    raise ValueError(f"unknown distance provider: {name!r}")


def get_distance_provider(settings: Settings | None = None) -> DistanceProvider:
    """The configured champion (default: haversine - never switched without
    shadow-mode evidence)."""
    s = settings or get_settings()
    return build_provider(s.distance_provider, s)


def get_shadow_provider(settings: Settings | None = None) -> DistanceProvider | None:
    """Optional shadow challenger: computed on every distance call and logged,
    never used for business decisions, until the benchmark says otherwise."""
    s = settings or get_settings()
    if not s.distance_shadow_provider:
        return None
    return build_provider(s.distance_shadow_provider, s)


def resolve_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    provider: DistanceProvider | None = None,
    settings: Settings | None = None,
) -> float:
    """Champion distance, with optional shadow comparison logged.

    The shadow value is NEVER returned - business decisions keep using the
    champion while distributions accumulate for the migration review.
    """
    s = settings or get_settings()
    champion = provider or get_distance_provider(s)
    distance = champion.distance_km(lat1, lon1, lat2, lon2)

    shadow = get_shadow_provider(s)
    if shadow is not None:
        try:
            shadow_km = shadow.distance_km(lat1, lon1, lat2, lon2)
            delta = round((shadow_km - distance) / distance * 100, 2) if distance else 0.0
            logger.info(
                "distance shadow: champion=%s(%.4fkm) shadow=%s(%.4fkm) delta=%+.2f%%",
                champion.name,
                distance,
                shadow.name,
                shadow_km,
                delta,
            )
        except Exception:  # noqa: BLE001 - a shadow failure must never break the champion
            logger.exception("shadow provider failed; champion unaffected")

    return distance

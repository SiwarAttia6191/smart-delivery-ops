"""Driver matching service — nearest available driver to the pickup point.

Phase 1 strategy: great-circle distance to the restaurant. Phase 3 upgrades
this to load-aware allocation (driver efficiency + predicted demand).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.models import Driver
from domain.rules import driver_rules, geo


@dataclass(frozen=True)
class MatchResult:
    driver: Driver
    distance_km: float


def find_nearest_driver(
    session: Session,
    pickup_lat: float,
    pickup_lon: float,
    vehicle: str | None = None,
) -> MatchResult | None:
    """Return the closest eligible driver to the pickup point, or None."""
    best: MatchResult | None = None
    drivers = session.scalars(select(Driver)).all()
    for driver in drivers:
        if not driver_rules.is_eligible(driver, vehicle):
            continue
        distance = geo.haversine_km(pickup_lat, pickup_lon, driver.latitude, driver.longitude)
        if best is None or distance < best.distance_km:
            best = MatchResult(driver=driver, distance_km=distance)
    return best

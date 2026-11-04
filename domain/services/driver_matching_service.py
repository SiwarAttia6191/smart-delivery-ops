"""Driver matching service — nearest available driver to the pickup point.

Phase 1 strategy: great-circle distance to the restaurant. Phase 3 upgrades
this to load-aware allocation (driver efficiency + predicted demand).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.models import Driver
from domain.rules import driver_rules, geo

if TYPE_CHECKING:
    from core.config import Settings
    from domain.rules.geo import DistanceProvider


@dataclass(frozen=True)
class MatchResult:
    driver: Driver
    distance_km: float


def find_nearest_driver(
    session: Session,
    pickup_lat: float,
    pickup_lon: float,
    vehicle: str | None = None,
    settings: "Settings | None" = None,
    provider: "DistanceProvider | None" = None,
) -> MatchResult | None:
    """Return the closest eligible driver to the pickup point, or None.

    Distance comes from the configured provider (champion by default; a
    shadow challenger may log alongside it - see domain/rules/geo.py)."""
    best: MatchResult | None = None
    # Claim semantics (#190): SKIP LOCKED makes a concurrent assign skip
    # rows another worker is dispatching against — two simultaneous
    # POST /orders/{id}/assign can no longer both claim the same driver.
    # The lock releases at the assign's commit.
    drivers = session.scalars(select(Driver).with_for_update(skip_locked=True)).all()
    for driver in drivers:
        if not driver_rules.is_eligible(driver, vehicle):
            continue
        distance = geo.resolve_distance(
            pickup_lat, pickup_lon, driver.latitude, driver.longitude, provider, settings
        )
        if best is None or distance < best.distance_km:
            best = MatchResult(driver=driver, distance_km=distance)
    return best

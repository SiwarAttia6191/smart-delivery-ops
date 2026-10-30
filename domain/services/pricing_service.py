"""Pricing service — quote a delivery fee for a route.

Thin orchestration over `domain.rules.pricing_rules`; the heavy lifting stays in
pure functions so it can be exhaustively unit-tested.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.rules import geo, pricing_rules
from domain.rules.pricing_rules import PriceBreakdown

if TYPE_CHECKING:
    from core.config import Settings
    from domain.rules.geo import DistanceProvider


@dataclass(frozen=True)
class Quote:
    distance_km: float
    breakdown: PriceBreakdown

    def as_dict(self) -> dict:
        return {"distance_km": self.distance_km, **self.breakdown.as_dict()}


def quote_delivery(
    pickup_lat: float,
    pickup_lon: float,
    dropoff_lat: float,
    dropoff_lon: float,
    demand_ratio: float = 1.0,
    settings: "Settings | None" = None,
    provider: "DistanceProvider | None" = None,
) -> Quote:
    """Distance comes from the configured provider (champion by default;
    a shadow challenger may log alongside it - see domain/rules/geo.py).
    ``settings`` carries the tenant's pricing overrides (#36): the caller
    resolves them (tenant_service.effective_settings) so both the geo
    provider and the fee computation run on the tenant's configuration."""
    distance_km = geo.resolve_distance(
        pickup_lat, pickup_lon, dropoff_lat, dropoff_lon, provider, settings
    )
    breakdown = pricing_rules.compute_price(distance_km, demand_ratio, settings)
    return Quote(distance_km=distance_km, breakdown=breakdown)

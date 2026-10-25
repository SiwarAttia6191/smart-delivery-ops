"""Pricing service — quote a delivery fee for a route.

Thin orchestration over `domain.rules.pricing_rules`; the heavy lifting stays in
pure functions so it can be exhaustively unit-tested.
"""

from dataclasses import dataclass

from domain.rules import geo, pricing_rules
from domain.rules.pricing_rules import PriceBreakdown


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
) -> Quote:
    distance_km = geo.haversine_km(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon)
    breakdown = pricing_rules.compute_price(distance_km, demand_ratio)
    return Quote(distance_km=distance_km, breakdown=breakdown)

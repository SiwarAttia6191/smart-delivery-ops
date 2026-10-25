"""Distance-based pricing with demand surge tiers.

Pure functions over the configured pricing parameters — the Phase 3 dynamic
pricing engine will swap the surge calculation for an ML-informed multiplier
while keeping this same interface.
"""

from dataclasses import dataclass

from core.config import Settings, get_settings


@dataclass(frozen=True)
class PriceBreakdown:
    base_fee: float
    distance_fee: float
    surge_multiplier: float
    total: float

    def as_dict(self) -> dict:
        return {
            "base_fee": self.base_fee,
            "distance_fee": self.distance_fee,
            "surge_multiplier": self.surge_multiplier,
            "total": self.total,
        }


def surge_multiplier(demand_ratio: float, settings: Settings | None = None) -> float:
    """Demand ratio (current load / expected load) to surge multiplier.

    Three tiers benchmarked against modern platforms (surge caps 1.2×–2.5×);
    tier 3 acts as the hard cap so extreme demand can never exceed 2×.
    """
    s = settings or get_settings()
    if demand_ratio >= s.pricing_surge_tier3_ratio:
        return s.pricing_surge_tier3_multiplier
    if demand_ratio >= s.pricing_surge_tier2_ratio:
        return s.pricing_surge_tier2_multiplier
    if demand_ratio >= s.pricing_surge_tier1_ratio:
        return s.pricing_surge_tier1_multiplier
    return 1.0


def compute_price(
    distance_km: float, demand_ratio: float = 1.0, settings: Settings | None = None
) -> PriceBreakdown:
    """Fee = max(min_fee, (base_fee + per_km × distance) × surge_multiplier)."""
    if distance_km < 0:
        raise ValueError("distance_km must be non-negative")
    if demand_ratio < 0:
        raise ValueError("demand_ratio must be non-negative")

    s = settings or get_settings()
    distance_fee = round(distance_km * s.pricing_per_km, 2)
    multiplier = surge_multiplier(demand_ratio, s)
    subtotal = s.pricing_base_fee + distance_fee
    total = round(max(s.pricing_min_fee, subtotal * multiplier), 2)
    return PriceBreakdown(
        base_fee=s.pricing_base_fee,
        distance_fee=distance_fee,
        surge_multiplier=multiplier,
        total=total,
    )

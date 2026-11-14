"""Fuel economics (#217-adjacent, the self-deliverer's margin view).

The insight from the competitive scan: a self-delivering restaurant's
delivery cost is FUEL — and seeing it at quote time turns accept/reject
into math. The numbers come from the city manifest's FuelProfile: the
OFFICIAL price for the deployment's city (state-set in Tunisia, the
ministry's daily average in Spain) and the fleet's consumption per
vehicle kind. Bicycles burn nothing — their fuel cost is zero, which is
itself a useful answer.

Honest boundaries: this is the city-level official price, not the
closest station's pump price (station-level free data doesn't exist
reliably anywhere; country integrations are the V2). And the CO₂ factor
(2.31 kg/L gasoline combustion) is the standard conversion, not an
invention.
"""

from typing import Any

from domain.city_config import CityConfig, FuelProfile

CO2_KG_PER_LITER_GASOLINE = 2.31
DEFAULT_VEHICLE = "motorcycle"  # the fleet's dominant kind — the quote-time basis


def estimate(config: CityConfig | None, distance_km: float) -> dict[str, Any] | None:
    """The quote-time estimate: what fuel THIS delivery would burn on the
    deployment's dominant vehicle (the assignment hasn't happened yet).
    Returns None when the manifest declares no fuel economics — the
    estimate is absent, not guessed."""
    return _compute(config, distance_km, DEFAULT_VEHICLE, basis="estimate")


def actual(config: CityConfig | None, distance_km: float, vehicle: str | None) -> dict[str, Any] | None:
    """The delivered truth: the assigned driver's vehicle decides the
    burn — a bicycle delivery's fuel cost is zero, which the totals show
    honestly instead of pretending every delivery burns gas."""
    return _compute(config, distance_km, vehicle or DEFAULT_VEHICLE, basis="actual")


def _compute(config: CityConfig | None, distance_km: float, vehicle: str, basis: str) -> dict[str, Any] | None:
    if config is None or config.fuel is None:
        return None
    profile: FuelProfile = config.fuel
    consumption = profile.consumption_l_per_100km.get(vehicle, 0.0)
    liters = round(distance_km / 100.0 * consumption, 3)
    cost = round(liters * profile.price_per_liter, 2)
    return {
        "basis": basis,
        "vehicle": vehicle,
        "distance_km": round(distance_km, 2),
        "liters": liters,
        "cost": cost,
        "currency": profile.currency,
        "co2_kg": round(liters * profile.co2_kg_per_liter, 3),
    }

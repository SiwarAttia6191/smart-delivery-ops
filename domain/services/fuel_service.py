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
from domain.rules import geo

CO2_KG_PER_LITER_GASOLINE = 2.31
DEFAULT_VEHICLE = "motorcycle"  # the fleet's dominant kind — the quote-time basis


def estimate(config: CityConfig | None, distance_km: float) -> dict[str, Any] | None:
    """The quote-time estimate: what fuel THIS delivery would burn on the
    deployment's dominant vehicle (the assignment hasn't happened yet).
    Returns None when the manifest declares no fuel economics — the
    estimate is absent, not guessed."""
    return _compute(config, distance_km, DEFAULT_VEHICLE, basis="estimate")


def actual(
    config: CityConfig | None, distance_km: float, vehicle: str | None
) -> dict[str, Any] | None:
    """The delivered truth: the assigned driver's vehicle decides the
    burn — a bicycle delivery's fuel cost is zero, which the totals show
    honestly instead of pretending every delivery burns gas."""
    return _compute(config, distance_km, vehicle or DEFAULT_VEHICLE, basis="actual")


def nearest_station_price(
    session: Any, config: CityConfig | None, latitude: float, longitude: float
) -> dict[str, Any] | None:
    """The nearest cached station's price for the deployment's fuel kind
    (#274 V2): a bounding box (+-0.05 degrees, ~5.5 km of latitude) over
    the station cache, then haversine top-1 — no external calls at
    request time. Returns None when the cache holds no stations for the
    country: the manifest constant covers the estimate (the V1 truth)."""
    if config is None or config.fuel is None or not config.fuel.country_code:
        return None
    from domain.models import FuelPriceStation

    kind = config.fuel.station_fuel_kind or "diesel"
    stations = (
        session.query(FuelPriceStation)
        .filter(
            FuelPriceStation.country == config.fuel.country_code,
            FuelPriceStation.fuel_kind == kind,
            FuelPriceStation.latitude.between(latitude - 0.05, latitude + 0.05),
            FuelPriceStation.longitude.between(longitude - 0.07, longitude + 0.07),
        )
        .all()
    )
    best = None
    best_distance: float | None = None
    for station in stations:
        distance = geo.haversine_km(latitude, longitude, station.latitude, station.longitude)
        if best_distance is None or distance < best_distance:
            best, best_distance = station, distance
    if best is None or best_distance is None:
        return None
    return {
        "station_id": best.station_id,
        "station_name": best.name,
        "latitude": best.latitude,
        "longitude": best.longitude,
        "fuel_kind": best.fuel_kind,
        "price": best.price,
        "currency": best.currency,
        "updated_at": best.updated_at,
        "source": best.source,
        "distance_km": round(best_distance, 3),
    }


def _compute(
    config: CityConfig | None, distance_km: float, vehicle: str, basis: str
) -> dict[str, Any] | None:
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

"""Driver eligibility rules used by the matching service."""

from core.constants import VEHICLE_TYPES
from domain.models.driver import Driver


def is_eligible(driver: Driver, vehicle: str | None = None) -> bool:
    """A driver is eligible when available and matching the requested vehicle."""
    if not driver.is_available:
        return False
    if vehicle is not None and driver.vehicle != vehicle:
        return False
    return True


def assert_valid_vehicle(vehicle: str) -> None:
    if vehicle not in VEHICLE_TYPES:
        raise ValueError(f"vehicle must be one of {VEHICLE_TYPES}")

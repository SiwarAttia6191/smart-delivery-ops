"""Record driver availability changes atomically with driver-state mutations."""

from typing import Literal

from sqlalchemy.orm import Session

from domain.models import Driver, DriverEvent

AvailabilityReason = Literal[
    "registered",
    "heartbeat",
    "order_assigned",
    "order_delivered",
    "order_cancelled",
]


def record_availability_change(
    session: Session,
    driver: Driver,
    previous: bool | None,
    available: bool,
    reason: AvailabilityReason,
    order_id: str | None = None,
) -> None:
    """Add an immutable availability event when the state actually changes."""
    if previous is available:
        return

    payload = {
        "from_available": previous,
        "to_available": available,
        "latitude": driver.latitude,
        "longitude": driver.longitude,
        "vehicle": driver.vehicle,
        "reason": reason,
    }
    if order_id is not None:
        payload["order_id"] = order_id
    session.add(
        DriverEvent(
            driver_id=driver.id,
            event_type="availability",
            payload=payload,
        )
    )

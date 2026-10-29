"""Record driver availability changes atomically with driver-state mutations."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.config import get_settings
from domain.models import Driver, DriverEvent
from domain.rules.geo import haversine_km

logger = logging.getLogger(__name__)

AvailabilityReason = Literal[
    "registered",
    "heartbeat",
    "order_assigned",
    "order_delivered",
    "order_cancelled",
    "stale_sweep",  # #60: worker-forced offline after a silent heartbeat window
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


LOCATION_EVENT_TYPES = ("availability", "location_update")


def _last_evented_position(session: Session, driver_id: int) -> tuple[float, float] | None:
    """The driver's most recently evented coordinates, from whichever event
    type carries positions (availability transitions and location updates)."""
    row = session.execute(
        sa.select(DriverEvent.payload)
        .where(
            DriverEvent.driver_id == driver_id,
            DriverEvent.event_type.in_(LOCATION_EVENT_TYPES),
        )
        .order_by(DriverEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not row:
        return None
    lat, lon = row.get("latitude"), row.get("longitude")
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def record_location_update(session: Session, driver: Driver) -> bool:
    """Emit a location_update event when the driver moved beyond the
    hysteresis threshold (#60) - event volume scales with movement, not with
    heartbeat frequency. Returns True when an event was emitted."""
    last = _last_evented_position(session, driver.id)
    settings = get_settings()
    if last is not None:
        moved_m = haversine_km(last[0], last[1], driver.latitude, driver.longitude) * 1000.0
        if moved_m <= settings.driver_location_update_threshold_m:
            return False
    session.add(
        DriverEvent(
            driver_id=driver.id,
            event_type="location_update",
            payload={
                "latitude": driver.latitude,
                "longitude": driver.longitude,
                "vehicle": driver.vehicle,
                # lets the supply mart filter moving-but-offline drivers out
                "available": driver.is_available,
                "distance_from_last_m": (
                    round(
                        haversine_km(last[0], last[1], driver.latitude, driver.longitude) * 1000.0,
                        1,
                    )
                    if last
                    else None
                ),
            },
        )
    )
    return True


def sweep_stale_drivers(session: Session) -> int:
    """Force heartbeats gone silent offline (#60 closes the stale-driver
    carry-forward hole): an available driver whose last event predates
    DRIVER_STALE_AFTER_MINUTES gets a forced availability event (reason
    stale_sweep) and flips to unavailable. Returns the number swept."""
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.driver_stale_after_minutes)
    drivers = session.scalars(sa.select(Driver).where(Driver.is_available.is_(True))).all()
    swept = 0
    for driver in drivers:
        last_event_at = session.scalar(
            sa.select(sa.func.max(DriverEvent.created_at)).where(DriverEvent.driver_id == driver.id)
        )
        reference = last_event_at or driver.created_at
        if reference is not None and reference.replace(tzinfo=UTC) >= cutoff:
            continue
        previous = driver.is_available
        driver.is_available = False
        record_availability_change(session, driver, previous, False, "stale_sweep")
        swept += 1
    if swept:
        session.commit()
        logger.info("stale-driver sweep: %d driver(s) forced offline", swept)
    return swept

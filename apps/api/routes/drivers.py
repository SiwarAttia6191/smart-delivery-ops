"""Driver endpoints."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.driver import DriverCreate, DriverResponse, DriverUpdate
from core.exceptions import NotFoundError
from domain.models import Driver
from domain.services import tenant_service
from domain.services.driver_event_service import (
    record_availability_change,
    record_location_update,
)

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.post(
    "",
    response_model=DriverResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writer)],
)
def create_driver(payload: DriverCreate, db: Session = Depends(get_db)) -> Driver:
    driver = Driver(
        tenant_id=tenant_service.context_tenant_id(db),
        name=payload.name,
        vehicle=payload.vehicle,
        latitude=payload.latitude,
        longitude=payload.longitude,
        is_available=True,
    )
    db.add(driver)
    db.flush()
    record_availability_change(db, driver, None, True, "registered")
    db.commit()
    db.refresh(driver)
    return driver


@router.get("", response_model=list[DriverResponse])
def list_drivers(
    available: bool | None = Query(None, description="Filter by availability"),
    db: Session = Depends(get_db),
) -> list[Driver]:
    stmt = select(Driver).order_by(Driver.id)
    if available is not None:
        stmt = stmt.where(Driver.is_available == available)
    return list(db.scalars(stmt))


@router.get("/{driver_id}", response_model=DriverResponse)
def get_driver(driver_id: int, db: Session = Depends(get_db)) -> Driver:
    driver = db.get(Driver, driver_id)
    if driver is None:
        raise NotFoundError(f"driver {driver_id} not found")
    return driver


@router.patch(
    "/{driver_id}",
    response_model=DriverResponse,
    dependencies=[Depends(require_writer)],
)
def update_driver(driver_id: int, payload: DriverUpdate, db: Session = Depends(get_db)) -> Driver:
    """Update a driver's live location and availability (heartbeat)."""
    driver = db.get(Driver, driver_id)
    if driver is None:
        raise NotFoundError(f"driver {driver_id} not found")
    previous_availability = driver.is_available
    driver.latitude = payload.latitude
    driver.longitude = payload.longitude
    driver.is_available = payload.is_available
    record_availability_change(db, driver, previous_availability, payload.is_available, "heartbeat")
    # #60: availability events stay semantic; a large-enough move gets its own
    # location_update event so supply follows the driver across zones.
    record_location_update(db, driver)
    db.commit()
    db.refresh(driver)
    return driver

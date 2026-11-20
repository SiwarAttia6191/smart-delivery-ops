"""Driver endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_driver, require_transition_caller, require_writer
from apps.api.schemas.driver import DriverCreate, DriverResponse, DriverUpdate
from apps.api.schemas.order import OrderResponse
from core.exceptions import NotFoundError
from domain.models import Driver, Order
from domain.services import tenant_service
from domain.services.driver_event_service import (
    record_availability_change,
    record_location_update,
)

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("/me", response_model=DriverResponse)
def my_driver_profile(claims: dict = Depends(require_driver), db: Session = Depends(get_db)):
    """The signed-in courier's own fleet row (#311): vehicle, live
    location, availability — the driver page's profile card."""
    driver = db.get(Driver, int(claims["did"]))
    if driver is None:
        raise NotFoundError(f"driver {claims['did']} not found")
    return driver


@router.get("/me/jobs", response_model=list[OrderResponse])
def my_jobs(
    claims: dict = Depends(require_driver),
    db: Session = Depends(get_db),
    limit: int = Query(25, ge=1, le=100),
):
    """The signed-in courier's jobs (#311): the orders assigned to THEIR
    fleet row, newest first, with the event log the dispatch and the
    warehouse share. Courier-scoped by the did claim, tenant-scoped by
    RLS — a courier never sees another courier's runs."""
    orders = db.scalars(
        select(Order)
        .where(Order.driver_id == int(claims["did"]))
        .order_by(Order.created_at.desc())
        .limit(limit)
    ).all()
    return orders


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
)
def update_driver(
    driver_id: int,
    payload: DriverUpdate,
    db: Session = Depends(get_db),
    caller: dict = Depends(require_transition_caller),
):
    """Update a driver's live location and availability (heartbeat).

    Two caller kinds (#311): STAFF may heartbeat any fleet row; a DRIVER
    token may only heartbeat their OWN row — the did claim scopes the
    courier's self-management."""
    if caller["kind"] == "driver" and driver_id != caller["driver_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="couriers may only update their own status",
        )
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

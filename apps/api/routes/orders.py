"""Order endpoints — create, assign, transition, query."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from apps.api.deps import get_db
from apps.api.schemas.order import OrderCreate, OrderResponse, OrderStatusUpdate
from domain.models import OrderStatus
from domain.services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    return order_service.create_order(
        db,
        customer_id=payload.customer_id,
        restaurant_id=payload.restaurant_id,
        dropoff_lat=payload.dropoff_latitude,
        dropoff_lon=payload.dropoff_longitude,
        demand_ratio=payload.demand_ratio,
    )


@router.get("", response_model=list[OrderResponse])
def list_orders(
    status_filter: OrderStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return order_service.list_orders(db, status=status_filter, limit=limit)


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db)):
    return order_service.get_order(db, order_id)


@router.post("/{order_id}/assign", response_model=OrderResponse)
def assign_driver(
    order_id: str,
    vehicle: Literal["bicycle", "motorcycle", "car"] | None = Query(
        None, description="Restrict matching to a vehicle type"
    ),
    db: Session = Depends(get_db),
):
    """Assign the nearest available driver to a pending order."""
    return order_service.assign_driver(db, order_id, vehicle)


@router.post("/{order_id}/status", response_model=OrderResponse)
def update_status(order_id: str, payload: OrderStatusUpdate, db: Session = Depends(get_db)):
    """Advance the lifecycle: pending/assigned → in_transit → delivered (or cancelled)."""
    return order_service.transition(db, order_id, payload.status)

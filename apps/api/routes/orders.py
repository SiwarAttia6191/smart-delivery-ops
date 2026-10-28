"""Order endpoints — create, assign, transition, query."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from apps.api.deps import get_db
from apps.api.schemas.order import OrderCreate, OrderResponse, OrderStatusUpdate
from core.exceptions import NotFoundError
from domain.models import Order, OrderStatus
from domain.services import order_service, route_service

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


@router.get("/{order_id}/route", response_model=None)
def get_order_route(order_id: str, db: Session = Depends(get_db)):
    """The road geometry (GeoJSON LineString) captured for this order's
    pickup→dropoff pair — 404 if the order doesn't exist, 404-with-reason
    if no geometry was ever captured (OSRM was down at creation)."""
    order = db.get(Order, order_id)
    if order is None:
        raise NotFoundError(f"order {order_id} not found")
    geojson = route_service.geojson_for_order(
        db,
        order.pickup_latitude,
        order.pickup_longitude,
        order.dropoff_latitude,
        order.dropoff_longitude,
    )
    if geojson is None:
        raise NotFoundError(
            f"no route geometry captured for order {order_id} (OSRM unavailable at creation)"
        )
    return Response(content=json.dumps(geojson), media_type="application/geo+json")


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

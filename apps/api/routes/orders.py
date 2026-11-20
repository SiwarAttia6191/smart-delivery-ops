"""Order endpoints — create, assign, transition, query."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from apps.api.deps import (
    get_db,
    require_order_creator,
    require_transition_caller,
    require_writer,
)
from apps.api.schemas.order import (
    OrderCreate,
    OrderResponse,
    OrderStatusUpdate,
    ReviewCreate,
    ReviewOut,
)
from core.exceptions import NotFoundError
from domain.models import Order, OrderReview, OrderStatus
from domain.services import order_service, review_service, route_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    caller: dict = Depends(require_order_creator),
):
    # Self-ordering enforcement (#309): a customer token may only ever
    # create orders for ITSELF — the payload's customer_id is overridden,
    # never trusted. Staff callers keep the on-behalf-of flow.
    if caller["kind"] == "customer":
        payload.customer_id = caller["customer_id"]
    return order_service.create_order(
        db,
        customer_id=payload.customer_id,
        restaurant_id=payload.restaurant_id,
        dropoff_lat=payload.dropoff_latitude,
        dropoff_lon=payload.dropoff_longitude,
        demand_ratio=payload.demand_ratio,
        coupon_code=payload.coupon_code,
        source=payload.source,
        dropoff_address=payload.dropoff_address,
        priority=payload.priority,
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


@router.post(
    "/{order_id}/assign",
    response_model=OrderResponse,
    dependencies=[Depends(require_writer)],
)
def assign_driver(
    order_id: str,
    vehicle: Literal["bicycle", "motorcycle", "car"] | None = Query(
        None, description="Restrict matching to a vehicle type"
    ),
    db: Session = Depends(get_db),
):
    """Assign the nearest available driver to a pending order."""
    return order_service.assign_driver(db, order_id, vehicle)


@router.post(
    "/{order_id}/status",
    response_model=OrderResponse,
)
def update_status(
    order_id: str,
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db),
    caller: dict = Depends(require_transition_caller),
):
    """Advance the lifecycle: pending/assigned → in_transit → delivered (or cancelled).

    Two caller kinds (#311): STAFF may advance any order; a DRIVER token
    may only advance THEIR OWN assigned order, and only the ride-and-
    deliver transitions — the cancel stays with the operator."""
    if caller["kind"] == "driver":
        order = db.get(Order, order_id)
        if order is None or order.driver_id != caller["driver_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="this order is not assigned to you",
            )
        if payload.status not in (OrderStatus.in_transit, OrderStatus.delivered):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="drivers may only start transit or mark delivered",
            )
    return order_service.transition(db, order_id, payload.status)


@router.post(
    "/{order_id}/review",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writer)],
)
def review_order(
    order_id: str, payload: ReviewCreate, db: Session = Depends(get_db)
) -> OrderReview:
    """Rate a DELIVERED order (#216): rating 1-5, optional comment. One
    review per order (the database enforces it); a non-delivered order is
    a 409, and another tenant's order is the uniform 404."""
    return review_service.create_review(db, order_id, payload.rating, payload.comment)

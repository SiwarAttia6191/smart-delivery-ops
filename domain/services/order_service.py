"""Order service — the order lifecycle use-cases.

State machine (Phase 1):

    pending ──assign──▶ assigned ──status──▶ in_transit ──status──▶ delivered
       │                   │                     │
       └──────cancel───────┴────────cancel───────┘

Every mutation appends an OrderEvent; those events become the raw feed for the
Phase 2 ingestion pipeline and the Phase 3 forecasting features.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.exceptions import (
    InvalidTransitionError,
    NoDriverAvailableError,
    NotFoundError,
)
from domain.models import Customer, Order, OrderEvent, OrderStatus, Restaurant
from domain.rules import geo
from domain.services import (
    demand_service,
    driver_matching_service,
    eta_model_service,
    eta_service,
    pricing_service,
    route_service,
    tenant_service,
)
from domain.services.driver_event_service import AvailabilityReason, record_availability_change
from domain.services.pricing_service import Quote

_ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.pending: {OrderStatus.cancelled},
    OrderStatus.assigned: {OrderStatus.in_transit, OrderStatus.cancelled},
    OrderStatus.in_transit: {OrderStatus.delivered, OrderStatus.cancelled},
    OrderStatus.delivered: set(),
    OrderStatus.cancelled: set(),
}


def _event(session: Session, order: Order, event_type: str, payload: dict[str, Any]) -> None:
    # Event envelope (#35): the tenant rides inside the payload (the raw
    # layer stores payloads as received) and as the queryable column.
    session.add(
        OrderEvent(
            order=order,
            tenant_id=order.tenant_id,
            event_type=event_type,
            payload=tenant_service.stamp_payload(payload, order.tenant_id),
        )
    )


def _get_order(session: Session, order_id: str) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise NotFoundError(f"order {order_id} not found")
    return order


def _free_driver(session: Session, order: Order, reason: AvailabilityReason) -> None:
    if order.driver is not None and order.driver.is_available is False:
        order.driver.is_available = True
        record_availability_change(
            session=session,
            driver=order.driver,
            previous=False,
            available=True,
            reason=reason,
            order_id=order.id,
        )


def create_order(
    session: Session,
    customer_id: int,
    restaurant_id: int,
    dropoff_lat: float,
    dropoff_lon: float,
    demand_ratio: float | None = None,
) -> Order:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"customer {customer_id} not found")
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise NotFoundError(f"restaurant {restaurant_id} not found")

    if demand_ratio is None:
        demand_ratio = demand_service.derive_demand_ratio(session, dropoff_lat, dropoff_lon)
    quote: Quote = pricing_service.quote_delivery(
        restaurant.latitude,
        restaurant.longitude,
        dropoff_lat,
        dropoff_lon,
        demand_ratio,
        settings=tenant_service.effective_settings(session),
    )

    order = Order(
        tenant_id=tenant_service.context_tenant_id(session),
        customer=customer,
        restaurant=restaurant,
        status=OrderStatus.pending,
        pickup_latitude=restaurant.latitude,
        pickup_longitude=restaurant.longitude,
        dropoff_latitude=dropoff_lat,
        dropoff_longitude=dropoff_lon,
        distance_km=quote.distance_km,
        delivery_fee=quote.breakdown.total,
        surge_multiplier=quote.breakdown.surge_multiplier,
        eta_minutes=(
            eta_model_service.estimate_eta_minutes(
                quote.distance_km,
                restaurant.latitude,
                restaurant.longitude,
                dropoff_lat,
                dropoff_lon,
            )
            if eta_model_service.is_serving()
            else None
        )
        or eta_service.estimate_eta_minutes(quote.distance_km),
    )
    _event(
        session,
        order,
        "created",
        {
            "customer_id": customer_id,
            "restaurant_id": restaurant_id,
            "distance_km": quote.distance_km,
            "delivery_fee": quote.breakdown.total,
            "surge_multiplier": quote.breakdown.surge_multiplier,
            "eta_minutes": order.eta_minutes,
            # Coordinates ride on the event so downstream consumers (zone
            # demand marts, dispatch analytics) never need the ops tables.
            "pickup_latitude": restaurant.latitude,
            "pickup_longitude": restaurant.longitude,
            "dropoff_latitude": dropoff_lat,
            "dropoff_longitude": dropoff_lon,
        },
    )
    session.add(order)
    session.commit()
    session.refresh(order)

    # Route geometry is a feature, not a requirement (#58): capture AFTER the
    # order commits, best-effort, so an OSRM stall never holds or breaks the
    # order transaction.
    route_service.capture_for_order(
        session, restaurant.latitude, restaurant.longitude, dropoff_lat, dropoff_lon
    )
    return order


def assign_driver(session: Session, order_id: str, vehicle: str | None = None) -> Order:
    order = _get_order(session, order_id)
    if order.status is not OrderStatus.pending:
        raise InvalidTransitionError(
            f"cannot assign driver to order in status '{order.status.value}' "
            "(only pending orders can be assigned)"
        )

    match = driver_matching_service.find_nearest_driver(
        session, order.pickup_latitude, order.pickup_longitude, vehicle
    )
    if match is None:
        raise NoDriverAvailableError("no eligible driver is currently available")

    order.driver = match.driver
    order.status = OrderStatus.assigned
    previous_availability = match.driver.is_available
    match.driver.is_available = False
    record_availability_change(
        session, match.driver, previous_availability, False, "order_assigned", order.id
    )
    _event(
        session,
        order,
        "assigned",
        {
            "driver_id": match.driver.id,
            "match_distance_km": match.distance_km,
            "vehicle": match.driver.vehicle,
        },
    )
    session.commit()
    session.refresh(order)
    return order


def transition(session: Session, order_id: str, new_status: OrderStatus) -> Order:
    order = _get_order(session, order_id)
    current = order.status
    if new_status is current:
        raise InvalidTransitionError(f"order is already in status '{current.value}'")
    if new_status not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"transition '{current.value}' → '{new_status.value}' is not allowed"
        )

    if new_status is OrderStatus.delivered:
        from datetime import UTC, datetime

        order.delivered_at = datetime.now(UTC)
        _free_driver(session, order, "order_delivered")
    elif new_status is OrderStatus.cancelled:
        _free_driver(session, order, "order_cancelled")

    order.status = new_status
    _event(session, order, "status", {"from": current.value, "to": new_status.value})
    session.commit()
    session.refresh(order)
    return order


def get_order(session: Session, order_id: str) -> Order:
    return _get_order(session, order_id)


def list_orders(
    session: Session, status: OrderStatus | None = None, limit: int = 50
) -> list[Order]:
    stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    return list(session.scalars(stmt))


def driver_distance_snapshot(order: Order) -> float:
    """Distance from the assigned driver's live position to the pickup point."""
    if order.driver is None:
        raise ValueError("order has no assigned driver")
    return geo.haversine_km(
        order.pickup_latitude,
        order.pickup_longitude,
        order.driver.latitude,
        order.driver.longitude,
    )

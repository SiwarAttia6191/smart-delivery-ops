"""Restaurant sales snapshot (#219) — the portal's own numbers.

A DELIBERATE V1 boundary: this is a live ops-slice computed per request
from the tenant's own orders — NOT a warehouse mart. The analytical
version (trends, channel attribution, payment-verified revenue) belongs
to the dashboard and the dbt marts; this answers exactly one question at
portal-open time: "how did TODAY go?" — computed in the deployment's
business timezone (a delivery at 02:00 Madrid time belongs to yesterday's
business day), delivered orders only.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import get_settings
from domain.city_config import load_city_config
from domain.models import Order, OrderReview, OrderStatus, Restaurant
from domain.services import fuel_service


def snapshot(session: Session, restaurant: Restaurant) -> dict:
    """Today's (business-day) delivered slice for ONE restaurant."""
    tz = ZoneInfo(get_settings().business_timezone)
    now_local = datetime.now(UTC).astimezone(tz)
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = day_start_local.astimezone(UTC)
    window_end = window_start + timedelta(days=1)

    delivered = (
        session.query(Order)
        .filter(
            Order.restaurant_id == restaurant.id,
            Order.tenant_id == restaurant.tenant_id,
            Order.status == OrderStatus.delivered,
            Order.delivered_at >= window_start,
            Order.delivered_at < window_end,
        )
        .all()
    )

    minutes = [o.delivery_minutes for o in delivered if o.delivery_minutes is not None]

    # the restaurant's rating average (#216): the same join the portal's
    # review card uses — the key existed as null before reviews landed so
    # the snapshot's shape never changed
    rating_average = session.scalar(
        select(func.avg(OrderReview.rating))
        .join(Order, OrderReview.order_id == Order.id)
        .where(Order.restaurant_id == restaurant.id, OrderReview.tenant_id == restaurant.tenant_id)
    )

    # fuel actuals (#user idea): the assigned driver's vehicle decides the
    # burn per delivered order — bicycles contribute zero, honestly
    snapshot_fuel: dict | None = None
    from core.config import get_settings as _gs

    config = load_city_config(_gs().city)
    if config.fuel is not None:
        liters_total = 0.0
        cost_total = 0.0
        co2_total = 0.0
        for order in delivered:
            vehicle = order.driver.vehicle if order.driver is not None else None
            fuel = fuel_service.actual(config, order.distance_km, vehicle)
            if fuel is not None:
                liters_total += fuel["liters"]
                cost_total += fuel["cost"]
                co2_total += fuel["co2_kg"]
        snapshot_fuel = {
            "liters": round(liters_total, 2),
            "cost": round(cost_total, 2),
            "co2_kg": round(co2_total, 2),
            "currency": config.fuel.currency,
        }

    return {
        "restaurant_id": restaurant.id,
        "business_date": day_start_local.date().isoformat(),
        "delivered_orders": len(delivered),
        "revenue": round(sum(o.delivery_fee for o in delivered), 2),
        "avg_delivery_minutes": round(sum(minutes) / len(minutes), 1) if minutes else None,
        "rating_average": round(float(rating_average), 2) if rating_average is not None else None,
        "fuel": snapshot_fuel,
    }

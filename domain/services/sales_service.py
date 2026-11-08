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

from sqlalchemy.orm import Session

from core.config import get_settings
from domain.models import Order, OrderStatus, Restaurant


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
    return {
        "restaurant_id": restaurant.id,
        "business_date": day_start_local.date().isoformat(),
        "delivered_orders": len(delivered),
        "revenue": round(sum(o.delivery_fee for o in delivered), 2),
        "avg_delivery_minutes": round(sum(minutes) / len(minutes), 1) if minutes else None,
        # reviews land with #216 — the key exists NOW so the portal's shape
        # never changes when they do
        "rating_average": None,
    }

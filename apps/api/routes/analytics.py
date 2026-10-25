"""Analytics endpoints — Phase 1 operational summary + Prometheus text metrics."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.deps import get_db
from apps.api.schemas.analytics import AnalyticsSummary
from domain.models import Driver, Order, OrderStatus, Restaurant

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _summary(db: Session) -> AnalyticsSummary:
    total = db.scalar(select(func.count(Order.id))) or 0
    raw_counts = dict(
        db.execute(select(Order.status, func.count(Order.id)).group_by(Order.status)).all()
    )
    counts_by_status = {status.value: count for status, count in raw_counts.items()}

    delivered_rows = db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.delivery_fee), 0.0)).where(
            Order.status == OrderStatus.delivered
        )
    ).one()
    delivered_count, delivered_revenue = delivered_rows

    avg_fee = db.scalar(select(func.avg(Order.delivery_fee)))

    delivered_orders = db.scalars(
        select(Order).where(
            Order.status == OrderStatus.delivered,
            Order.delivered_at.is_not(None),
        )
    ).all()
    if delivered_orders:
        minutes = [
            (o.delivered_at - o.created_at).total_seconds() / 60
            for o in delivered_orders
            if o.delivered_at is not None
        ]
        avg_minutes = sum(minutes) / len(minutes) if minutes else None
    else:
        avg_minutes = None

    drivers_available = db.scalar(select(func.count(Driver.id)).where(Driver.is_available)) or 0
    drivers_total = db.scalar(select(func.count(Driver.id))) or 0
    restaurants_active = (
        db.scalar(select(func.count(Restaurant.id)).where(Restaurant.is_active)) or 0
    )

    return AnalyticsSummary(
        orders_total=total,
        orders_by_status=counts_by_status,
        delivered_revenue=round(float(delivered_revenue), 2),
        average_fee=round(float(avg_fee), 2) if avg_fee is not None else 0.0,
        average_delivery_minutes=(
            round(float(avg_minutes), 2) if avg_minutes is not None else None
        ),
        drivers_available=drivers_available,
        drivers_total=drivers_total,
        restaurants_active=restaurants_active,
    )


@router.get("/summary", response_model=AnalyticsSummary)
def summary(db: Session = Depends(get_db)) -> AnalyticsSummary:
    return _summary(db)


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> Response:
    """Prometheus text exposition of the operational counters."""
    s = _summary(db)
    lines = ["# HELP sdo_orders_total Total orders by status", "# TYPE sdo_orders_total gauge"]
    for status in ("pending", "assigned", "in_transit", "delivered", "cancelled"):
        lines.append(f'sdo_orders_total{{status="{status}"}} {s.orders_by_status.get(status, 0)}')
    lines += [
        "# HELP sdo_drivers_available Currently available drivers",
        "# TYPE sdo_drivers_available gauge",
        f"sdo_drivers_available {s.drivers_available}",
        "# HELP sdo_delivered_revenue_total Revenue from delivered orders",
        "# TYPE sdo_delivered_revenue_total gauge",
        f"sdo_delivered_revenue_total {s.delivered_revenue}",
    ]
    return Response(media_type="text/plain; version=0.0.4", content="\n".join(lines) + "\n")

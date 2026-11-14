"""Review service (#216) — the post-purchase rating flow.

The lifecycle rule: a review exists only for a DELIVERED order of the
caller's tenant, and the order_id UNIQUE constraint makes
one-review-per-order a database rule (a second attempt is a 409, not a
silent duplicate). Tenant-scoped like every business service: the
explicit tenant filter keeps SQLite (tests, no RLS) correct and Postgres
RLS (the 14th tenant policy) re-enforces it at the engine.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.exceptions import InvalidTransitionError, NotFoundError
from domain.models import Order, OrderReview, OrderStatus, Restaurant
from domain.services import tenant_service


def create_review(session: Session, order_id: str, rating: int, comment: str | None) -> OrderReview:
    """Rate a DELIVERED order (writer). One review per order, forever."""
    tid = tenant_service.context_tenant_id(session)
    order = session.query(Order).filter(Order.id == order_id, Order.tenant_id == tid).one_or_none()
    if order is None:
        raise NotFoundError(f"order {order_id} not found")
    if order.status is not OrderStatus.delivered:
        raise InvalidTransitionError(
            f"cannot review an order in status '{order.status.value}' (only delivered orders can be rated)"
        )
    existing = session.query(OrderReview).filter(OrderReview.order_id == order.id).one_or_none()
    if existing is not None:
        raise InvalidTransitionError(
            f"order {order_id} is already reviewed (rating {existing.rating})"
        )
    review = OrderReview(
        order_id=order.id,
        tenant_id=tid,
        rating=rating,
        comment=(comment or None),
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def list_for_restaurant(session: Session, restaurant: Restaurant) -> dict:
    """The restaurant's reviews (open GET, tenant-scoped): newest first,
    plus the running average the portal card shows."""
    tid = tenant_service.context_tenant_id(session)
    reviews = (
        session.query(OrderReview)
        .join(Order, OrderReview.order_id == Order.id)
        .filter(Order.restaurant_id == restaurant.id, OrderReview.tenant_id == tid)
        .order_by(OrderReview.id.desc())
        .limit(50)
        .all()
    )
    average = session.scalar(
        select(func.avg(OrderReview.rating))
        .join(Order, OrderReview.order_id == Order.id)
        .where(Order.restaurant_id == restaurant.id, OrderReview.tenant_id == tid)
    )
    return {
        "restaurant_id": restaurant.id,
        "review_count": len(reviews),
        "rating_average": round(float(average), 2) if average is not None else None,
        "reviews": [
            {
                "id": r.id,
                "order_id": r.order_id,
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at,
            }
            for r in reviews
        ],
    }

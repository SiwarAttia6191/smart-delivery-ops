"""Promotions: coupon lookup + discount math (#159 item 3).

The discount mirror of the pricing engine. Lookup is tenant-scoped twice:
the explicit ``tenant_id`` filter keeps SQLite (tests, no RLS) correct and
Postgres RLS (migration 006) re-enforces the same scope at the engine.
Every rejection path — unknown, inactive, expired, redemption budget
spent — collapses to "no usable coupon", so the API returns one uniform
message and codes cannot be probed for existence.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from domain.models import Coupon
from domain.services import tenant_service


def _expired(coupon: Coupon) -> bool:
    if coupon.expires_at is None:
        return False
    expires = coupon.expires_at
    if expires.tzinfo is None:
        # SQLite (tests) stores TIMESTAMP naive even for DateTime(timezone=True)
        expires = expires.replace(tzinfo=UTC)
    return expires <= datetime.now(UTC)


def active_coupon(session: Session, code: str) -> Coupon | None:
    """The CURRENT request tenant's coupon for ``code``, if it is usable
    right now: active, not expired, redemption budget not spent."""
    tid = tenant_service.context_tenant_id(session)
    coupon = (
        session.query(Coupon)
        .filter(Coupon.tenant_id == tid, Coupon.code == code.strip().upper())
        .one_or_none()
    )
    if coupon is None or not coupon.is_active:
        return None
    if _expired(coupon):
        return None
    if coupon.max_redemptions is not None and coupon.times_used >= coupon.max_redemptions:
        return None
    return coupon


def apply_discount(coupon: Coupon, total: float) -> float:
    """The coupon's discount against a quoted fee — never more than the
    fee itself (an amount coupon larger than the total discounts to zero,
    not a payout), rounded to cents."""
    if coupon.discount_kind == "percent":
        raw = total * coupon.discount_value / 100.0
    else:
        raw = coupon.discount_value
    return round(min(raw, total), 2)

"""OrderReview — the customer's rating of a DELIVERED order (#216).

The last "their win" in the post-purchase flow: the aggregators capture a
rating after delivery; so do we now. The order_id UNIQUE constraint makes
one-review-per-order a DATABASE rule. Tenant-scoped like every business
row (RLS, the 14th tenant policy): tenant A's reviews are invisible to
tenant B by the engine, not by convention.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OrderReview(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OrderReview order={self.order_id[:8]} rating={self.rating}>"

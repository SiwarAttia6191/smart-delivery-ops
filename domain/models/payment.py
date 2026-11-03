"""Payment — a settled charge for an order's delivery fee (#159 item 2).

The checkout seam: the pricing engine produces the amount, the payment
provider settles it. V1 ships the SANDBOX provider (instant, deterministic,
no money moves) behind a `payment_provider` settings knob — a real gateway
is a config flip plus one class implementing the same two-method seam.

One row per settled charge; the sandbox never fails, so retries/webhook
states (pending/failed) are a documented growth path, not schema today.
Coupon audit lives on the ORDER's `created` event payload (the fee was
already discounted before the charge), not here.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10), default="paid")
    provider: Mapped[str] = mapped_column(String(20), default="sandbox")
    provider_ref: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Payment {self.id} order={self.order_id} {self.amount} {self.status}>"

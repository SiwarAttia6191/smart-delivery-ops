"""Coupon — a per-tenant promotion code (#159 item 3, promotions engine).

The mirror of the pricing engine: surge raises a fee under demand, a
coupon discounts it as a promotion. Codes are tenant-scoped (the same
code may exist in two tenants with different values) and enforced twice —
the explicit tenant filter in promotion_service keeps SQLite (the tests,
no RLS) correct, and Postgres RLS (migration 006) re-enforces the same
scope at the storage engine.

``times_used`` does NOT tick on quotes: a quote is a preview, so previews
cannot drain a redemption budget. It ticks when an order is actually
placed with the code, wired alongside the ordering surface (#159 item 1).
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Coupon(Base):
    __tablename__ = "coupons"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_coupons_tenant_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    # 'percent' (0 < value <= 100) or 'amount' (fee currency units)
    discount_kind: Mapped[str] = mapped_column(String(10))
    discount_value: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Coupon {self.tenant_id}:{self.code} {self.discount_kind} {self.discount_value}>"

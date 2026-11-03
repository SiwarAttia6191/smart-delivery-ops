"""MenuItem — a restaurant's sellable item (#159 item 4, restaurant portal).

The menu is the portal's CRUD surface and the storefront's browsing
surface. Tenant-scoped like every business row (9th RLS table, migration
008) and scoped a second time by the explicit tenant/restaurant filters in
the routes — SQLite (tests, no RLS) stays correct, Postgres enforces the
same scope at the engine.

V1 honesty: an order does NOT carry line items yet (no order_items model)
— the menu prices what the restaurant sells while the delivery fee prices
the ride. A cart with per-order line items is the documented next step.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MenuItem {self.id} r={self.restaurant_id} {self.name!r} {self.price}>"

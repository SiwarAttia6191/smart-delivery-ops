"""Immutable order event log — the raw feed for the Phase 2 data pipeline."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(30))  # created | assigned | status
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    order = relationship("Order", back_populates="events")

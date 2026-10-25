"""Immutable order event log — the raw feed for the Phase 2 data pipeline.

Event *content* is immutable. The one mutable column, ``published_at``, is
delivery metadata owned by the outbox relay: NULL means "not yet on the
Kafka bus", and it is only set after the broker has acknowledged delivery.
The relay claims due rows with FOR UPDATE SKIP LOCKED, so concurrent
transactions that commit out of id order (or a burned sequence value from a
rolled-back insert) can never create a silent gap.
"""

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
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )

    order = relationship("Order", back_populates="events")

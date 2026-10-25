"""Outbox dead letters — events the relay could not deliver, quarantined
instead of stalling the whole outbox behind them (head-of-line blocking).

A row here is an incident, not a silent drop: the source event remains in
``order_events`` with ``quarantined_at`` set (excluded from claiming), the
failure reason is preserved, and the outbox health gauge on /analytics/metrics
counts quarantined events so the failure is visible without opening logs.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RelayDeadLetter(Base):
    __tablename__ = "relay_dead_letters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        index=True
    )  # order_events.id, no FK: survives independently
    order_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(30))
    error: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

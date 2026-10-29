"""Tenant — the business-ownership dimension (#35 tenancy foundation).

One row per delivery company on this deployment. Today exactly one exists
(slug 'default', inserted by migration 002 and backfilled onto every
business row); the isolation layer (#36) adds JWT tenant claims and makes
the multiplicity real. ``city`` is a forward reference to the per-tenant
region binding (#78's manifests) — set when a second tenant actually
onboards.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(64))  # city-manifest name (#78)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tenant {self.slug}>"

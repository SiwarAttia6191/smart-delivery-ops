"""Customer OTP: the one-time codes that turn a phone number into a
customer-scoped JWT (#309).

The customers table carries the phone; this table carries the PROOF that
the holder of that phone answered a challenge. Codes are stored hashed
(SHA-256 — a leaked dump must not leak usable codes), single-use
(`used_at`), and short-lived (10 minutes at the service layer). Deliberately
tenant-scoped like every business row: the code is issued by one tenant's
channel and only verifies against that tenant's customers.

No password, no email dependency: the phone the customer gave at checkout
is the identity, the OTP is the proof, the JWT is the session.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CustomerOtp(Base):
    __tablename__ = "customer_otps"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CustomerOtp id={self.id} customer={self.customer_id}>"

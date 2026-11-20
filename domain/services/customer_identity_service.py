"""Customer identity (#309): the phone the customer gave at checkout is
the identity; the OTP is the proof; the JWT is the session.

Deliberately no password, no email dependency, no SMS provider in V1 —
the demo posture returns the code in the response (`dev_otp`) and says
so in api.md; a real deployment swaps this one function for an SMS send
without touching the routes. The anti-oracle tradeoff is documented
there too: issuing to an unknown phone is a 404 (the storefront is the
owner's own channel — the phone number was given to the operator's
business at checkout, not harvested from a public form).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.models import Customer, CustomerOtp

OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _code() -> str:
    # 6 digits from the crypto RNG — 1M combinations, single-use, 10
    # minutes, and the bucket caps guesses at 10/min: the math holds.
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_otp(db: Session, phone: str) -> tuple[Customer, str]:
    """Find the customer by phone, mint a single-use code, store it
    hashed, retire older unused codes for the same customer (one live
    code at a time). Returns (customer, code)."""
    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if customer is None:
        raise LookupError("customer_not_found")
    code = _code()
    now = datetime.now(UTC)
    # retire any earlier unused codes: one live challenge per customer
    for stale in db.scalars(
        select(CustomerOtp).where(
            CustomerOtp.customer_id == customer.id,
            CustomerOtp.used_at.is_(None),
        )
    ):
        stale.used_at = now
    db.add(
        CustomerOtp(
            tenant_id=customer.tenant_id,
            customer_id=customer.id,
            code_hash=_hash(code),
            expires_at=now + timedelta(seconds=OTP_TTL_SECONDS),
        )
    )
    db.commit()
    return customer, code


def verify_otp(db: Session, phone: str, code: str) -> Customer:
    """Verify the code against the customer's latest live challenge:
    unused, unexpired, under the attempt cap. Marks it used on success.
    Raises LookupError (no customer / no live challenge) or
    PermissionError (wrong code, expired, exhausted attempts)."""
    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if customer is None:
        raise LookupError("customer_not_found")
    otp = db.scalar(
        select(CustomerOtp)
        .where(
            CustomerOtp.customer_id == customer.id,
            CustomerOtp.used_at.is_(None),
        )
        .order_by(CustomerOtp.id.desc())
    )
    if otp is None:
        raise LookupError("no_live_challenge")
    now = datetime.now(UTC)
    # SQLite reads DateTime(timezone=True) back NAIVE — normalize before
    # comparing (the driver_event_service pattern).
    expires = otp.expires_at if otp.expires_at.tzinfo else otp.expires_at.replace(tzinfo=UTC)
    if expires < now:
        raise PermissionError("otp_expired")
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        raise PermissionError("otp_attempts_exhausted")
    otp.attempts += 1
    if otp.code_hash != _hash(code):
        db.commit()
        raise PermissionError("otp_invalid")
    otp.used_at = now
    db.commit()
    return customer


def otp_ttl_seconds() -> int:
    return OTP_TTL_SECONDS

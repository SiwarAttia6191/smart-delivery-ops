"""Payments — the checkout seam (#159 item 2).

The pricing engine produces the amount; this module settles it. The
provider is a deliberate SEAM: :class:`SandboxProvider` settles instantly
with a fake reference (test-mode gateway semantics — no money moves), and
``Settings.payment_provider`` selects the implementation, so wiring a real
gateway later is one config value plus one class with the same two-method
shape. Money movement, idempotency keys and webhook states are the
documented growth path; the sandbox keeps the demo honest by SAYING it is
a sandbox (every receipt is prefixed ``sbx_``).

Scoping is tenant-first, same rule as every other service: the explicit
``tenant_id`` filter keeps SQLite (tests, no RLS) correct and Postgres RLS
(migration 007, the eighth tenant table) re-enforces it at the engine.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from core.config import get_settings
from core.exceptions import InvalidTransitionError, NotFoundError, PaymentError
from domain.models import Order, OrderEvent, OrderStatus, Payment
from domain.services import tenant_service


@dataclass(frozen=True)
class ChargeResult:
    ref: str
    status: str


class PaymentProvider(Protocol):
    """The seam a real gateway implements — nothing else knows the vendor."""

    def charge(self, amount: float, reference: str) -> ChargeResult: ...


class SandboxProvider:
    """Test-mode gateway: instant, deterministic, unmistakably fake."""

    def charge(self, amount: float, reference: str) -> ChargeResult:
        if amount <= 0:
            raise PaymentError(f"sandbox provider refuses a {amount:.2f} charge")
        return ChargeResult(ref=f"sbx_{uuid4().hex[:12]}", status="paid")


def resolve_provider() -> PaymentProvider:
    name = get_settings().payment_provider
    if name == "sandbox":
        return SandboxProvider()
    raise ValueError(f"unknown payment provider: {name!r}")


def pay_order(session: Session, order_id: str) -> Payment:
    """Settle an order's delivery fee through the configured provider.

    Payable only while the order is ``pending`` — pay at checkout, then
    dispatch; a second charge is a 409, not a double payment.
    """
    tid = tenant_service.context_tenant_id(session)
    order = session.query(Order).filter(Order.id == order_id, Order.tenant_id == tid).one_or_none()
    if order is None:
        raise NotFoundError(f"order {order_id} not found")
    if order.status is not OrderStatus.pending:
        raise InvalidTransitionError(
            f"cannot pay an order in status '{order.status.value}' (only pending orders are payable)"
        )
    existing = session.query(Payment).filter(Payment.order_id == order.id).one_or_none()
    if existing is not None:
        raise InvalidTransitionError(f"order {order_id} is already paid ({existing.provider_ref})")

    charge = resolve_provider().charge(order.delivery_fee, reference=order.id)
    payment = Payment(
        tenant_id=order.tenant_id,
        order_id=order.id,
        amount=order.delivery_fee,
        status=charge.status,
        provider=get_settings().payment_provider,
        provider_ref=charge.ref,
    )
    session.add(payment)
    # The order's event log carries the settlement too — it is the feed the
    # warehouse consumes, so revenue analytics never need the ops tables.
    # provider_ref is the receipt key; the Payment row is joinable by order_id.
    session.add(
        OrderEvent(
            order=order,
            tenant_id=order.tenant_id,
            event_type="paid",
            payload=tenant_service.stamp_payload(
                {
                    "amount": payment.amount,
                    "provider": payment.provider,
                    "provider_ref": payment.provider_ref,
                },
                order.tenant_id,
            ),
        )
    )
    session.commit()
    session.refresh(payment)
    return payment


def list_payments(session: Session) -> list[Payment]:
    tid = tenant_service.context_tenant_id(session)
    return (
        session.query(Payment)
        .filter(Payment.tenant_id == tid)
        .order_by(Payment.created_at.desc())
        .all()
    )


def get_payment(session: Session, payment_id: int) -> Payment:
    tid = tenant_service.context_tenant_id(session)
    payment = (
        session.query(Payment)
        .filter(Payment.id == payment_id, Payment.tenant_id == tid)
        .one_or_none()
    )
    if payment is None:
        raise NotFoundError(f"payment {payment_id} not found")
    return payment

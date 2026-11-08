"""Payments — the checkout seam (#159 item 2).

The pricing engine produces the amount; this module settles it. The
provider is a deliberate SEAM: :class:`SandboxProvider` settles instantly
with a fake reference (test-mode gateway semantics — no money moves), and
:class:`StripeProvider` proves the seam is real with Stripe TEST-mode
PaymentIntents (#215 — real API calls, zero money moved, receipts are
``pi_…`` ids). ``Settings.payment_provider`` selects the implementation;
the sandbox stays one env away.

Card data never touches this server: the intent flow means the client
tokenizes their card with Stripe directly, and this side only ever sees
the intent id. Scoping is tenant-first, same rule as every other
service: the explicit ``tenant_id`` filter keeps SQLite (tests, no RLS)
correct and Postgres RLS (migration 007, the eighth tenant table)
re-enforces it at the engine.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

import httpx
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


class StripeProvider:
    """Stripe TEST-mode provider (#215): PaymentIntent create + confirm.

    Same two-step shape the sandbox fakes, with the real API: create an
    intent for the fee (amount in the smallest currency unit — Stripe's
    contract), confirm it with a test payment method, and the receipt is
    the ``pi_…`` intent id. Declines map to the existing taxonomy
    (PaymentError → 409); transport/5xx failures surface as PaymentError
    too — a failed charge is a failed charge, the caller retries.

    ``transport`` is the test seam: tests inject an httpx MockTransport
    so the suite is deterministic with zero network. TEST KEYS ONLY —
    the settings validator refuses the provider without a ``sk_test_…``
    style key, and no live key is ever accepted by convention.
    """

    API_BASE = "https://api.stripe.com/v1"
    # a Stripe TEST payment method id — the server never sees a card
    TEST_PAYMENT_METHOD = "pm_card_visa"

    def __init__(self, api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self._api_key = api_key or get_settings().stripe_api_key
        self._transport = transport

    def charge(self, amount: float, reference: str) -> ChargeResult:
        if amount <= 0:
            raise PaymentError(f"stripe provider refuses a {amount:.2f} charge")
        settings = get_settings()
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            with httpx.Client(
                base_url=self.API_BASE, headers=headers, transport=self._transport, timeout=15
            ) as client:
                create = client.post(
                    "/payment_intents",
                    data={
                        "amount": int(round(amount * 100)),  # smallest currency unit
                        "currency": settings.stripe_currency,
                        "metadata[order_id]": reference,
                    },
                )
                create.raise_for_status()
                intent_id = create.json()["id"]

                confirm = client.post(
                    f"/payment_intents/{intent_id}/confirm",
                    data={"payment_method": self.TEST_PAYMENT_METHOD},
                )
                confirm.raise_for_status()  # a decline is a 402 — mapped below
        except httpx.HTTPStatusError as exc:
            body = (
                exc.response.json()
                if exc.response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            decline = (body.get("error") or {}).get("decline_code") or (
                body.get("error") or {}
            ).get("code")
            raise PaymentError(
                f"stripe declined the charge ({decline or exc.response.status_code})"
            ) from exc
        except httpx.HTTPError as exc:
            raise PaymentError(f"stripe unreachable: {exc.__class__.__name__}") from exc

        confirmed = confirm.json()
        if confirmed.get("status") != "succeeded":
            raise PaymentError(f"stripe intent {intent_id} not settled ({confirmed.get('status')})")
        return ChargeResult(ref=intent_id, status="paid")


def resolve_provider() -> PaymentProvider:
    name = get_settings().payment_provider
    if name == "sandbox":
        return SandboxProvider()
    if name == "stripe":
        return StripeProvider()
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

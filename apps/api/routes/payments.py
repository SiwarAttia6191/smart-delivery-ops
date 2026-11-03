"""Payment endpoints (#159 item 2): settle an order's fee, list receipts.

The charge is a mutation on the ORDER (``POST /orders/{id}/pay`` — pay at
checkout, before dispatch) while receipts read under the payments prefix.
Same posture as the rest of the API: mutations hang off require_writer,
GETs stay open with tenant scoping (RLS on Postgres, explicit filters
everywhere). The sandbox provider answers instantly; a real gateway is a
config flip, not a route change.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.payments import PaymentOut
from domain.services import payment_service

# the charge acts on the ORDER (pay at checkout); receipts live under
# their own prefix — two routers, one module
router = APIRouter(tags=["payments"])
receipts = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "/orders/{order_id}/pay",
    response_model=PaymentOut,
    status_code=status.HTTP_201_CREATED,
)
def pay_order(
    order_id: str,
    db: Session = Depends(get_db),
    _writer: dict = Depends(require_writer),
) -> PaymentOut:
    """Settle the order's delivery fee via the configured provider
    (sandbox by default). Payable only while pending; a second charge 409s."""
    return PaymentOut.model_validate(payment_service.pay_order(db, order_id))


@receipts.get("", response_model=list[PaymentOut])
def list_payments(db: Session = Depends(get_db)) -> list[PaymentOut]:
    """The caller's tenant's payment receipts (anonymous callers see the
    default tenant's, like every open GET)."""
    return [PaymentOut.model_validate(p) for p in payment_service.list_payments(db)]


@receipts.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: int, db: Session = Depends(get_db)) -> PaymentOut:
    """One receipt (404 outside the caller's tenant)."""
    return PaymentOut.model_validate(payment_service.get_payment(db, payment_id))

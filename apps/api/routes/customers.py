"""Customer endpoints."""

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import (
    _oauth2_scheme,
    get_db,
    require_customer,
    require_writer,
)
from apps.api.schemas.customer import (
    CustomerCreate,
    CustomerOtpIssued,
    CustomerOtpRequest,
    CustomerOtpVerified,
    CustomerOtpVerifyRequest,
    CustomerResponse,
)
from core.exceptions import NotFoundError
from core.security import create_access_token
from domain.models import Customer, Order
from domain.services import customer_identity_service, tenant_service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writer)],
)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)) -> Customer:
    customer = Customer(
        tenant_id=tenant_service.context_tenant_id(db),
        name=payload.name,
        phone=payload.phone,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def _claims_or_none(token: str | None = Depends(_oauth2_scheme)) -> dict | None:
    """Soft identity: decode the bearer if present, else None. GETs stay
    open (demo posture #28) — this only decides PII visibility (#192)."""
    from core.security import decode_access_token

    if not token:
        return None
    try:
        return decode_access_token(token)
    except jwt.InvalidTokenError:
        return None


@router.get("", response_model=list[CustomerResponse])
def list_customers(
    db: Session = Depends(get_db), claims: dict | None = Depends(_claims_or_none)
) -> list[CustomerResponse]:
    rows = list(db.scalars(select(Customer).order_by(Customer.id)))
    if claims:
        return [CustomerResponse.model_validate(r) for r in rows]
    # anonymous: no phone (#192) — the storefront's visible name stays
    return [
        CustomerResponse(
            id=r.id,
            name=r.name,
            phone=None,
            latitude=r.latitude,
            longitude=r.longitude,
            loyalty_points=r.loyalty_points,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    claims: dict | None = Depends(_claims_or_none),
) -> CustomerResponse:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"customer {customer_id} not found")
    if claims:
        return CustomerResponse.model_validate(customer)
    return CustomerResponse(
        id=customer.id,
        name=customer.name,
        phone=None,
        latitude=customer.latitude,
        longitude=customer.longitude,
        loyalty_points=customer.loyalty_points,
        created_at=customer.created_at,
    )


@router.post(
    "/otp",
    response_model=CustomerOtpIssued,
    status_code=status.HTTP_201_CREATED,
)
def issue_customer_otp(payload: CustomerOtpRequest, db: Session = Depends(get_db)):
    """Mint the login challenge for a customer's phone (#309). Pre-auth by
    design — this IS the login. V1 demo posture: the code rides the
    response (`dev_otp`); a real deployment swaps the send, not the
    contract. The 404 oracle tradeoff: the storefront is the owner's own
    channel, the phone was given to that business at checkout."""
    try:
        customer, code = customer_identity_service.issue_otp(db, payload.phone)
    except LookupError:
        raise NotFoundError(f"no customer with phone {payload.phone}")
    return CustomerOtpIssued(
        sent=True,
        expires_in=customer_identity_service.otp_ttl_seconds(),
        dev_otp=code,
    )


@router.post("/otp/verify", response_model=CustomerOtpVerified)
def verify_customer_otp(payload: CustomerOtpVerifyRequest, db: Session = Depends(get_db)):
    """Verify the code, spend it, and issue the customer-scoped JWT: role
    `customer` + the `cid` claim + the tenant binding — the same pinning
    the staff tokens ride, scoped to exactly one customer's rows."""
    try:
        customer = customer_identity_service.verify_otp(db, payload.phone, payload.code)
    except LookupError as exc:
        raise NotFoundError(str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    tenant = tenant_service.resolve_default(db)
    token = create_access_token(
        username=f"customer:{customer.id}",
        role="customer",
        tenant_id=customer.tenant_id,
        tenant_slug=tenant.slug if tenant else None,
        customer_id=customer.id,
    )
    return CustomerOtpVerified(
        access_token=token,
        customer=CustomerResponse.model_validate(customer),
    )


@router.get("/me/orders", response_model=list[dict])
def my_orders(claims: dict = Depends(require_customer), db: Session = Depends(get_db)):
    """The customer's own orders, newest first, WITH the event log — the
    same payload the tracking page renders. Customer-scoped by the cid
    claim, tenant-scoped by RLS (#309)."""
    orders = db.scalars(
        select(Order)
        .where(Order.customer_id == int(claims["cid"]))
        .order_by(Order.created_at.desc())
        .limit(100)
    ).all()
    result = []
    for order in orders:
        result.append(
            {
                "id": order.id,
                "status": order.status.value if hasattr(order.status, "value") else order.status,
                "restaurant_id": order.restaurant_id,
                "delivery_fee": order.delivery_fee,
                "eta_minutes": order.eta_minutes,
                "distance_km": order.distance_km,
                "created_at": order.created_at,
                "events": [
                    {
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "created_at": event.created_at,
                    }
                    for event in order.events
                ],
            }
        )
    return result

"""Customer endpoints."""

import jwt
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import _oauth2_scheme, get_db, require_writer
from apps.api.schemas.customer import CustomerCreate, CustomerResponse
from core.exceptions import NotFoundError
from domain.models import Customer
from domain.services import tenant_service

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
        created_at=customer.created_at,
    )

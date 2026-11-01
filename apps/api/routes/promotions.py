"""Coupon management endpoints (#159 item 3): per-tenant promotion codes.

Same auth posture as the rest of the API: GET stays open (demo posture
#28, RLS still scopes the rows), mutations hang off require_writer. A
code is unique per tenant — two tenants may both run "WELCOME10" with
different values; every lookup and mutation filters on the caller's
tenant (RLS re-enforces it on Postgres), so cross-tenant use looks
identical to a code that does not exist.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.promotions import CouponCreate, CouponOut
from core.exceptions import NotFoundError
from domain.models import Coupon
from domain.services import tenant_service

router = APIRouter(prefix="/coupons", tags=["pricing"])


@router.post("", response_model=CouponOut, status_code=status.HTTP_201_CREATED)
def create_coupon(
    payload: CouponCreate,
    db: Session = Depends(get_db),
    _writer: dict = Depends(require_writer),
) -> CouponOut:
    """Create a promotion code for the caller's tenant (writer)."""
    coupon = Coupon(
        tenant_id=tenant_service.context_tenant_id(db),
        code=payload.code,
        discount_kind=payload.discount_kind,
        discount_value=payload.discount_value,
        expires_at=payload.expires_at,
        max_redemptions=payload.max_redemptions,
    )
    db.add(coupon)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"coupon code {payload.code!r} already exists for this tenant",
        ) from exc
    db.refresh(coupon)
    return CouponOut.model_validate(coupon)


@router.get("", response_model=list[CouponOut])
def list_coupons(db: Session = Depends(get_db)) -> list[CouponOut]:
    """The caller's tenant's coupons (anonymous callers see the default
    tenant's, like every open GET)."""
    tid = tenant_service.context_tenant_id(db)
    coupons = db.query(Coupon).filter(Coupon.tenant_id == tid).order_by(Coupon.created_at).all()
    return [CouponOut.model_validate(c) for c in coupons]


@router.delete("/{coupon_id}", response_model=CouponOut)
def deactivate_coupon(
    coupon_id: int,
    db: Session = Depends(get_db),
    _writer: dict = Depends(require_writer),
) -> CouponOut:
    """Deactivate a coupon (soft — history keeps the code for auditing)."""
    tid = tenant_service.context_tenant_id(db)
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id, Coupon.tenant_id == tid).one_or_none()
    if coupon is None:
        raise NotFoundError(f"coupon {coupon_id} not found")
    coupon.is_active = False
    db.commit()
    db.refresh(coupon)
    return CouponOut.model_validate(coupon)

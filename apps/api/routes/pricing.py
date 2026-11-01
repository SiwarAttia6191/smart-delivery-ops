"""Pricing quote + per-tenant configuration endpoints (#36)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.pricing import QuoteRequest, QuoteResponse
from core.exceptions import NotFoundError
from domain.models import Restaurant, Tenant
from domain.services import demand_service, pricing_service, promotion_service, tenant_service

router = APIRouter(prefix="/pricing", tags=["pricing"])


# POST by shape but a pure READ: computes a fee, mutates nothing — open
# under the demo auth posture (#28), like every GET. When the caller is
# authenticated, the fee runs on THEIR tenant's pricing overrides (#36).
@router.post("/quote", response_model=QuoteResponse)
def quote(payload: QuoteRequest, db: Session = Depends(get_db)) -> QuoteResponse:
    """Quote a delivery fee before an order is placed."""
    if payload.restaurant_id is not None:
        restaurant = db.get(Restaurant, payload.restaurant_id)
        if restaurant is None:
            raise NotFoundError(f"restaurant {payload.restaurant_id} not found")
        pickup_lat, pickup_lon = restaurant.latitude, restaurant.longitude
    else:
        # exactly one of restaurant_id / pickup coordinates is set (schema validator)
        assert payload.pickup_latitude is not None and payload.pickup_longitude is not None
        pickup_lat = payload.pickup_latitude
        pickup_lon = payload.pickup_longitude

    demand_ratio = payload.demand_ratio
    if demand_ratio is None:
        demand_ratio = demand_service.derive_demand_ratio(
            db, payload.dropoff_latitude, payload.dropoff_longitude
        )
    quote = pricing_service.quote_delivery(
        pickup_lat,
        pickup_lon,
        payload.dropoff_latitude,
        payload.dropoff_longitude,
        demand_ratio,
        settings=tenant_service.effective_settings(db),
    )
    # promotions (#159 item 3): an accepted coupon becomes an auditable
    # discount line. A quote is a PREVIEW — times_used does not tick here.
    discount = 0.0
    applied_code = None
    if payload.coupon_code:
        coupon = promotion_service.active_coupon(db, payload.coupon_code)
        if coupon is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail="coupon code is not valid"
            )
        discount = promotion_service.apply_discount(coupon, quote.breakdown.total)
        applied_code = coupon.code
    return QuoteResponse(
        distance_km=quote.distance_km,
        base_fee=quote.breakdown.base_fee,
        distance_fee=quote.breakdown.distance_fee,
        surge_multiplier=quote.breakdown.surge_multiplier,
        total=quote.breakdown.total,
        discount=discount,
        coupon_code=applied_code,
        final_total=round(quote.breakdown.total - discount, 2) if applied_code else None,
    )


class PricingConfig(BaseModel):
    """The deployment's pricing knobs and which a tenant may override."""

    base_fee: float
    per_km: float
    min_fee: float
    surge_tiers: list[dict[str, float]]


class PricingOverrideRequest(BaseModel):
    overrides: dict[str, float]


@router.get("/config", response_model=PricingConfig)
def get_config(db: Session = Depends(get_db)) -> PricingConfig:
    """The pricing configuration in effect for the caller's tenant (#36):
    deployment defaults, or defaults with the tenant's overrides merged."""
    s = tenant_service.effective_settings(db)
    return PricingConfig(
        base_fee=s.pricing_base_fee,
        per_km=s.pricing_per_km,
        min_fee=s.pricing_min_fee,
        surge_tiers=[
            {
                "tier": 1,
                "ratio": s.pricing_surge_tier1_ratio,
                "multiplier": s.pricing_surge_tier1_multiplier,
            },
            {
                "tier": 2,
                "ratio": s.pricing_surge_tier2_ratio,
                "multiplier": s.pricing_surge_tier2_multiplier,
            },
            {
                "tier": 3,
                "ratio": s.pricing_surge_tier3_ratio,
                "multiplier": s.pricing_surge_tier3_multiplier,
            },
        ],
    )


@router.put("/config", response_model=PricingConfig)
def put_config(
    payload: PricingOverrideRequest,
    db: Session = Depends(get_db),
    _writer: dict = Depends(require_writer),
) -> PricingConfig:
    """Set the caller's tenant's pricing overrides (writer). Unknown keys
    are rejected — overrides are confined to the pricing knobs."""
    tid = tenant_service.context_tenant_id(db)
    tenant = db.get(Tenant, tid)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"tenant {tid} not found")
    try:
        tenant_service.set_overrides(db, tenant, payload.overrides)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return get_config(db)

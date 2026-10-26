"""Pricing quote endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.deps import get_db
from apps.api.schemas.pricing import QuoteRequest, QuoteResponse
from core.exceptions import NotFoundError
from domain.models import Restaurant
from domain.services import demand_service, pricing_service

router = APIRouter(prefix="/pricing", tags=["pricing"])


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
    )
    return QuoteResponse(
        distance_km=quote.distance_km,
        base_fee=quote.breakdown.base_fee,
        distance_fee=quote.breakdown.distance_fee,
        surge_multiplier=quote.breakdown.surge_multiplier,
        total=quote.breakdown.total,
    )

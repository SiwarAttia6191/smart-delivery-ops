"""Pricing quote schemas."""

from pydantic import BaseModel, Field, model_validator


class QuoteRequest(BaseModel):
    restaurant_id: int | None = None
    pickup_latitude: float | None = Field(None, ge=-90, le=90)
    pickup_longitude: float | None = Field(None, ge=-180, le=180)
    dropoff_latitude: float = Field(..., ge=-90, le=90)
    dropoff_longitude: float = Field(..., ge=-180, le=180)
    demand_ratio: float | None = Field(None, ge=0)  # None = derive from the forecast table
    coupon_code: str | None = Field(None, max_length=60)  # promotions (#159 item 3)

    @model_validator(mode="after")
    def check_pickup(self) -> "QuoteRequest":
        has_restaurant = self.restaurant_id is not None
        has_coords = self.pickup_latitude is not None and self.pickup_longitude is not None
        if has_restaurant == has_coords:
            raise ValueError("provide exactly one of restaurant_id or pickup coordinates")
        return self


class QuoteResponse(BaseModel):
    distance_km: float
    base_fee: float
    distance_fee: float
    surge_multiplier: float
    total: float
    # present when a coupon_code was supplied and accepted; total above is
    # the fee BEFORE the discount, final_total is what the order would pay
    discount: float = 0.0
    coupon_code: str | None = None
    final_total: float | None = None

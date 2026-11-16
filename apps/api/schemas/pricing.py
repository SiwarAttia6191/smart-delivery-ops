"""Pricing quote schemas."""

from pydantic import BaseModel, Field, model_validator


class QuoteRequest(BaseModel):
    restaurant_id: int | None = None
    pickup_latitude: float | None = Field(None, ge=-90, le=90)
    pickup_longitude: float | None = Field(None, ge=-180, le=180)
    # dropoff: address text OR coordinates (#232) — the geocoder resolves
    # a typed address (and caches it) before any pricing math
    dropoff_latitude: float | None = Field(None, ge=-90, le=90)
    dropoff_longitude: float | None = Field(None, ge=-180, le=180)
    dropoff_address: str | None = Field(None, max_length=300)
    demand_ratio: float | None = Field(None, ge=0)  # None = derive from the forecast table
    coupon_code: str | None = Field(None, max_length=60)  # promotions (#159 item 3)

    @model_validator(mode="after")
    def check_pickup(self) -> "QuoteRequest":
        has_restaurant = self.restaurant_id is not None
        has_coords = self.pickup_latitude is not None and self.pickup_longitude is not None
        if has_restaurant == has_coords:
            raise ValueError("provide exactly one of restaurant_id or pickup coordinates")
        return self

    @model_validator(mode="after")
    def check_dropoff(self) -> "QuoteRequest":
        has_coords = self.dropoff_latitude is not None and self.dropoff_longitude is not None
        if has_coords == (self.dropoff_address is not None):
            raise ValueError("provide exactly one of dropoff coordinates or dropoff_address")
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
    # the geocoder's display name when the quote was priced by address
    # (#232) — the customer sees what their text resolved to
    dropoff_resolved: str | None = None
    # the self-deliverer's fuel estimate (user idea): liters, cost and CO2
    # for the deployment's dominant vehicle — absent when the manifest
    # declares no fuel economics
    fuel_estimate: dict | None = None

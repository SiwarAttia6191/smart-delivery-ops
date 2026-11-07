"""Order schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from apps.api.schemas.common import ORMModel
from domain.models import OrderStatus


class OrderCreate(BaseModel):
    customer_id: int
    restaurant_id: int
    dropoff_latitude: float | None = Field(None, ge=-90, le=90)
    dropoff_longitude: float | None = Field(None, ge=-180, le=180)
    dropoff_address: str | None = Field(
        None,
        max_length=300,
        description=(
            "Address text — geocoded to the dropoff coordinates (#221 "
            "follow-up). Provide address OR coordinates, not both."
        ),
    )
    demand_ratio: float | None = Field(
        None,
        ge=0,
        description=(
            "Current demand / expected demand for the dropoff zone+hour. Omit to "
            "derive it from the demand_forecasts table (observed orders vs the "
            "forecast for the elapsed part of the hour); explicit values are "
            "honored for demos and tests."
        ),
    )
    source: str | None = Field(
        None,
        max_length=30,
        description=(
            "Order-source attribution (#226): storefront / marketplace / phone "
            "— rides the created event so the marts can split revenue per "
            "channel (the data a restaurant needs to decide which orders to "
            "keep on-platform)."
        ),
    )

    @model_validator(mode="after")
    def check_dropoff(self) -> "OrderCreate":
        has_coords = self.dropoff_latitude is not None and self.dropoff_longitude is not None
        if has_coords == (self.dropoff_address is not None):
            raise ValueError("provide exactly one of dropoff coordinates or dropoff_address")
        return self

    coupon_code: str | None = Field(
        None,
        max_length=60,
        description=(
            "Promotion code to redeem at checkout (#159 item 3): an accepted "
            "code discounts the fee and ticks its redemption counter — a quote "
            "never does. Rejections are one uniform 422."
        ),
    )


class OrderStatusUpdate(BaseModel):
    status: OrderStatus

    @field_validator("status")
    @classmethod
    def reject_assigned_via_status(cls, value: OrderStatus) -> OrderStatus:
        if value is OrderStatus.assigned:
            raise ValueError("use POST /orders/{id}/assign to assign a driver instead")
        return value


class OrderEventResponse(ORMModel):
    id: int
    event_type: str
    payload: dict
    created_at: datetime


class OrderResponse(ORMModel):
    id: str
    status: OrderStatus
    customer_id: int
    restaurant_id: int
    driver_id: int | None
    pickup_latitude: float
    pickup_longitude: float
    dropoff_latitude: float
    dropoff_longitude: float
    distance_km: float
    delivery_fee: float
    surge_multiplier: float
    eta_minutes: int
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None
    delivery_minutes: float | None = None
    events: list[OrderEventResponse] = []

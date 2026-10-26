"""Order schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from apps.api.schemas.common import ORMModel
from domain.models import OrderStatus


class OrderCreate(BaseModel):
    customer_id: int
    restaurant_id: int
    dropoff_latitude: float = Field(..., ge=-90, le=90)
    dropoff_longitude: float = Field(..., ge=-180, le=180)
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

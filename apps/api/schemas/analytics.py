"""Analytics schemas."""

from pydantic import BaseModel


class AnalyticsSummary(BaseModel):
    orders_total: int
    orders_by_status: dict[str, int]
    delivered_revenue: float
    average_fee: float
    average_delivery_minutes: float | None
    drivers_available: int
    drivers_total: int
    restaurants_active: int

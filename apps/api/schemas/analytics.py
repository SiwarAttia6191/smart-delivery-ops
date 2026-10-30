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


class TenantBreakdown(AnalyticsSummary):
    """Per-tenant slice of the same summary (#36)."""

    tenant_id: int
    tenant_slug: str
    tenant_name: str

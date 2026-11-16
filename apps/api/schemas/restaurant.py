"""Restaurant schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from apps.api.schemas.common import LatLonMixin, ORMModel


class RestaurantCreate(LatLonMixin):
    name: str = Field(..., min_length=1, max_length=120)
    is_active: bool = True


class RestaurantResponse(ORMModel):
    id: int
    name: str
    latitude: float
    longitude: float
    is_active: bool
    created_at: datetime


class SalesSnapshot(BaseModel):
    """The portal's own numbers for the business day (#219). A live
    ops-slice per request — deliberately NOT a warehouse mart."""

    restaurant_id: int
    business_date: str
    delivered_orders: int
    revenue: float
    avg_delivery_minutes: float | None = None
    rating_average: float | None = None  # rides null until #216 lands
    # the delivered-day fuel actuals (#274): liters/cost/CO2 summed over the
    # day's deliveries via each assigned driver's vehicle — absent when the
    # city manifest declares no fuel economics
    fuel: dict | None = None


class WeeklyDayRow(BaseModel):
    date: str
    orders: int
    revenue: float
    avg_delivery_minutes: float | None = None


class WeeklyReport(BaseModel):
    """The Monday-morning artifact (#275): the last N business days for
    one restaurant — per-day rows plus the totals, from the live tables."""

    restaurant_id: int
    period_start: str
    period_end: str
    days_requested: int
    delivered_orders: int
    revenue: float
    avg_delivery_minutes: float | None = None
    fuel: dict | None = None
    rating_average: float | None = None
    per_day: list[WeeklyDayRow]

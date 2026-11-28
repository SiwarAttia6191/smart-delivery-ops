"""Restaurant schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from apps.api.schemas.common import LatLonMixin, ORMModel


class RestaurantCreate(LatLonMixin):
    name: str = Field(..., min_length=1, max_length=120)
    is_active: bool = True


class RestaurantResponse(ORMModel):
    id: int
    name: str
    latitude: float
    longitude: float
    # the pickup identity (#358): the typed address is the label, the
    # coordinates are the truth, and pin_source records who vouched -
    # imported / geocoded / owner_verified (NULL = pre-#358 row)
    address: str | None = None
    pin_source: str | None = None
    is_active: bool
    created_at: datetime


class PickupPointUpdate(BaseModel):
    """The owner sets their door (#358). Coordinates alone = the dragged
    pin (owner_verified - the geocoder is never consulted against the
    owner's hand). Address alone = geocoded + self-verified. BOTH = the
    dragged coordinates are the truth, the typed text rides along as the
    label - the same contract the customer checkout uses."""

    pickup_latitude: float | None = Field(None, ge=-90, le=90)
    pickup_longitude: float | None = Field(None, ge=-180, le=180)
    address: str | None = Field(None, max_length=300)

    @model_validator(mode="after")
    def check_pickup(self) -> "PickupPointUpdate":
        has_coords = self.pickup_latitude is not None and self.pickup_longitude is not None
        if not has_coords and self.address is None:
            raise ValueError("provide pickup coordinates, an address, or both")
        if has_coords != (self.pickup_longitude is not None):
            raise ValueError("pickup coordinates come as a pair")
        return self


class PickupPointResponse(BaseModel):
    """What the door now is, with the verification verdict attached - the
    portal badges it exactly like the customer's checkout."""

    latitude: float
    longitude: float
    address: str | None = None
    pin_source: str
    precision: str


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

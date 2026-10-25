"""Restaurant schemas."""

from datetime import datetime

from pydantic import Field

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

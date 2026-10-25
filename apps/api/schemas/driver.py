"""Driver schemas."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from apps.api.schemas.common import LatLonMixin, ORMModel


class DriverCreate(LatLonMixin):
    name: str = Field(..., min_length=1, max_length=120)
    vehicle: Literal["bicycle", "motorcycle", "car"] = "motorcycle"


class DriverUpdate(LatLonMixin):
    is_available: bool


class DriverResponse(ORMModel):
    id: int
    name: str
    vehicle: str
    latitude: float
    longitude: float
    is_available: bool
    created_at: datetime

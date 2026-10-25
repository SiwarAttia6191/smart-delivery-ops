"""Customer schemas."""

from datetime import datetime

from pydantic import Field

from apps.api.schemas.common import LatLonMixin, ORMModel


class CustomerCreate(LatLonMixin):
    name: str = Field(..., min_length=1, max_length=120)
    phone: str | None = Field(None, max_length=30)


class CustomerResponse(ORMModel):
    id: int
    name: str
    phone: str | None
    latitude: float
    longitude: float
    created_at: datetime

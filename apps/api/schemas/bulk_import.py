"""Bulk import schemas (#223, Onboarding R1).

Rows mirror the client's world: plain fields + an ADDRESS — coordinates
are never requested; the geocoder turns their text into ours. The report
is per-row and explicit: what imported, what failed and WHY, what each
address resolved to.
"""

from typing import Literal

from pydantic import BaseModel, Field


class RestaurantRow(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    address: str = Field(..., min_length=1, max_length=300)
    is_active: bool = True


class DriverRow(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    vehicle: Literal["bicycle", "motorcycle", "car"] = "motorcycle"
    address: str = Field(..., min_length=1, max_length=300)


class CustomerRow(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    phone: str | None = Field(None, max_length=30)
    address: str = Field(..., min_length=1, max_length=300)


class MenuItemRow(BaseModel):
    restaurant_id: int
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    price: float = Field(..., gt=0)
    is_available: bool = True


# entity path segment → row model; geo entities geocode `address`
ROW_MODELS: dict[str, type[BaseModel]] = {
    "restaurants": RestaurantRow,
    "drivers": DriverRow,
    "customers": CustomerRow,
    "menu-items": MenuItemRow,
}
GEO_ENTITIES = {"restaurants", "drivers", "customers"}


class RowReport(BaseModel):
    """One line of the per-row import report."""

    row: int  # 1-based position in the submitted file
    status: Literal["imported", "failed"]
    detail: str | None = None  # the failure reason, row-specific
    resolved_address: str | None = None  # geocoder display name (geo rows)
    latitude: float | None = None
    longitude: float | None = None
    cached: bool | None = None  # did the geocoder serve from the cache


class ImportResult(BaseModel):
    entity: str
    imported: int
    report: list[RowReport]

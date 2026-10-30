"""Tenant schemas (#36)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TenantCreate(BaseModel):
    slug: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
        description="URL-safe unique identifier, e.g. 'acme-express'",
    )
    name: str = Field(min_length=1, max_length=120)
    city: str | None = Field(
        default=None,
        max_length=64,
        description="city-manifest name (#78) when the tenant runs its own region",
    )


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    city: str | None
    pricing_overrides: dict | None
    created_at: datetime

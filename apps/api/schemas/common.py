"""Shared schema building blocks."""

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    """Base for schemas serialized from SQLAlchemy models."""

    model_config = ConfigDict(from_attributes=True)


class LatLonMixin(BaseModel):
    latitude: float = Field(..., ge=-90, le=90, description="Latitude in decimal degrees")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude in decimal degrees")

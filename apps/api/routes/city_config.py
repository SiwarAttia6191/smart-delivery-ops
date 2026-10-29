"""City/region configuration for demo surfaces (#78 region pack)."""

from fastapi import APIRouter
from pydantic import BaseModel

from core.config import get_settings
from domain.city_config import load_city_config

router = APIRouter(tags=["config"])


class MapViewOut(BaseModel):
    center: list[float]
    zoom: int


class CityConfigOut(BaseModel):
    city: str
    label: str
    business_timezone: str
    map: MapViewOut


@router.get("/config", response_model=CityConfigOut)
def get_city_config() -> CityConfigOut:
    """The deployment's city manifest: label, business timezone, map view.

    The landing page reads this so the demo surfaces follow whatever city
    the API actually serves (one deployment = one city).
    """
    config = load_city_config(get_settings().city)
    return CityConfigOut(
        city=config.city,
        label=config.label,
        business_timezone=config.business_timezone,
        map=MapViewOut(center=[config.map.center_lat, config.map.center_lon], zoom=config.map.zoom),
    )

"""City manifests: the one file describing a deployment's region (#78).

One deployment = one city = one business timezone (simultaneous
multi-region is multi-tenant territory, #35/#36). A manifest carries
everything city-specific in one place: seed coordinates (restaurants,
drivers, customers), the synthetic benchmark grid, the demo-page map
view, the OSRM extract to download, and the business timezone every
local grain must derive in.

Scripts load the manifest; the deployment pins it with ``CITY`` and
``BUSINESS_TIMEZONE`` env vars. The seed/backfill scripts call
:func:`ensure_business_timezone` and refuse to run when the manifest and
the environment disagree — the environment's zone is what dbt grains and
ML features actually derive in, so a silent mismatch would mis-grain
every row the pipeline ever writes.
"""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator

CITY_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "cities"


class RestaurantSeed(BaseModel):
    name: str
    latitude: float
    longitude: float


class DriverSeed(BaseModel):
    name: str
    vehicle: str
    latitude: float
    longitude: float


class CustomerSeed(BaseModel):
    name: str
    phone: str
    latitude: float
    longitude: float


class MapView(BaseModel):
    center_lat: float
    center_lon: float
    zoom: int = Field(ge=1, le=19)


class BenchmarkGrid(BaseModel):
    center_lat: float
    center_lon: float
    spread_deg: float


class BackfillProfile(BaseModel):
    # Orders per hour at weight 1.0; the hour-of-day shape itself is the
    # universal food-delivery curve (HOUR_WEIGHTS in the backfill script).
    base_rate: float


class OsrmExtract(BaseModel):
    # Local file convention: <extract_id>-latest.osm.pbf inside the data
    # dir, exactly how Geofabrik names its downloads.
    extract_id: str
    download_url: str


class CityConfig(BaseModel):
    city: str
    label: str
    business_timezone: str
    map: MapView
    benchmark_grid: BenchmarkGrid
    backfill: BackfillProfile
    osrm: OsrmExtract
    restaurants: list[RestaurantSeed]
    drivers: list[DriverSeed]
    customers: list[CustomerSeed]

    @field_validator("business_timezone")
    @classmethod
    def _valid_iana_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:  # noqa: BLE001 - KeyError variants from zoneinfo
            raise ValueError(f"'{value}' is not a valid IANA timezone") from exc
        return value


def available_cities(directory: Path = CITY_CONFIG_DIR) -> list[str]:
    return sorted(path.stem for path in directory.glob("*.json"))


def load_city_config(name: str, directory: Path = CITY_CONFIG_DIR) -> CityConfig:
    path = directory / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"Unknown city manifest '{name}' - available: {', '.join(available_cities(directory))}"
        )
    try:
        config = CityConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ValueError(f"Invalid city manifest {path.name}: {exc}") from exc
    if config.city != name:
        raise ValueError(
            f"Manifest {path.name} declares city '{config.city}' - the file name must match."
        )
    return config


def ensure_business_timezone(config: CityConfig, environment_timezone: str) -> None:
    """Refuse city writes when manifest and environment zones disagree."""
    if config.business_timezone != environment_timezone:
        raise SystemExit(
            f"Timezone mismatch: city '{config.city}' manifest declares "
            f"{config.business_timezone} but BUSINESS_TIMEZONE={environment_timezone}. "
            "Export BUSINESS_TIMEZONE with the manifest value - dbt grains and ML "
            "features derive in the environment's zone, and a mismatch would "
            "silently mis-grain every row."
        )


def load_city_manifest_json(name: str, directory: Path = CITY_CONFIG_DIR) -> dict:
    """Raw manifest dict - helper for tests."""
    return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))

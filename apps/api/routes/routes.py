"""Saved route geometry endpoints."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(prefix="/routes", tags=["routes"])
DEMO_ROUTE_PATH = Path(__file__).resolve().parents[3] / "docs" / "route.geojson"


@router.get("/demo", response_class=FileResponse)
def get_demo_route() -> FileResponse:
    """Return the saved demo route snapshot as GeoJSON (regenerated per city)."""
    return FileResponse(DEMO_ROUTE_PATH, media_type="application/geo+json")

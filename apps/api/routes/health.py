"""Health and readiness endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.deps import get_db
from core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    settings: Settings = get_settings()
    database = "up"
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - health endpoint reports, never raises
        database = "down"
    return {
        "status": "ok" if database == "up" else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "database": database,
    }

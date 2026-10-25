"""FastAPI application factory.

Run locally (repo root):
    uvicorn apps.api.main:app --reload --port 8000
Interactive docs: http://127.0.0.1:8000/docs  (Swagger)  /  /redoc
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.routes import analytics, customers, drivers, health, orders, pricing, restaurants
from core.config import get_settings
from core.database import create_all
from core.exceptions import DomainError
from core.logging import configure_logging

DESCRIPTION = """
Operational API for a delivery startup: **orders**, **drivers**, **dynamic
pricing**, **dispatch**, and **analytics** — the data backbone of the
AI-Powered Smart Delivery Operations Platform.

Phase 1 scope: order lifecycle, nearest-driver matching, distance + surge
pricing, and operational KPIs. Forecasting, the event pipeline, and ML-driven
ETA/pricing land in Phases 2–3 (see docs/roadmap.md).
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    create_all()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tighten per-environment in Phase 5
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.error_code, "message": str(exc)}},
        )

    for router in (
        health.router,
        customers.router,
        restaurants.router,
        drivers.router,
        orders.router,
        pricing.router,
        analytics.router,
    ):
        app.include_router(router)
    return app


app = create_app()

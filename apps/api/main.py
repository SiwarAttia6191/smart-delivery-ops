"""FastAPI application factory.

Run locally (repo root):
    uvicorn apps.api.main:app --reload --port 8000
Interactive docs: http://127.0.0.1:8000/docs  (Swagger)  /  /redoc
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.api.routes import (
    analytics,
    auth,
    city_config,
    copilot,
    customers,
    drivers,
    health,
    imports,
    knowledge,
    menu,
    onboarding,
    orders,
    payments,
    pricing,
    promotions,
    restaurants,
    routes,
    tenants,
)
from apps.api.tenancy_middleware import TenantScopeMiddleware
from core.config import get_settings
from core.database import run_migrations
from core.exceptions import DomainError
from core.logging import configure_logging
from domain.services import eta_model_service

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
    # Load-test finding (#103): sync endpoints run in anyio's threadpool,
    # whose default capacity is 40 tokens - the 300-VU spike queued 260
    # requests forever behind it. 100 tokens matches the connection pool's
    # capacity so threads wait on the pool, not on a smaller proxy limit.
    from anyio import to_thread

    to_thread.current_default_thread_limiter().total_tokens = 100
    # The ops schema is owned by the Alembic chain (#28): apply pending
    # migrations at boot. In compose the `migrate` service has already
    # brought the DB to head before this runs — idempotent no-op here.
    run_migrations()
    if get_settings().eta_model_source == "registry":
        eta_model_service.load_from_registry()
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
    # Tenant scoping (#36): pin the JWT's tid claim for the request so
    # writes resolve their owner and Postgres RLS filters reads/writes.
    app.add_middleware(TenantScopeMiddleware)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        body: dict = {"code": exc.error_code, "message": str(exc)}
        if exc.details is not None:
            # structured payload (e.g. the per-row bulk-import report, #223)
            body["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content={"error": body})

    for router in (
        health.router,
        auth.router,
        menu.router,
        customers.router,
        restaurants.router,
        drivers.router,
        imports.router,
        onboarding.router,
        orders.router,
        payments.router,
        payments.receipts,
        pricing.router,
        promotions.router,
        analytics.router,
        routes.router,
        tenants.router,
        copilot.router,
        knowledge.router,
        city_config.router,
    ):
        app.include_router(router)

    # The ordering storefront served BY the API (#159 item 1): same-origin
    # for its fetch calls, stylesheet included, no separate static server —
    # any clone with the stack up gets it at /storefront/ordering-demo.html.
    # GitHub Pages also serves the file, with an offline banner when the API
    # is away. The image whitelists only the RAG corpus files from docs/
    # (.dockerignore), so the demo page and its stylesheet are added there.
    docs_dir = Path(__file__).resolve().parents[2] / "docs"
    app.mount("/storefront", StaticFiles(directory=str(docs_dir)), name="storefront")

    return app


app = create_app()

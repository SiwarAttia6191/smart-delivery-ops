"""FastAPI application factory.

Run locally (repo root):
    uvicorn apps.api.main:app --reload --port 8000
Interactive docs: http://127.0.0.1:8000/docs  (Swagger)  /  /redoc
"""

from contextlib import asynccontextmanager
from pathlib import Path

import json
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
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
        # comma-separated CORS_ORIGINS (bug-hunt #260: the demo compose
        # promised "no wildcard CORS on a TLS box" — the env now has a
        # consumer); "*" keeps the local demo posture
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
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

    # The front door (#258): the API's surfaces live under /storefront/* and
    # /docs, so a bare host used to return a blank 404 — the last member of
    # the hardcoded-host/missing-door class the #219 live drive exposed.
    # A bare host now greets with the demos hub, on every deployment.
    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/storefront/demos.html")

    # Crash visibility (#194, wired by bug-hunt #260): on the demo/VPS
    # deployment ALERT_WEBHOOK is the ONLY alert path (no Prometheus there).
    # Fire-and-forget with a short timeout — a webhook outage must never
    # add latency or break the error response itself.
    @app.exception_handler(Exception)
    async def unhandled_with_webhook(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        webhook = settings.alert_webhook
        if webhook:
            payload = json.dumps(
                {"text": f"500 {request.url.path}: {type(exc).__name__}: {exc}"[:300]}
            ).encode("utf-8")
            try:
                import urllib.request

                webhook_request = urllib.request.Request(
                    webhook, data=payload, headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(webhook_request, timeout=2)
            except Exception:  # noqa: BLE001 - the alert path never breaks the API
                logger.warning("alert webhook unreachable")
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    return app


logger = logging.getLogger(__name__)

app = create_app()

"""Analytics endpoints — Phase 1 operational summary + Prometheus text metrics."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import get_db
from apps.api.schemas.analytics import AnalyticsSummary, TenantBreakdown
from core.tenancy import reset_request_tenant, set_request_tenant
from domain.models import (
    Driver,
    DriverEvent,
    DriverRelayDeadLetter,
    Order,
    OrderEvent,
    OrderStatus,
    RelayDeadLetter,
    Restaurant,
    RouteCache,
    Tenant,
)
from domain.services import eta_model_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _summary(db: Session) -> AnalyticsSummary:
    total = db.scalar(select(func.count(Order.id))) or 0
    raw_counts = dict(
        db.execute(select(Order.status, func.count(Order.id)).group_by(Order.status)).all()
    )
    counts_by_status = {status.value: count for status, count in raw_counts.items()}

    delivered_rows = db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.delivery_fee), 0.0)).where(
            Order.status == OrderStatus.delivered
        )
    ).one()
    delivered_count, delivered_revenue = delivered_rows

    avg_fee = db.scalar(select(func.avg(Order.delivery_fee)))

    # Average delivery minutes as a SQL aggregate (#103 load finding): the
    # old version hydrated EVERY delivered order into Python to average two
    # timestamps - seconds of pure Python per call at 11k+ orders, and the
    # whole API queued behind it under concurrent load. The database does
    # this in milliseconds (same math the daily summary mart uses).
    avg_minutes = db.scalar(
        select(func.avg(func.extract("epoch", Order.delivered_at - Order.created_at) / 60.0)).where(
            Order.status == OrderStatus.delivered,
            Order.delivered_at.is_not(None),
        )
    )

    drivers_available = db.scalar(select(func.count(Driver.id)).where(Driver.is_available)) or 0
    drivers_total = db.scalar(select(func.count(Driver.id))) or 0
    restaurants_active = (
        db.scalar(select(func.count(Restaurant.id)).where(Restaurant.is_active)) or 0
    )

    return AnalyticsSummary(
        orders_total=total,
        orders_by_status=counts_by_status,
        delivered_revenue=round(float(delivered_revenue), 2),
        average_fee=round(float(avg_fee), 2) if avg_fee is not None else 0.0,
        average_delivery_minutes=(
            round(float(avg_minutes), 2) if avg_minutes is not None else None
        ),
        drivers_available=drivers_available,
        drivers_total=drivers_total,
        restaurants_active=restaurants_active,
    )


@router.get("/summary", response_model=AnalyticsSummary)
def summary(db: Session = Depends(get_db)) -> AnalyticsSummary:
    return _summary(db)


@router.get("/by-tenant", response_model=list[TenantBreakdown])
def by_tenant(db: Session = Depends(get_db)) -> list[TenantBreakdown]:
    """The same operational summary, one slice per tenant (#36).

    This is the operator view the Phase 4 dashboard's tenant filter will
    consume. RLS-respecting by construction: the tenants table itself is
    not a tenant-scoped row, and each slice is computed in its OWN session
    whose transaction pins ``app.current_tenant`` to that tenant — the
    database engine, not a WHERE clause, decides what each slice sees."""
    tenants = db.scalars(select(Tenant).order_by(Tenant.id)).all()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    breakdown: list[TenantBreakdown] = []
    for tenant in tenants:
        reset = set_request_tenant(tenant.id)
        try:
            with factory() as scoped:
                summary = _summary(scoped)
        finally:
            reset_request_tenant(reset)
        breakdown.append(
            TenantBreakdown(
                tenant_id=tenant.id,
                tenant_slug=tenant.slug,
                tenant_name=tenant.name,
                **summary.model_dump(),
            )
        )
    return breakdown


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> Response:
    """Prometheus text exposition of the operational counters."""
    s = _summary(db)
    lines = ["# HELP sdo_orders_total Total orders by status", "# TYPE sdo_orders_total gauge"]
    for status in ("pending", "assigned", "in_transit", "delivered", "cancelled"):
        lines.append(f'sdo_orders_total{{status="{status}"}} {s.orders_by_status.get(status, 0)}')
    lines += [
        "# HELP sdo_drivers_available Currently available drivers",
        "# TYPE sdo_drivers_available gauge",
        f"sdo_drivers_available {s.drivers_available}",
        "# HELP sdo_delivered_revenue_total Revenue from delivered orders",
        "# TYPE sdo_delivered_revenue_total gauge",
        f"sdo_delivered_revenue_total {s.delivered_revenue}",
        # ── Event-pipeline health (Phase 2 review finding 5): the relay and
        # ingest workers run without their own metrics endpoints, but the
        # pipeline's source of truth IS this database — so its delivery state
        # is surfaced here for the same Prometheus scraper.
        "# HELP sdo_outbox_unpublished Order and driver events awaiting relay to Kafka",
        "# TYPE sdo_outbox_unpublished gauge",
        f"sdo_outbox_unpublished {_outbox_backlog(db)}",
        "# HELP sdo_outbox_quarantined Events quarantined by the relay (dead letters)",
        "# TYPE sdo_outbox_quarantined gauge",
        f"sdo_outbox_quarantined {_outbox_quarantined(db)}",
        "# HELP sdo_outbox_last_relay_age_seconds Seconds since the last successful ORDER relay (driver relays do not update it) (-1 = never)",
        "# TYPE sdo_outbox_last_relay_age_seconds gauge",
        f"sdo_outbox_last_relay_age_seconds {_last_relay_age(db)}",
        # ── ETA serving (#69): 1 = the registered eta_model answers order
        # ETAs, 0 = the heuristic fallback is answering (model unloaded or
        # failed — check the API logs for the reason).
        "# HELP sdo_eta_model_serving 1 when the registered ETA model is serving, 0 when the heuristic fallback answers",
        "# TYPE sdo_eta_model_serving gauge",
        f"sdo_eta_model_serving {1 if eta_model_service.is_serving() else 0}",
        # ── m7 (#76): route_cache grows with distinct quoted pairs — bounded
        # in practice, unbounded in principle. The gauge makes the size
        # visible; the prune policy lives in docs/runbook.md.
        "# HELP sdo_route_cache_rows Cached route geometries (prune policy: docs/runbook.md)",
        "# TYPE sdo_route_cache_rows gauge",
        f"sdo_route_cache_rows {_route_cache_rows(db)}",
    ]
    return Response(media_type="text/plain; version=0.0.4", content="\n".join(lines) + "\n")


def _scoped_sessions(db: Session):
    """Yield (tenant_id, session) over every tenant, each session in that
    tenant's RLS scope. The platform view = the UNION of all tenant scopes,
    computed THROUGH row security rather than around it (#36)."""
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    tenants = db.scalars(select(Tenant).order_by(Tenant.id)).all()
    for tenant in tenants:
        reset = set_request_tenant(tenant.id)
        try:
            with factory() as scoped:
                yield tenant.id, scoped
        finally:
            reset_request_tenant(reset)


def _route_cache_rows(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(RouteCache)) or 0


def _outbox_backlog(db: Session) -> int:
    """Order and driver events not yet on the bus and not quarantined —
    summed across every tenant's scope (pipeline health is platform-level)."""
    total = 0
    for _tenant_id, scoped in _scoped_sessions(db):
        order_backlog = scoped.scalar(
            select(func.count(OrderEvent.id)).where(
                OrderEvent.published_at.is_(None), OrderEvent.quarantined_at.is_(None)
            )
        )
        driver_backlog = scoped.scalar(
            select(func.count(DriverEvent.id)).where(
                DriverEvent.published_at.is_(None), DriverEvent.quarantined_at.is_(None)
            )
        )
        total += (order_backlog or 0) + (driver_backlog or 0)
    return total


def _outbox_quarantined(db: Session) -> int:
    order_dead_letters = db.scalar(select(func.count(RelayDeadLetter.id)))
    driver_dead_letters = db.scalar(select(func.count(DriverRelayDeadLetter.id)))
    return (order_dead_letters or 0) + (driver_dead_letters or 0)


def _last_relay_age(db: Session) -> float:
    last_per_tenant: list = []
    for _tenant_id, scoped in _scoped_sessions(db):
        for ts in (
            scoped.scalar(select(func.max(OrderEvent.published_at))),
            scoped.scalar(select(func.max(DriverEvent.published_at))),
        ):
            if ts is not None:
                last_per_tenant.append(
                    ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
                )
    last = max(last_per_tenant) if last_per_tenant else None
    if last is None:
        return -1.0
    return round((datetime.now(UTC) - last).total_seconds(), 1)

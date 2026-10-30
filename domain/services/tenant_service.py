"""Tenant resolution — the single point where a write learns its owner (#35, #36).

Every business-row creation stamps ``tenant_id`` through here. Since #36 the
resolution is request-aware: the ASGI middleware pins the JWT's ``tid`` claim
into core.tenancy's ContextVar, and :func:`context_tenant_id` reads it —
falling back to the deployment's default tenant for anonymous/demo and
background paths. This is the funnel the tenancy foundation promised: the
bodies below changed, the call sites did not (offline scripts still call
``default_id`` deliberately — they predate any request and own no token).
"""

from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.tenancy import current_tenant_id
from domain.models import Tenant

DEFAULT_TENANT_SLUG = "default"

#: Settings keys a tenant may override (per-tenant pricing, #36). Anything
#: else in a tenant's pricing_overrides is rejected at the API boundary and
#: ignored here — pricing knobs can't be smuggled into arbitrary settings.
OVERRIDABLE = frozenset(
    {
        "pricing_base_fee",
        "pricing_per_km",
        "pricing_min_fee",
        "pricing_surge_tier1_ratio",
        "pricing_surge_tier1_multiplier",
        "pricing_surge_tier2_ratio",
        "pricing_surge_tier2_multiplier",
        "pricing_surge_tier3_ratio",
        "pricing_surge_tier3_multiplier",
    }
)


def resolve_default(session: Session) -> Tenant:
    """The deployment's default tenant, created on first use."""
    tenant = session.query(Tenant).filter_by(slug=DEFAULT_TENANT_SLUG).one_or_none()
    if tenant is None:
        tenant = Tenant(slug=DEFAULT_TENANT_SLUG, name="Default tenant")
        session.add(tenant)
        session.flush()  # tenant.id available to the caller's stamp
    return tenant


def default_id(session: Session) -> int:
    return resolve_default(session).id


def context_tenant_id(session: Session) -> int:
    """The tenant the CURRENT request acts as: the JWT claim when one is
    set, the default tenant otherwise. The one function every write goes
    through (#36) — with RLS (migration 003) Postgres enforces the same
    scope a second time, at the storage engine."""
    tid = current_tenant_id.get()
    if tid is not None:
        return tid
    return default_id(session)


def by_slug(session: Session, slug: str) -> Tenant | None:
    return session.query(Tenant).filter_by(slug=slug).one_or_none()


def effective_settings(session: Session, settings: Settings | None = None) -> Settings:
    """Global Settings merged with the CURRENT request tenant's pricing
    overrides — same resolution rule as :func:`context_tenant_id`, so an
    anonymous caller gets the default tenant's configuration. A tenant
    with no overrides gets the deployment defaults verbatim; unknown keys
    were rejected when the overrides were written."""
    s = settings or get_settings()
    tenant = session.get(Tenant, context_tenant_id(session))
    overrides = (tenant.pricing_overrides or {}) if tenant else {}
    update = {k: v for k, v in overrides.items() if k in OVERRIDABLE}
    return s.model_copy(update=update) if update else s


def set_overrides(session: Session, tenant: Tenant, overrides: dict) -> None:
    """Replace a tenant's pricing overrides after validating every key."""
    unknown = set(overrides) - OVERRIDABLE
    if unknown:
        raise ValueError(f"unknown pricing settings: {', '.join(sorted(unknown))}")
    tenant.pricing_overrides = overrides


def stamp_payload(payload: dict, tenant_id: int) -> dict:
    """Copy of the payload with the tenant inside — the event envelope
    (#35): the warehouse's raw layer stores payloads as received, so the
    tenant dimension flows to staging/marts without wire-format changes."""
    enveloped = dict(payload)
    enveloped["tenant_id"] = tenant_id
    return enveloped

"""Tenant resolution — the single point where a write learns its owner (#35).

Every business-row creation stamps ``tenant_id`` through here. Today the
resolution is always the deployment's default tenant (get-or-create by
slug, so even a database migrated before the seed has one); #36 replaces
this function's body with JWT-claim resolution WITHOUT touching any call
site — that is the whole point of funneling every write through one
function.
"""

from sqlalchemy.orm import Session

from domain.models import Tenant

DEFAULT_TENANT_SLUG = "default"


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


def stamp_payload(payload: dict, tenant_id: int) -> dict:
    """Copy of the payload with the tenant inside — the event envelope
    (#35): the warehouse's raw layer stores payloads as received, so the
    tenant dimension flows to staging/marts without wire-format changes."""
    enveloped = dict(payload)
    enveloped["tenant_id"] = tenant_id
    return enveloped

"""Tenant administration (#36) — platform-level, admin-only.

Tenants are the SaaS dimension itself, not a business row inside one, so
neither endpoint is tenant-scoped and both require the admin role (ops and
viewer of a single tenant must not onboard new tenants or list the
platform's). Business isolation starts AFTER onboarding: rows created by a
tenant's users are stamped and RLS-scoped to them.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_admin
from apps.api.schemas.tenants import TenantCreate, TenantOut
from domain.models import Tenant
from domain.services import tenant_service

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> Tenant:
    """Onboard a tenant (admin). Rows its users create are isolated from
    every other tenant's by the RLS policies (migration 003)."""
    if tenant_service.by_slug(db, payload.slug) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"tenant slug '{payload.slug}' already exists",
        )
    tenant = Tenant(slug=payload.slug, name=payload.name, city=payload.city)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("", response_model=list[TenantOut])
def list_tenants(
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> list[Tenant]:
    return list(db.scalars(select(Tenant).order_by(Tenant.id)).all())

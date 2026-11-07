"""Onboarding wizard endpoints (#224, Onboarding R2).

Two pieces the wizard page drives:

- `POST /onboarding/staff` — batch-issue staff users for the CALLER's
  tenant. A writer (ops) can mint ops/viewer; minting an admin requires
  an admin caller (no escalation through the wizard). Generated passwords
  are echoed exactly once — the wizard shows them, then they are gone.
  Per-user status (created / exists): the wizard is interactive, a
  re-click shouldn't fail the batch.
- `GET /onboarding/readiness` — the tenant-readiness checklist: is the
  tenant actually ready to trade (restaurants, available drivers, menu
  coverage, the copilot's knowledge corpus, staff users)? Doubles as the
  sales-call script: the five things a client must see green.
"""

import secrets

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.onboarding import (
    ReadinessResponse,
    ReadinessSection,
    StaffIssueRequest,
)
from core.security import hash_password
from domain.models import Driver, KnowledgeChunk, MenuItem, Restaurant, User
from domain.services import tenant_service

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("/staff")
def issue_staff(
    payload: StaffIssueRequest,
    db: Session = Depends(get_db),
    caller: dict = Depends(require_writer),
) -> dict:
    """Batch-issue staff users for the caller's tenant (#224)."""
    if any(m.role == "admin" for m in payload.users) and caller.get("role") != "admin":
        from core.exceptions import DomainError

        raise DomainError("only an admin can mint admin users")

    tid = tenant_service.context_tenant_id(db)
    results = []
    created_any = False
    for member in payload.users:
        existing = db.scalar(select(User).where(User.username == member.username))
        if existing is not None:
            results.append(
                {
                    "username": member.username,
                    "role": member.role,
                    "status": "exists",
                    "password": None,
                }
            )
            continue
        password = member.password or secrets.token_urlsafe(9)
        db.add(
            User(
                username=member.username,
                password_hash=hash_password(password),
                role=member.role,
                tenant_id=tid,
            )
        )
        created_any = True
        results.append(
            {
                "username": member.username,
                "role": member.role,
                "status": "created",
                "password": password,
            }
        )
    if created_any:
        db.commit()
    return {"issued": results}


@router.get("/readiness", response_model=ReadinessResponse)
def readiness(db: Session = Depends(get_db)) -> ReadinessResponse:
    """The tenant-readiness checklist (#224): the five things that make a
    tenant actually tradeable, each with a plain-language detail line."""

    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    tid = tenant_service.context_tenant_id(db)

    restaurant_count = count(
        select(func.count()).select_from(Restaurant).where(Restaurant.tenant_id == tid)
    )
    driver_count = count(
        select(func.count())
        .select_from(Driver)
        .where(Driver.tenant_id == tid, Driver.is_available.is_(True))
    )
    item_count = count(select(func.count()).select_from(MenuItem).where(MenuItem.tenant_id == tid))
    covered_restaurants = count(
        select(func.count(func.distinct(MenuItem.restaurant_id))).where(MenuItem.tenant_id == tid)
    )
    knowledge_count = count(
        select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.tenant_id == tid)
    )
    staff_count = count(select(func.count()).select_from(User).where(User.tenant_id == tid))

    menu_ok = item_count > 0 and covered_restaurants == restaurant_count
    sections = [
        ReadinessSection(
            key="restaurants",
            label="Restaurants live",
            ok=restaurant_count > 0,
            detail=f"{restaurant_count} restaurant(s) on file",
        ),
        ReadinessSection(
            key="drivers",
            label="Drivers dispatchable",
            ok=driver_count > 0,
            detail=f"{driver_count} driver(s) available now",
        ),
        ReadinessSection(
            key="menu",
            label="Menu coverage",
            ok=menu_ok,
            detail=(
                f"{item_count} item(s); {restaurant_count - (covered_restaurants or 0)} "
                "restaurant(s) with no menu yet"
                if restaurant_count
                else "no restaurants to cover"
            ),
        ),
        ReadinessSection(
            key="knowledge",
            label="Copilot knowledge",
            ok=knowledge_count > 0,
            detail=f"{knowledge_count} knowledge chunk(s) indexed for your tenant",
        ),
        ReadinessSection(
            key="users",
            label="Staff accounts",
            ok=staff_count >= 2,
            detail=f"{staff_count} user(s) — at least 2 before the team can split roles",
        ),
    ]
    return ReadinessResponse(ready=all(s.ok for s in sections), sections=sections)

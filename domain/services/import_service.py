"""Bulk import service (#223, Onboarding R1): the client's file → entities.

The contract from the issue: CSV or JSON rows, per-row validation,
address geocoding, ALL-OR-NOTHING per file with a per-row report
(imported / geocoded / failed-and-why). Two phases make all-or-nothing
real:

  Phase 1 — validate + geocode EVERY row. Nothing entity-wise touches
  the session yet; only the geocoder's technical cache may write (cache
  rows are city facts, safe to persist even when the import later
  rejects).
  Phase 2 — only if EVERY row passed: build all ORM objects and commit
  ONCE. Any row failure raises ImportRejectedError (422) with the full
  report before Phase 2 ever runs — the client fixes the named rows and
  re-submits.

Failure shapes stay honest (rule 11): an unresolvable ADDRESS is a row
failure (the file as a whole is rejected, the report says which row);
an unreachable geocoder is a 503 for the whole request (nothing to do
with the client's data — retry).

Nominatim fair use: ~1 req/s. Cache hits are free; every LIVE call is
followed by a pacing sleep (import_geocode_pace_seconds, default 1.05)
so a bulk file can never hammer the free provider. Re-imports of the
same file pay only for genuinely new addresses.
"""

import time
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.api.schemas.bulk_import import GEO_ENTITIES, ROW_MODELS, RowReport
from core.config import get_settings
from core.exceptions import ImportRejectedError
from domain.models import Customer, Driver, MenuItem, Restaurant
from domain.services import geocoder, tenant_service


def _pace(after_live_call: bool) -> None:
    """Sleep after a live Nominatim call so bulk files respect fair use."""
    if after_live_call:
        time.sleep(get_settings().import_geocode_pace_seconds)


def import_rows(session: Session, entity: str, rows: list[dict]) -> dict:
    """Validate + geocode + (all-or-nothing) import one entity's rows."""
    if entity not in ROW_MODELS:
        raise KeyError(entity)

    row_model = ROW_MODELS[entity]
    reports: list[RowReport] = []
    # the row union is entity-dispatched: each branch below only touches
    # the fields its row model guarantees (pydantic validated above)
    accepted: list[tuple[int, Any, dict | None]] = []  # (row_no, row, resolved)
    failed = False

    for row_no, raw in enumerate(rows, start=1):
        try:
            row: Any = row_model(**raw)
        except ValidationError as exc:
            reasons = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            reports.append(RowReport(row=row_no, status="failed", detail=reasons))
            failed = True
            continue

        resolved: dict | None = None
        if entity in GEO_ENTITIES:
            try:
                resolved = geocoder.geocode(session, row.address)
                _pace(resolved["cached"] is False)
            except geocoder.GeocodeAddressError as exc:
                reports.append(RowReport(row=row_no, status="failed", detail=str(exc)))
                failed = True
                continue
        accepted.append((row_no, row, resolved))

    if failed:
        raise ImportRejectedError(
            "import rejected all-or-nothing: fix the failed rows and re-submit",
            details={"entity": entity, "report": [r.model_dump() for r in reports]},
        )

    # menu rows must reference a restaurant of the CALLER's tenant (the
    # same scoping the portal's writes enforce — another tenant's
    # restaurant reads as not found)
    if entity == "menu-items":
        owned_ids = {r.id for r in session.query(Restaurant).all()}
        for row_no, row, _ in accepted:
            if row.restaurant_id not in owned_ids:
                reports.append(
                    RowReport(
                        row=row_no,
                        status="failed",
                        detail=f"restaurant {row.restaurant_id} not found in your tenant",
                    )
                )
        if any(r.status == "failed" for r in reports):
            raise ImportRejectedError(
                "import rejected all-or-nothing: fix the failed rows and re-submit",
                details={"entity": entity, "report": [r.model_dump() for r in reports]},
            )

    # Phase 2 — everything validated: build all rows, commit once
    tid = tenant_service.context_tenant_id(session)
    for row_no, row, resolved in accepted:
        if entity == "restaurants":
            assert resolved is not None  # geo rows always resolved in Phase 1
            session.add(
                Restaurant(
                    tenant_id=tid,
                    name=row.name,
                    latitude=resolved["lat"],
                    longitude=resolved["lon"],
                    is_active=row.is_active,
                )
            )
        elif entity == "drivers":
            assert resolved is not None  # geo rows always resolved in Phase 1
            session.add(
                Driver(
                    tenant_id=tid,
                    name=row.name,
                    vehicle=row.vehicle,
                    latitude=resolved["lat"],
                    longitude=resolved["lon"],
                )
            )
        elif entity == "customers":
            assert resolved is not None  # geo rows always resolved in Phase 1
            session.add(
                Customer(
                    tenant_id=tid,
                    name=row.name,
                    phone=row.phone,
                    latitude=resolved["lat"],
                    longitude=resolved["lon"],
                )
            )
        elif entity == "menu-items":
            session.add(
                MenuItem(
                    tenant_id=tid,
                    restaurant_id=row.restaurant_id,
                    name=row.name,
                    description=row.description,
                    price=row.price,
                    is_available=row.is_available,
                )
            )
        reports.append(
            RowReport(
                row=row_no,
                status="imported",
                resolved_address=resolved["display_name"] if resolved else None,
                latitude=resolved["lat"] if resolved else None,
                longitude=resolved["lon"] if resolved else None,
                cached=resolved["cached"] if resolved else None,
            )
        )

    session.commit()
    return {
        "entity": entity,
        "imported": len(accepted),
        "report": [r.model_dump() for r in reports],
    }

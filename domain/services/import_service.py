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
from typing import Any, Literal

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


def _validate_and_geocode(
    session: Session, entity: str, rows: list[dict]
) -> tuple[list[tuple[int, Any, dict | None]], list[RowReport], bool]:
    """Phase 1 shared by import and preview (#224): validate + geocode
    every row, nothing entity-wise written. Returns (accepted, reports,
    failed). Menu rows are additionally checked against the caller's
    tenant here so preview flags them too."""
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
                failed = True

    return accepted, reports, failed


def _find_existing(session: Session, entity: str, row: Any):
    """The row's natural key within the caller's tenant (#225): restaurants/
    drivers/customers key on NAME, menu items on (restaurant_id, name).
    A re-sent file with the same keys UPDATES instead of duplicating."""
    tid = tenant_service.context_tenant_id(session)
    if entity == "restaurants":
        return (
            session.query(Restaurant)
            .filter(Restaurant.tenant_id == tid, Restaurant.name == row.name)
            .one_or_none()
        )
    if entity == "drivers":
        return (
            session.query(Driver)
            .filter(Driver.tenant_id == tid, Driver.name == row.name)
            .one_or_none()
        )
    if entity == "customers":
        return (
            session.query(Customer)
            .filter(Customer.tenant_id == tid, Customer.name == row.name)
            .one_or_none()
        )
    return (
        session.query(MenuItem)
        .filter(
            MenuItem.tenant_id == tid,
            MenuItem.restaurant_id == row.restaurant_id,
            MenuItem.name == row.name,
        )
        .one_or_none()
    )


def _row_values(entity: str, row: Any, resolved: dict | None) -> dict:
    """The row's writable fields (the diff surface for idempotency)."""
    if entity == "restaurants":
        assert resolved is not None
        return {
            "name": row.name,
            "latitude": resolved["lat"],
            "longitude": resolved["lon"],
            "is_active": row.is_active,
        }
    if entity == "drivers":
        assert resolved is not None
        return {
            "name": row.name,
            "vehicle": row.vehicle,
            "latitude": resolved["lat"],
            "longitude": resolved["lon"],
        }
    if entity == "customers":
        assert resolved is not None
        return {
            "name": row.name,
            "phone": row.phone,
            "latitude": resolved["lat"],
            "longitude": resolved["lon"],
        }
    return {
        "restaurant_id": row.restaurant_id,
        "name": row.name,
        "description": row.description,
        "price": row.price,
        "is_available": row.is_available,
    }


def _classify(existing, values: dict) -> Literal["updated", "unchanged"]:
    """`unchanged` when every field already matches, else `updated`."""
    for field, value in values.items():
        if getattr(existing, field) != value:
            return "updated"
    return "unchanged"


def preview_rows(session: Session, entity: str, rows: list[dict]) -> dict:
    """The wizard's map preview (#224): exactly the import's validation +
    geocoding + KEY RESOLUTION, but NOTHING is committed — EVERY row
    appears in the report (new rows as `pending`, existing keys noted in
    the detail as will-update/no-change), so the client sees the full
    file's fate before deciding to import."""
    accepted, reports, _ = _validate_and_geocode(session, entity, rows)
    failed_rows = {r.row for r in reports}
    for row_no, row, resolved in accepted:
        if row_no in failed_rows:
            continue
        detail = None
        existing = _find_existing(session, entity, row)
        if existing is not None:
            outcome = _classify(existing, _row_values(entity, row, resolved))
            detail = (
                "exists — no change needed" if outcome == "unchanged" else "exists — will update"
            )
        reports.append(
            RowReport(
                row=row_no,
                status="pending",
                detail=detail,
                resolved_address=resolved["display_name"] if resolved else None,
                latitude=resolved["lat"] if resolved else None,
                longitude=resolved["lon"] if resolved else None,
                cached=resolved["cached"] if resolved else None,
            )
        )
    reports.sort(key=lambda r: r.row)
    return {
        "entity": entity,
        "would_import": len(accepted),
        "report": [r.model_dump() for r in reports],
    }


def import_rows(session: Session, entity: str, rows: list[dict]) -> dict:
    """Validate + geocode + (all-or-nothing, idempotent) import one
    entity's rows (#225): a natural-key match UPDATES in place (or reads
    `unchanged` when identical) instead of duplicating."""
    if entity not in ROW_MODELS:
        raise KeyError(entity)

    accepted, reports, failed = _validate_and_geocode(session, entity, rows)

    if failed:
        raise ImportRejectedError(
            "import rejected all-or-nothing: fix the failed rows and re-submit",
            details={"entity": entity, "report": [r.model_dump() for r in reports]},
        )

    # Phase 2 — everything validated: upsert by natural key, commit once
    model = {
        "restaurants": Restaurant,
        "drivers": Driver,
        "customers": Customer,
        "menu-items": MenuItem,
    }[entity]
    tid = tenant_service.context_tenant_id(session)
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for row_no, row, resolved in accepted:
        values = _row_values(entity, row, resolved)
        existing = _find_existing(session, entity, row)
        outcome: Literal["created", "updated", "unchanged"]
        if existing is None:
            session.add(model(**values, tenant_id=tid))
            outcome = "created"
        else:
            outcome = _classify(existing, values)
            if outcome == "updated":
                for field, value in values.items():
                    setattr(existing, field, value)
        counts[outcome] += 1
        reports.append(
            RowReport(
                row=row_no,
                status=outcome,
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
        "created": counts["created"],
        "updated": counts["updated"],
        "unchanged": counts["unchanged"],
        "report": [r.model_dump() for r in reports],
    }


# CSV templates (#225): one downloadable header + example row per entity —
# the client's starting point, served by GET /import/{entity}/template
TEMPLATES: dict[str, str] = {
    "restaurants": ('name,address,is_active\nCasa Aurelio,"Calle Gran Vía 42, Madrid",true\n'),
    "drivers": ('name,vehicle,address\nCarlos,motorcycle,"Plaza Mayor 1, Madrid"\n'),
    "customers": ('name,phone,address\nAna,+34 600 000 001,"Plaza Mayor 1, Madrid"\n'),
    "menu-items": (
        "restaurant_id,name,description,price,is_available\n"
        "1,Croqueta,Homemade ham croquette,4.5,true\n"
    ),
}

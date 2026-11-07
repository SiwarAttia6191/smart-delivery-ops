"""Bulk import endpoints (#223, Onboarding R1).

`POST /import/{entity}` — restaurants / drivers / customers / menu-items,
CSV or JSON, writer-gated. The client's file is all-or-nothing with a
per-row report: fix the named rows, re-submit. The client has ADDRESSES,
not coordinates — the geocoder (Nominatim + the permanent cache) turns
their text into ours, paced to the provider's fair-use policy.
"""

import csv
import io
import json

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.bulk_import import ROW_MODELS, ImportResult
from core.exceptions import ImportRejectedError, NotFoundError
from domain.services import import_service

router = APIRouter(tags=["imports"])


def _parse_rows(body: bytes, content_type: str) -> list[dict]:
    """CSV or JSON rows in — list-of-dicts out. CSV columns are the row
    models' field names; blank trailing lines are skipped."""
    if "text/csv" in content_type or "csv" in content_type:
        text = body.decode("utf-8-sig")
        return [
            {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            for row in csv.DictReader(io.StringIO(text))
            if any((v or "").strip() for v in row.values())
        ]
    if "json" in content_type:
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON import must be a list of row objects")
        return data
    raise ValueError(f"unsupported content type: {content_type} (use text/csv or application/json)")


@router.post(
    "/import/{entity}",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writer)],
)
async def import_entity(entity: str, request: Request, db: Session = Depends(get_db)):
    """Bulk-import one entity's rows, all-or-nothing, per-row report."""
    if entity not in ROW_MODELS:
        raise NotFoundError(f"unknown import entity '{entity}' (valid: {', '.join(ROW_MODELS)})")
    try:
        rows = _parse_rows(await request.body(), request.headers.get("content-type", ""))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportRejectedError(f"file could not be parsed: {exc}") from exc
    if not rows:
        raise ImportRejectedError("the file contains no rows")
    # returned as the dict — FastAPI validates/serializes against ImportResult
    return import_service.import_rows(db, entity, rows)


@router.post(
    "/import/{entity}/preview",
    dependencies=[Depends(require_writer)],
)
async def preview_entity(entity: str, request: Request, db: Session = Depends(get_db)):
    """The wizard's map preview (#224): the import's validation + geocoding
    pass with NOTHING committed — every row's fate and coordinates come
    back so the client can fix flagged rows before importing."""
    if entity not in ROW_MODELS:
        raise NotFoundError(f"unknown import entity '{entity}' (valid: {', '.join(ROW_MODELS)})")
    try:
        rows = _parse_rows(await request.body(), request.headers.get("content-type", ""))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportRejectedError(f"file could not be parsed: {exc}") from exc
    if not rows:
        raise ImportRejectedError("the file contains no rows")
    return import_service.preview_rows(db, entity, rows)

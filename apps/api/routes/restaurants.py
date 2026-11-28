"""Restaurant endpoints."""

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.order import RestaurantReviews
from apps.api.schemas.restaurant import (
    PickupPointResponse,
    PickupPointUpdate,
    RestaurantCreate,
    RestaurantResponse,
    SalesSnapshot,
    WeeklyReport,
)
from core.exceptions import NotFoundError
from domain.models import Restaurant
from domain.services import geocoder, review_service, sales_service, tenant_service

router = APIRouter(prefix="/restaurants", tags=["restaurants"])


@router.post(
    "",
    response_model=RestaurantResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_writer)],
)
def create_restaurant(payload: RestaurantCreate, db: Session = Depends(get_db)) -> Restaurant:
    restaurant = Restaurant(
        tenant_id=tenant_service.context_tenant_id(db),
        name=payload.name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        is_active=payload.is_active,
    )
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


@router.get("", response_model=list[RestaurantResponse])
def list_restaurants(db: Session = Depends(get_db)) -> list[Restaurant]:
    return list(db.scalars(select(Restaurant).order_by(Restaurant.id)))


@router.patch(
    "/{restaurant_id}/pickup-point",
    response_model=PickupPointResponse,
    dependencies=[Depends(require_writer)],
)
def update_pickup_point(
    restaurant_id: int,
    payload: PickupPointUpdate,
    db: Session = Depends(get_db),
) -> PickupPointResponse:
    """The owner sets their door (#358): the pickup identity, verified the
    same way the dropoff is. Coordinates alone = the dragged pin — the
    geocoder is never consulted against the owner's hand (`owner_verified`).
    Address alone = geocoded + self-verified (`geocoded`, with the witnesses'
    verdict — the portal tells the owner to check the pin when it flags).
    BOTH = the dragged coordinates are the truth, the typed text rides along
    as the label. Tenant-scoped like every write: another tenant's
    restaurant is the uniform 404."""
    tid = tenant_service.context_tenant_id(db)
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.id == restaurant_id, Restaurant.tenant_id == tid)
        .one_or_none()
    )
    if restaurant is None:
        raise NotFoundError(f"restaurant {restaurant_id} not found")

    new_lat = payload.pickup_latitude
    new_lon = payload.pickup_longitude
    if new_lat is not None and new_lon is not None:
        restaurant.latitude = new_lat
        restaurant.longitude = new_lon
        restaurant.pin_source = "owner_verified"
        precision = "owner_verified"
    else:
        resolved = geocoder.geocode(db, payload.address or "")
        restaurant.latitude = resolved["lat"]
        restaurant.longitude = resolved["lon"]
        restaurant.pin_source = "geocoded"
        precision = resolved["precision"]
    restaurant.address = payload.address

    db.commit()
    return PickupPointResponse(
        latitude=restaurant.latitude,
        longitude=restaurant.longitude,
        address=restaurant.address,
        pin_source=restaurant.pin_source,
        precision=precision,
    )


@router.get("/{restaurant_id}", response_model=RestaurantResponse)
def get_restaurant(restaurant_id: int, db: Session = Depends(get_db)) -> Restaurant:
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise NotFoundError(f"restaurant {restaurant_id} not found")
    return restaurant


@router.get("/{restaurant_id}/sales-snapshot", response_model=SalesSnapshot)
def get_sales_snapshot(restaurant_id: int, db: Session = Depends(get_db)) -> SalesSnapshot:
    """The restaurant's OWN numbers for the business day (#219): today's
    delivered orders, revenue and average delivery minutes, computed from
    the caller-tenant's orders. A live ops-slice per request — the
    analytical version belongs to the dashboard and the warehouse marts.
    Another tenant's restaurant reads as not found, the uniform scoping."""
    tid = tenant_service.context_tenant_id(db)
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.id == restaurant_id, Restaurant.tenant_id == tid)
        .one_or_none()
    )
    if restaurant is None:
        raise NotFoundError(f"restaurant {restaurant_id} not found")
    return SalesSnapshot(**sales_service.snapshot(db, restaurant))


@router.get("/{restaurant_id}/qr")
def restaurant_qr(restaurant_id: int, url: str = Query(...), db: Session = Depends(get_db)):
    """The restaurant's shareable link as a QR code (#230): an SVG the
    portal embeds for print and screens. The ``url`` is the caller's own
    public ordering link (the portal builds it from its own origin) —
    validated to an http(s) absolute URL, tenant-scoped like every read."""
    tid = tenant_service.context_tenant_id(db)
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.id == restaurant_id, Restaurant.tenant_id == tid)
        .one_or_none()
    )
    if restaurant is None:
        raise NotFoundError(f"restaurant {restaurant_id} not found")
    if not url.startswith(("http://", "https://")):
        raise NotFoundError("the qr target must be an absolute http(s) URL")
    import io

    # the qrcode library ships no type stubs
    import qrcode  # type: ignore[import-untyped]
    import qrcode.image.svg  # type: ignore[import-untyped]

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/{restaurant_id}/weekly-report", response_model=WeeklyReport)
def get_weekly_report(restaurant_id: int, db: Session = Depends(get_db)) -> WeeklyReport:
    """The Monday-morning artifact (#275): the last 7 business days for the
    caller's restaurant — per-day delivered orders, revenue and average
    delivery minutes, plus the week's fuel burn and rating average. A live
    ops-slice like the snapshot; the mart version is the analytical path.
    Another tenant's restaurant is the uniform 404."""
    tid = tenant_service.context_tenant_id(db)
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.id == restaurant_id, Restaurant.tenant_id == tid)
        .one_or_none()
    )
    if restaurant is None:
        raise NotFoundError(f"restaurant {restaurant_id} not found")
    return WeeklyReport(**sales_service.weekly_report(db, restaurant))


@router.get("/{restaurant_id}/reviews", response_model=RestaurantReviews)
def list_restaurant_reviews(restaurant_id: int, db: Session = Depends(get_db)) -> RestaurantReviews:
    """The restaurant's reviews (#216): newest first with the running
    average — open GET, tenant-scoped like every read (another tenant's
    restaurant is the uniform 404)."""
    tid = tenant_service.context_tenant_id(db)
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.id == restaurant_id, Restaurant.tenant_id == tid)
        .one_or_none()
    )
    if restaurant is None:
        raise NotFoundError(f"restaurant {restaurant_id} not found")
    return RestaurantReviews(**review_service.list_for_restaurant(db, restaurant))

"""Restaurant endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.order import RestaurantReviews
from apps.api.schemas.restaurant import RestaurantCreate, RestaurantResponse, SalesSnapshot
from core.exceptions import NotFoundError
from domain.models import Restaurant
from domain.services import review_service, sales_service, tenant_service

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

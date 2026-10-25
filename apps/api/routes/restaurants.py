"""Restaurant endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.deps import get_db
from apps.api.schemas.restaurant import RestaurantCreate, RestaurantResponse
from core.exceptions import NotFoundError
from domain.models import Restaurant

router = APIRouter(prefix="/restaurants", tags=["restaurants"])


@router.post("", response_model=RestaurantResponse, status_code=status.HTTP_201_CREATED)
def create_restaurant(payload: RestaurantCreate, db: Session = Depends(get_db)) -> Restaurant:
    restaurant = Restaurant(
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

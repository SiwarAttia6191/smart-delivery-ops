"""Restaurant menu endpoints (#159 item 4): the portal's CRUD surface.

Menu items hang off a restaurant of the caller's tenant. Scoping is
twofold like everywhere: the explicit tenant+restaurant filters keep
SQLite (tests, no RLS) correct and Postgres RLS (migration 008, the ninth
tenant table) re-enforces it at the engine. A menu write to another
tenant's restaurant reads as "restaurant not found" — the same uniform
404 the RLS-backed GETs produce.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.api.schemas.menu import MenuItemCreate, MenuItemOut, MenuItemUpdate
from core.exceptions import NotFoundError
from domain.models import MenuItem, Restaurant
from domain.services import tenant_service

router = APIRouter(tags=["menu"])


def _owned_restaurant(db: Session, restaurant_id: int) -> Restaurant:
    tid = tenant_service.context_tenant_id(db)
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.id == restaurant_id, Restaurant.tenant_id == tid)
        .one_or_none()
    )
    if restaurant is None:
        raise NotFoundError(f"restaurant {restaurant_id} not found")
    return restaurant


def _owned_item(db: Session, item_id: int) -> MenuItem:
    tid = tenant_service.context_tenant_id(db)
    item = (
        db.query(MenuItem).filter(MenuItem.id == item_id, MenuItem.tenant_id == tid).one_or_none()
    )
    if item is None:
        raise NotFoundError(f"menu item {item_id} not found")
    return item


@router.post(
    "/restaurants/{restaurant_id}/menu-items",
    response_model=MenuItemOut,
    status_code=status.HTTP_201_CREATED,
)
def create_menu_item(
    restaurant_id: int,
    payload: MenuItemCreate,
    db: Session = Depends(get_db),
    _writer: dict = Depends(require_writer),
) -> MenuItemOut:
    """Add an item to the restaurant's menu (writer)."""
    restaurant = _owned_restaurant(db, restaurant_id)
    item = MenuItem(
        tenant_id=restaurant.tenant_id,
        restaurant_id=restaurant.id,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        is_available=payload.is_available,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return MenuItemOut.model_validate(item)


@router.get("/restaurants/{restaurant_id}/menu-items", response_model=list[MenuItemOut])
def list_menu_items(restaurant_id: int, db: Session = Depends(get_db)) -> list[MenuItemOut]:
    """The restaurant's menu (open GET, tenant-scoped; the storefront and
    the portal both read this)."""
    _owned_restaurant(db, restaurant_id)
    items = (
        db.query(MenuItem)
        .filter(MenuItem.restaurant_id == restaurant_id)
        .order_by(MenuItem.created_at)
        .all()
    )
    return [MenuItemOut.model_validate(i) for i in items]


@router.patch("/menu-items/{item_id}", response_model=MenuItemOut)
def update_menu_item(
    item_id: int,
    payload: MenuItemUpdate,
    db: Session = Depends(get_db),
    _writer: dict = Depends(require_writer),
) -> MenuItemOut:
    """Rename, reprice, or flip availability (sold out) — writer."""
    item = _owned_item(db, item_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return MenuItemOut.model_validate(item)


@router.delete("/menu-items/{item_id}", response_model=MenuItemOut)
def delete_menu_item(
    item_id: int,
    db: Session = Depends(get_db),
    _writer: dict = Depends(require_writer),
) -> MenuItemOut:
    """Remove an item from the menu (writer). Availability flips cover
    "sold out"; this is the actual removal."""
    item = _owned_item(db, item_id)
    out = MenuItemOut.model_validate(item)
    db.delete(item)
    db.commit()
    return out

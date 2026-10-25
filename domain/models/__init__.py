"""SQLAlchemy models. Importing this package registers all tables on Base.metadata."""

from domain.models.base import Base
from domain.models.customer import Customer
from domain.models.driver import Driver
from domain.models.order import Order, OrderStatus
from domain.models.order_event import OrderEvent
from domain.models.relay_state import RelayState
from domain.models.restaurant import Restaurant

__all__ = [
    "Base",
    "Customer",
    "Driver",
    "Order",
    "OrderEvent",
    "OrderStatus",
    "RelayState",
    "Restaurant",
]

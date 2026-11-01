"""SQLAlchemy models. Importing this package registers all tables on Base.metadata."""

from domain.models.base import Base
from domain.models.coupon import Coupon
from domain.models.customer import Customer
from domain.models.demand_forecast import DemandForecast
from domain.models.deployment_state import INCARNATION_KEY, LEGACY_INCARNATION, DeploymentState
from domain.models.driver import Driver
from domain.models.driver_event import DriverEvent
from domain.models.driver_relay_dead_letter import DriverRelayDeadLetter
from domain.models.knowledge_chunk import KnowledgeChunk
from domain.models.order import Order, OrderStatus
from domain.models.order_event import OrderEvent
from domain.models.relay_dead_letter import RelayDeadLetter
from domain.models.relay_state import RelayState
from domain.models.restaurant import Restaurant
from domain.models.route_cache import RouteCache
from domain.models.tenant import Tenant
from domain.models.user import User

__all__ = [
    "Base",
    "Coupon",
    "Customer",
    "DeploymentState",
    "INCARNATION_KEY",
    "LEGACY_INCARNATION",
    "DemandForecast",
    "Driver",
    "DriverEvent",
    "DriverRelayDeadLetter",
    "KnowledgeChunk",
    "Order",
    "OrderEvent",
    "OrderStatus",
    "RelayDeadLetter",
    "RelayState",
    "Restaurant",
    "RouteCache",
    "Tenant",
    "User",
]

"""Pydantic schemas (request/response contracts)."""

from apps.api.schemas.analytics import AnalyticsSummary
from apps.api.schemas.customer import CustomerCreate, CustomerResponse
from apps.api.schemas.driver import DriverCreate, DriverResponse, DriverUpdate
from apps.api.schemas.order import (
    OrderCreate,
    OrderResponse,
    OrderStatusUpdate,
)
from apps.api.schemas.pricing import QuoteRequest, QuoteResponse
from apps.api.schemas.restaurant import RestaurantCreate, RestaurantResponse

__all__ = [
    "AnalyticsSummary",
    "CustomerCreate",
    "CustomerResponse",
    "DriverCreate",
    "DriverResponse",
    "DriverUpdate",
    "OrderCreate",
    "OrderResponse",
    "OrderStatusUpdate",
    "QuoteRequest",
    "QuoteResponse",
    "RestaurantCreate",
    "RestaurantResponse",
]

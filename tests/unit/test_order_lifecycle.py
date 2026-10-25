"""Unit tests for the order lifecycle state machine."""

import pytest

from core.exceptions import InvalidTransitionError, NoDriverAvailableError
from domain.models import Customer, Driver, OrderStatus, Restaurant
from domain.services import order_service


@pytest.fixture
def entities(db):
    customer = Customer(name="Aziza", latitude=36.8381, longitude=10.2385)  # Lac 2
    restaurant = Restaurant(
        name="Chez Slah",
        latitude=36.8065,
        longitude=10.1815,  # Tunis centre
    )
    driver = Driver(name="Karim", vehicle="motorcycle", latitude=36.8100, longitude=10.1900)
    db.add_all([customer, restaurant, driver])
    db.commit()
    return customer, restaurant, driver


class TestCreateOrder:
    def test_create_pending_order_with_quote(self, db, entities):
        customer, restaurant, _ = entities
        order = order_service.create_order(db, customer.id, restaurant.id, 36.8381, 10.2385)
        assert order.status is OrderStatus.pending
        assert order.driver_id is None
        assert 5.0 < order.distance_km < 8.0  # Tunis centre → Lac 2
        assert order.delivery_fee >= 3.0
        assert order.eta_minutes > 0
        assert [e.event_type for e in order.events] == ["created"]

    def test_unknown_customer_raises(self, db, entities):
        _, restaurant, _ = entities
        from core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            order_service.create_order(db, 999, restaurant.id, 36.8, 10.1)


class TestAssignAndTransitions:
    def test_full_happy_path(self, db, entities):
        customer, restaurant, driver = entities
        order = order_service.create_order(db, customer.id, restaurant.id, 36.8381, 10.2385)

        order = order_service.assign_driver(db, order.id)
        assert order.status is OrderStatus.assigned
        assert order.driver_id == driver.id
        assert driver.is_available is False

        order = order_service.transition(db, order.id, OrderStatus.in_transit)
        assert order.status is OrderStatus.in_transit

        order = order_service.transition(db, order.id, OrderStatus.delivered)
        assert order.status is OrderStatus.delivered
        assert order.delivered_at is not None
        assert driver.is_available is True  # freed after delivery
        assert order.delivery_minutes is not None

        types = [e.event_type for e in order.events]
        assert types == ["created", "assigned", "status", "status"]

    def test_assign_rejects_non_pending(self, db, entities):
        customer, restaurant, _ = entities
        order = order_service.create_order(db, customer.id, restaurant.id, 36.8381, 10.2385)
        order = order_service.assign_driver(db, order.id)
        with pytest.raises(InvalidTransitionError):
            order_service.assign_driver(db, order.id)

    def test_assign_without_drivers_raises(self, db, entities):
        customer, restaurant, driver = entities
        driver.is_available = False
        db.commit()
        order = order_service.create_order(db, customer.id, restaurant.id, 36.8381, 10.2385)
        with pytest.raises(NoDriverAvailableError):
            order_service.assign_driver(db, order.id)

    def test_invalid_skip_transition(self, db, entities):
        customer, restaurant, _ = entities
        order = order_service.create_order(db, customer.id, restaurant.id, 36.8381, 10.2385)
        with pytest.raises(InvalidTransitionError):
            order_service.transition(db, order.id, OrderStatus.delivered)

    def test_cancel_frees_driver(self, db, entities):
        customer, restaurant, driver = entities
        order = order_service.create_order(db, customer.id, restaurant.id, 36.8381, 10.2385)
        order = order_service.assign_driver(db, order.id)
        order = order_service.transition(db, order.id, OrderStatus.cancelled)
        assert order.status is OrderStatus.cancelled
        assert driver.is_available is True

    def test_terminal_states_are_final(self, db, entities):
        customer, restaurant, _ = entities
        order = order_service.create_order(db, customer.id, restaurant.id, 36.8381, 10.2385)
        order = order_service.transition(db, order.id, OrderStatus.cancelled)
        with pytest.raises(InvalidTransitionError):
            order_service.transition(db, order.id, OrderStatus.in_transit)

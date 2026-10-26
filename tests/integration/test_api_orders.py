"""Integration tests: the order flow over HTTP, as a client would drive it."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from domain.models import DriverEvent, DriverRelayDeadLetter

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


def _seed(client: TestClient) -> dict:
    customer = client.post("/customers", json=CUSTOMER).json()
    restaurant = client.post("/restaurants", json=RESTAURANT).json()
    driver = client.post("/drivers", json=DRIVER).json()
    return {"customer": customer, "restaurant": restaurant, "driver": driver}


class TestHealth:
    def test_health_reports_database(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["database"] == "up"


class TestDriverEvents:
    def test_registration_and_heartbeat_record_availability_changes(self, client, db):
        created = client.post("/drivers", json=DRIVER)
        assert created.status_code == 201
        driver = created.json()

        unchanged = client.patch(
            f"/drivers/{driver['id']}",
            json={"latitude": 36.81, "longitude": 10.19, "is_available": True},
        )
        unavailable = client.patch(
            f"/drivers/{driver['id']}",
            json={"latitude": 36.81, "longitude": 10.19, "is_available": False},
        )

        assert unchanged.status_code == 200
        assert unavailable.status_code == 200
        events = (
            db.query(DriverEvent).filter_by(driver_id=driver["id"]).order_by(DriverEvent.id).all()
        )
        assert [event.payload["reason"] for event in events] == ["registered", "heartbeat"]
        assert [event.payload["to_available"] for event in events] == [True, False]
        assert events[-1].payload["latitude"] == 36.81


class TestOutboxMetrics:
    def test_metrics_include_driver_backlog_and_quarantine(self, client, db):
        driver = client.post("/drivers", json=DRIVER).json()
        event = db.query(DriverEvent).filter_by(driver_id=driver["id"]).one()
        event.quarantined_at = event.created_at
        db.add(
            DriverEvent(
                driver_id=driver["id"],
                event_type="availability",
                payload={"from_available": True, "to_available": False},
            )
        )
        db.add(
            DriverRelayDeadLetter(
                event_id=event.id,
                driver_id=driver["id"],
                event_type=event.event_type,
                error="test quarantine",
            )
        )
        db.commit()

        metrics = client.get("/analytics/metrics").text

        assert "sdo_outbox_unpublished 1" in metrics
        assert "sdo_outbox_quarantined 1" in metrics

    def test_metrics_calculate_age_from_driver_relay(self, client, db):
        driver = client.post("/drivers", json=DRIVER).json()
        event = db.query(DriverEvent).filter_by(driver_id=driver["id"]).one()
        event.published_at = datetime.now(UTC) - timedelta(seconds=5)
        db.commit()

        metrics = client.get("/analytics/metrics").text
        age = next(
            line
            for line in metrics.splitlines()
            if line.startswith("sdo_outbox_last_relay_age_seconds ")
        )

        assert 0 <= float(age.rsplit(" ", 1)[1]) <= 10


class TestOrderFlow:
    def test_quote_then_create_assign_deliver(self, client, db):
        ids = _seed(client)

        quote = client.post(
            "/pricing/quote",
            json={
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
            },
        ).json()
        assert quote["distance_km"] > 0
        assert quote["total"] >= 3.0

        created = client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
            },
        )
        assert created.status_code == 201
        order = created.json()
        assert order["status"] == "pending"
        assert order["delivery_fee"] == quote["total"]
        order_id = order["id"]

        assigned = client.post(f"/orders/{order_id}/assign").json()
        assert assigned["status"] == "assigned"
        assert assigned["driver_id"] == ids["driver"]["id"]

        in_transit = client.post(f"/orders/{order_id}/status", json={"status": "in_transit"}).json()
        assert in_transit["status"] == "in_transit"

        delivered = client.post(f"/orders/{order_id}/status", json={"status": "delivered"}).json()
        assert delivered["status"] == "delivered"
        assert delivered["delivered_at"] is not None
        assert len(delivered["events"]) == 4

        # Driver was freed and shows as available again.
        drivers = client.get("/drivers", params={"available": True}).json()
        assert any(d["id"] == ids["driver"]["id"] for d in drivers)
        driver_events = (
            db.query(DriverEvent).filter_by(driver_id=ids["driver"]["id"]).order_by(DriverEvent.id)
        )
        assert [event.payload["to_available"] for event in driver_events] == [True, False, True]
        assert [event.payload["reason"] for event in driver_events] == [
            "registered",
            "order_assigned",
            "order_delivered",
        ]

    def test_order_not_found(self, client):
        response = client.get("/orders/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_invalid_transition_conflict(self, client):
        ids = _seed(client)
        order = client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": 36.84,
                "dropoff_longitude": 10.24,
            },
        ).json()
        response = client.post(f"/orders/{order['id']}/status", json={"status": "delivered"})
        assert response.status_code == 409

    def test_assign_without_drivers_conflict(self, client):
        customer = client.post("/customers", json=CUSTOMER).json()
        restaurant = client.post("/restaurants", json=RESTAURANT).json()
        order = client.post(
            "/orders",
            json={
                "customer_id": customer["id"],
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": 36.84,
                "dropoff_longitude": 10.24,
            },
        ).json()
        response = client.post(f"/orders/{order['id']}/assign")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "no_driver_available"

    def test_assigned_status_via_endpoint_is_rejected(self, client):
        ids = _seed(client)
        order = client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": 36.84,
                "dropoff_longitude": 10.24,
            },
        ).json()
        response = client.post(f"/orders/{order['id']}/status", json={"status": "assigned"})
        assert response.status_code == 422  # schema-level guard


class TestAnalytics:
    def test_summary_reflects_delivered_order(self, client):
        ids = _seed(client)
        order = client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": 36.8381,
                "dropoff_longitude": 10.2385,
            },
        ).json()
        client.post(f"/orders/{order['id']}/assign")
        client.post(f"/orders/{order['id']}/status", json={"status": "in_transit"})
        client.post(f"/orders/{order['id']}/status", json={"status": "delivered"})

        summary = client.get("/analytics/summary").json()
        assert summary["orders_total"] == 1
        assert summary["orders_by_status"]["delivered"] == 1
        assert summary["delivered_revenue"] == order["delivery_fee"]
        assert summary["average_delivery_minutes"] is not None

        metrics = client.get("/analytics/metrics").text
        assert 'sdo_orders_total{status="delivered"} 1' in metrics

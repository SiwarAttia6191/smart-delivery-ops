"""Integration tests: the order flow over HTTP, as a client would drive it."""

from fastapi.testclient import TestClient

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


class TestOrderFlow:
    def test_quote_then_create_assign_deliver(self, client):
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

"""Integration tests: the order flow over HTTP, as a client would drive it."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from domain.models import DriverEvent, DriverRelayDeadLetter

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


def _eta_heuristic_for(distance_km: float) -> int:
    """The champion formula, mirrored for assertions (ceil(prep + travel + buffer))."""
    import math

    return math.ceil(12 + (distance_km / 20.0) * 60 + 5)


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
    def test_registration_and_heartbeat_record_availability_changes(self, authed_client, db):
        created = authed_client.post("/drivers", json=DRIVER)
        assert created.status_code == 201
        driver = created.json()

        unchanged = authed_client.patch(
            f"/drivers/{driver['id']}",
            json={"latitude": 36.81, "longitude": 10.19, "is_available": True},
        )
        unavailable = authed_client.patch(
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
    def test_metrics_include_driver_backlog_and_quarantine(self, authed_client, db):
        driver = authed_client.post("/drivers", json=DRIVER).json()
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

        metrics = authed_client.get("/analytics/metrics").text

        assert "sdo_outbox_unpublished 1" in metrics
        assert "sdo_outbox_quarantined 1" in metrics

    def test_metrics_calculate_age_from_driver_relay(self, authed_client, db):
        driver = authed_client.post("/drivers", json=DRIVER).json()
        event = db.query(DriverEvent).filter_by(driver_id=driver["id"]).one()
        event.published_at = datetime.now(UTC) - timedelta(seconds=5)
        db.commit()

        metrics = authed_client.get("/analytics/metrics").text
        age = next(
            line
            for line in metrics.splitlines()
            if line.startswith("sdo_outbox_last_relay_age_seconds ")
        )

        assert 0 <= float(age.rsplit(" ", 1)[1]) <= 10


class TestRoutes:
    def test_demo_route_returns_geojson(self, client):
        response = client.get("/routes/demo")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/geo+json")
        body = response.json()
        assert body["type"] == "FeatureCollection"
        feature = body["features"][0]
        assert feature["geometry"]["type"] == "LineString"
        assert feature["geometry"]["coordinates"][0] == [10.181621, 36.806522]
        assert feature["geometry"]["coordinates"][-1] == [10.238784, 36.838188]
        assert feature["properties"] == {"distance_m": 7645.2, "duration_s": 510.6}


class TestOrderFlow:
    def test_quote_then_create_assign_deliver(self, authed_client, db):
        ids = _seed(authed_client)

        quote = authed_client.post(
            "/pricing/quote",
            json={
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
            },
        ).json()
        assert quote["distance_km"] > 0
        assert quote["total"] >= 3.0

        created = authed_client.post(
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

        assigned = authed_client.post(f"/orders/{order_id}/assign").json()
        assert assigned["status"] == "assigned"
        assert assigned["driver_id"] == ids["driver"]["id"]

        in_transit = authed_client.post(
            f"/orders/{order_id}/status", json={"status": "in_transit"}
        ).json()
        assert in_transit["status"] == "in_transit"

        delivered = authed_client.post(
            f"/orders/{order_id}/status", json={"status": "delivered"}
        ).json()
        assert delivered["status"] == "delivered"
        assert delivered["delivered_at"] is not None
        assert len(delivered["events"]) == 4

        # Driver was freed and shows as available again.
        drivers = authed_client.get("/drivers", params={"available": True}).json()
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

    def test_order_route_endpoint_serves_captured_geometry(self, authed_client, db, monkeypatch):
        from domain.models import RouteCache
        from domain.services import route_service

        monkeypatch.setattr(
            route_service,
            "_fetch_geometry",
            lambda base_url, key: {
                "coordinates": [[10.181621, 36.806522], [10.2385, 36.8381]],
                "distance_m": 7645.2,
                "duration_s": 510.6,
            },
        )
        ids = _seed(authed_client)
        created = authed_client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
            },
        ).json()

        response = authed_client.get(f"/orders/{created['id']}/route")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/geo+json")
        body = response.json()
        assert body["type"] == "FeatureCollection"
        assert body["features"][0]["geometry"]["type"] == "LineString"
        assert body["features"][0]["properties"]["distance_m"] == 7645.2
        # a second order with the same pair reuses the cached geometry
        assert db.query(RouteCache).count() == 1

    def test_order_route_unknown_order_404(self, client):
        response = client.get("/orders/00000000-0000-0000-0000-000000000000/route")
        assert response.status_code == 404

    def test_order_route_without_geometry_404(self, authed_client, monkeypatch):
        # Simulate the OSRM outage at creation time (a live OSRM would
        # otherwise capture real geometry for this order).
        from domain.services import route_service

        monkeypatch.setattr(route_service, "_fetch_geometry", lambda base_url, key: None)
        ids = _seed(authed_client)
        created = authed_client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": 36.60,
                "dropoff_longitude": 10.60,
            },
        ).json()
        response = authed_client.get(f"/orders/{created['id']}/route")
        assert response.status_code == 404
        assert "no route geometry" in response.json()["error"]["message"]

    def test_eta_model_serving_and_gauge(self, authed_client, monkeypatch):
        # With the model "loaded", order ETAs come from it; the gauge agrees.
        from domain.services import eta_model_service

        class FakeModel:
            def predict(self, frame):
                assert list(frame.columns) == [
                    "distance_km",
                    "hour_local",
                    "dow",
                    "pickup_zone",
                    "dropoff_zone",
                    "dropoff_zone_lon",
                ]
                return [25.4]

        monkeypatch.setattr(eta_model_service, "_model", FakeModel())
        monkeypatch.setattr(eta_model_service, "_loaded", True)

        ids = _seed(authed_client)
        created = authed_client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
            },
        ).json()
        assert created["eta_minutes"] == 25  # round(25.4); heuristic would differ

        metrics = authed_client.get("/analytics/metrics").text
        gauge = next(
            line for line in metrics.splitlines() if line.startswith("sdo_eta_model_serving ")
        )
        assert gauge.rsplit(" ", 1)[1] == "1"

    def test_eta_heuristic_fallback_gauge_zero(self, authed_client):
        # No model loaded: heuristic answers, gauge reports the fallback.
        from domain.services import eta_model_service

        eta_model_service.reset_state()
        ids = _seed(authed_client)
        created = authed_client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
            },
        ).json()
        assert created["eta_minutes"] == _eta_heuristic_for(created["distance_km"])

        metrics = authed_client.get("/analytics/metrics").text
        gauge = next(
            line for line in metrics.splitlines() if line.startswith("sdo_eta_model_serving ")
        )
        assert gauge.rsplit(" ", 1)[1] == "0"

    def test_quote_without_forecasts_derives_neutral_ratio(self, authed_client):
        # No demand_forecasts rows: derivation must fall back to 1.0, never
        # spike a customer's fee on missing data.
        ids = _seed(authed_client)
        quote = authed_client.post(
            "/pricing/quote",
            json={
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
            },
        ).json()
        assert quote["surge_multiplier"] == 1.0

    def test_order_not_found(self, client):
        response = client.get("/orders/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_invalid_transition_conflict(self, authed_client):
        ids = _seed(authed_client)
        order = authed_client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": 36.84,
                "dropoff_longitude": 10.24,
            },
        ).json()
        response = authed_client.post(f"/orders/{order['id']}/status", json={"status": "delivered"})
        assert response.status_code == 409

    def test_assign_without_drivers_conflict(self, authed_client):
        customer = authed_client.post("/customers", json=CUSTOMER).json()
        restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
        order = authed_client.post(
            "/orders",
            json={
                "customer_id": customer["id"],
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": 36.84,
                "dropoff_longitude": 10.24,
            },
        ).json()
        response = authed_client.post(f"/orders/{order['id']}/assign")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "no_driver_available"

    def test_assigned_status_via_endpoint_is_rejected(self, authed_client):
        ids = _seed(authed_client)
        order = authed_client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": 36.84,
                "dropoff_longitude": 10.24,
            },
        ).json()
        response = authed_client.post(f"/orders/{order['id']}/status", json={"status": "assigned"})
        assert response.status_code == 422  # schema-level guard


class TestAnalytics:
    def test_summary_reflects_delivered_order(self, authed_client):
        ids = _seed(authed_client)
        order = authed_client.post(
            "/orders",
            json={
                "customer_id": ids["customer"]["id"],
                "restaurant_id": ids["restaurant"]["id"],
                "dropoff_latitude": 36.8381,
                "dropoff_longitude": 10.2385,
            },
        ).json()
        authed_client.post(f"/orders/{order['id']}/assign")
        authed_client.post(f"/orders/{order['id']}/status", json={"status": "in_transit"})
        authed_client.post(f"/orders/{order['id']}/status", json={"status": "delivered"})

        summary = authed_client.get("/analytics/summary").json()
        assert summary["orders_total"] == 1
        assert summary["orders_by_status"]["delivered"] == 1
        assert summary["delivered_revenue"] == order["delivery_fee"]
        assert summary["average_delivery_minutes"] is not None

        metrics = authed_client.get("/analytics/metrics").text
        assert 'sdo_orders_total{status="delivered"} 1' in metrics

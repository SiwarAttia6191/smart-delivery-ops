"""Sales snapshot tests (#219): the portal's own numbers for today.

The guarantees: delivered-only revenue math (a cancelled order's fee
never counts, yesterday's deliveries don't either — the window is the
BUSINESS day), empty state (zeros and nulls, not an error), and scoping
(another tenant's restaurant reads as not found — the uniform 404).
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from domain.models import Order

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


def _seed_and_place(client: TestClient, count: int = 1) -> tuple[dict, list[dict]]:
    customer = client.post("/customers", json=CUSTOMER).json()
    restaurant = client.post("/restaurants", json=RESTAURANT).json()
    client.post("/drivers", json=DRIVER)
    orders = []
    for _ in range(count):
        placed = client.post(
            "/orders",
            json={
                "customer_id": customer["id"],
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
                "demand_ratio": 0,
            },
        )
        assert placed.status_code == 201, placed.text
        orders.append(placed.json())
    return restaurant, orders


def _deliver(client: TestClient, order_id: str) -> None:
    client.post(f"/orders/{order_id}/assign")
    client.post(f"/orders/{order_id}/status", json={"status": "in_transit"})
    delivered = client.post(f"/orders/{order_id}/status", json={"status": "delivered"})
    assert delivered.status_code == 200, delivered.text


def test_delivered_only_revenue_math(authed_client: TestClient, db):
    restaurant, orders = _seed_and_place(authed_client, count=3)

    # deliver two, cancel the third — its fee must never count
    for order in orders[:2]:
        _deliver(authed_client, order["id"])
    cancelled = authed_client.post(
        f"/orders/{orders[2]['id']}/status", json={"status": "cancelled"}
    )
    assert cancelled.status_code == 200

    # backdate the second delivery to YESTERDAY (business-day window)
    yesterday = datetime.now(UTC) - timedelta(days=2)
    old = db.get(Order, orders[1]["id"])
    old.created_at = yesterday
    old.delivered_at = yesterday + timedelta(minutes=20)
    db.commit()

    snap = authed_client.get(f"/restaurants/{restaurant['id']}/sales-snapshot").json()
    assert snap["delivered_orders"] == 1  # yesterday's delivery is outside the window
    assert snap["revenue"] == orders[0]["delivery_fee"]  # cancelled fee excluded
    # the test lifecycle completes in milliseconds — 0.0 min is honest
    assert snap["avg_delivery_minutes"] is not None and snap["avg_delivery_minutes"] >= 0
    assert snap["rating_average"] is None  # rides null until #216
    assert snap["business_date"] == datetime.now(UTC).date().isoformat()


def test_empty_state_is_zeros_not_an_error(authed_client: TestClient):
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    snap = authed_client.get(f"/restaurants/{restaurant['id']}/sales-snapshot").json()
    assert snap["delivered_orders"] == 0
    assert snap["revenue"] == 0
    assert snap["avg_delivery_minutes"] is None
    assert snap["rating_average"] is None


def test_snapshot_is_tenant_scoped(client: TestClient, authed_client: TestClient, db):
    from core.security import hash_password
    from domain.models import Tenant, User

    restaurant, _ = _seed_and_place(authed_client, count=1)

    db.add(Tenant(slug="acme3", name="Acme3"))
    db.commit()
    acme = db.query(Tenant).filter_by(slug="acme3").one()
    db.add(
        User(username="acme3ops", password_hash=hash_password("pw"), role="ops", tenant_id=acme.id)
    )
    db.commit()
    from tests.integration.test_api_promotions import _headers, _token

    acme_headers = _headers(_token(client, "acme3ops", "pw"))
    response = client.get(f"/restaurants/{restaurant['id']}/sales-snapshot", headers=acme_headers)
    assert response.status_code == 404  # another tenant's restaurant: uniform not-found
    assert "not found" in response.json()["error"]["message"]

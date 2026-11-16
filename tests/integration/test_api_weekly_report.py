"""Weekly report tests (#275): the Monday-morning artifact.

The guarantees: the multi-day math across a real window (a backdated
delivery lands on ITS business day), the fuel actuals joined per
assigned driver's vehicle, the empty state (zeros and nulls, not an
error), and tenant scoping (another tenant's restaurant is the uniform
404).
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from domain.models import Order

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


def _place(client: TestClient, restaurant_id: int, customer_id: int) -> dict:
    order = client.post(
        "/orders",
        json={
            "customer_id": customer_id,
            "restaurant_id": restaurant_id,
            "dropoff_latitude": CUSTOMER["latitude"],
            "dropoff_longitude": CUSTOMER["longitude"],
            "demand_ratio": 0,
        },
    )
    assert order.status_code == 201, order.text
    return order.json()


def _deliver(client: TestClient, order_id: str) -> None:
    client.post(f"/orders/{order_id}/assign")
    client.post(f"/orders/{order_id}/status", json={"status": "in_transit"})
    delivered = client.post(f"/orders/{order_id}/status", json={"status": "delivered"})
    assert delivered.status_code == 200, delivered.text


def test_multi_day_math_and_fuel_join(authed_client: TestClient, db):
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    authed_client.post("/drivers", json=DRIVER)

    # today: one delivered order (fee F1)
    today_order = _place(authed_client, restaurant["id"], customer["id"])
    _deliver(authed_client, today_order["id"])
    today_fee = today_order["delivery_fee"]

    # 3 days ago: one delivered order (fee F2) — inside the 7-day window
    old_order = _place(authed_client, restaurant["id"], customer["id"])
    _deliver(authed_client, old_order["id"])
    old = db.get(Order, old_order["id"])
    shift = timedelta(days=3)
    old.created_at = old.created_at - shift
    old.delivered_at = old.delivered_at - shift
    db.commit()
    old_fee = old_order["delivery_fee"]

    report = authed_client.get(f"/restaurants/{restaurant['id']}/weekly-report").json()
    assert report["delivered_orders"] == 2
    assert report["revenue"] == round(today_fee + old_fee, 2)
    assert report["period_end"] == datetime.now(UTC).date().isoformat()
    days = {row["date"]: row for row in report["per_day"]}
    assert len(days) >= 2  # both business days appear as rows
    assert all(row["orders"] >= 1 for row in days.values())

    # the fuel actuals: two motorcycle deliveries joined per driver vehicle
    assert report["fuel"] is not None
    assert report["fuel"]["currency"] == "TND"
    assert report["fuel"]["cost"] > 0


def test_cancelled_orders_never_count(authed_client: TestClient):
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    authed_client.post("/drivers", json=DRIVER)
    order = _place(authed_client, restaurant["id"], customer["id"])
    authed_client.post(f"/orders/{order['id']}/status", json={"status": "cancelled"})

    report = authed_client.get(f"/restaurants/{restaurant['id']}/weekly-report").json()
    assert report["delivered_orders"] == 0
    assert report["revenue"] == 0


def test_empty_restaurant_is_zeros_not_an_error(authed_client: TestClient):
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    report = authed_client.get(f"/restaurants/{restaurant['id']}/weekly-report").json()
    assert report["delivered_orders"] == 0
    assert report["revenue"] == 0
    assert report["avg_delivery_minutes"] is None
    assert report["fuel"] is None or report["fuel"]["cost"] == 0
    assert report["rating_average"] is None


def test_cross_tenant_is_the_uniform_404(client: TestClient, authed_client: TestClient, db):
    from core.security import hash_password
    from domain.models import Tenant, User

    restaurant, _ = _place_and_deliver(authed_client)

    db.add(Tenant(slug="acme5", name="Acme5"))
    db.commit()
    acme = db.query(Tenant).filter_by(slug="acme5").one()
    db.add(
        User(username="acme5ops", password_hash=hash_password("pw"), role="ops", tenant_id=acme.id)
    )
    db.commit()
    from tests.integration.test_api_promotions import _headers, _token

    acme_headers = _headers(_token(client, "acme5ops", "pw"))
    assert (
        client.get(
            f"/restaurants/{restaurant['id']}/weekly-report", headers=acme_headers
        ).status_code
        == 404
    )


def _place_and_deliver(client: TestClient) -> tuple[dict, dict]:
    customer = client.post("/customers", json=CUSTOMER).json()
    restaurant = client.post("/restaurants", json=RESTAURANT).json()
    client.post("/drivers", json=DRIVER)
    order = client.post(
        "/orders",
        json={
            "customer_id": customer["id"],
            "restaurant_id": restaurant["id"],
            "dropoff_latitude": CUSTOMER["latitude"],
            "dropoff_longitude": CUSTOMER["longitude"],
            "demand_ratio": 0,
        },
    ).json()
    _deliver(client, order["id"])
    return restaurant, order

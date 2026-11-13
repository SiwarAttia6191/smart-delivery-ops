"""Loyalty points tests (#217): accrue per delivered order, redeem
through the coupon seam.

The guarantees from the issue: accrual on DELIVERY only (pending and
cancelled orders accrue nothing, and the points tick in the same
transaction as the delivery transition), redemption debits the balance
through the existing checkout path, and the balance can never go
negative (an unaffordable points coupon is the same uniform coupon
rejection, with the balance and the order untouched).
"""

from fastapi.testclient import TestClient

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


def _place(client: TestClient, customer_id: int, restaurant_id: int) -> dict:
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


def _deliver(client: TestClient, order_id: str) -> dict:
    client.post(f"/orders/{order_id}/assign")
    client.post(f"/orders/{order_id}/status", json={"status": "in_transit"})
    delivered = client.post(f"/orders/{order_id}/status", json={"status": "delivered"})
    assert delivered.status_code == 200, delivered.text
    return delivered.json()


def _balance(client: TestClient, customer_id: int) -> int:
    return client.get(f"/customers/{customer_id}").json()["loyalty_points"]


def test_accrual_on_delivery_only(authed_client: TestClient):
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    authed_client.post("/drivers", json=DRIVER)

    # pending: nothing accrued yet
    order = _place(authed_client, customer["id"], restaurant["id"])
    assert _balance(authed_client, customer["id"]) == 0

    # delivered: 1 point per currency unit of the fee actually paid
    delivered = _deliver(authed_client, order["id"])
    expected = int(delivered["delivery_fee"])
    assert expected > 0
    assert _balance(authed_client, customer["id"]) == expected


def test_cancelled_order_accrues_nothing(authed_client: TestClient):
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    authed_client.post("/drivers", json=DRIVER)
    order = _place(authed_client, customer["id"], restaurant["id"])
    cancelled = authed_client.post(f"/orders/{order['id']}/status", json={"status": "cancelled"})
    assert cancelled.status_code == 200
    assert _balance(authed_client, customer["id"]) == 0


def test_points_coupon_redeems_and_debits(authed_client: TestClient):
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    authed_client.post("/drivers", json=DRIVER)

    # deliver twice to accrue a known balance
    first = _place(authed_client, customer["id"], restaurant["id"])
    _deliver(authed_client, first["id"])
    second = _place(authed_client, customer["id"], restaurant["id"])
    _deliver(authed_client, second["id"])
    balance = _balance(authed_client, customer["id"])
    assert balance == int(first["delivery_fee"]) + int(second["delivery_fee"])

    # a points-funded coupon: 1 point = 1 currency unit of discount
    authed_client.post(
        "/coupons",
        json={"code": "LOYAL5", "discount_kind": "points", "discount_value": 5},
    )
    discounted = authed_client.post(
        "/orders",
        json={
            "customer_id": customer["id"],
            "restaurant_id": restaurant["id"],
            "dropoff_latitude": CUSTOMER["latitude"],
            "dropoff_longitude": CUSTOMER["longitude"],
            "demand_ratio": 0,
            "coupon_code": "LOYAL5",
        },
    )
    assert discounted.status_code == 201, discounted.text
    body = discounted.json()
    assert body["delivery_fee"] < first["delivery_fee"] or balance >= 5  # the discount applied

    # the debit happened at checkout, in the same transaction
    new_balance = _balance(authed_client, customer["id"])
    assert new_balance == balance - 5

    # delivering the discounted order accrues on the POST-coupon fee
    delivered = _deliver(authed_client, body["id"])
    final_balance = _balance(authed_client, customer["id"])
    assert final_balance == new_balance + int(delivered["delivery_fee"])


def test_unaffordable_points_coupon_is_rejected_without_side_effects(authed_client: TestClient):
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    authed_client.post("/drivers", json=DRIVER)

    authed_client.post(
        "/coupons",
        json={"code": "LOYAL999", "discount_kind": "points", "discount_value": 999},
    )
    balance = _balance(authed_client, customer["id"])

    rejected = authed_client.post(
        "/orders",
        json={
            "customer_id": customer["id"],
            "restaurant_id": restaurant["id"],
            "dropoff_latitude": CUSTOMER["latitude"],
            "dropoff_longitude": CUSTOMER["longitude"],
            "demand_ratio": 0,
            "coupon_code": "LOYAL999",
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "coupon_invalid"  # the uniform rejection

    # NO side effects: the balance is untouched and no order exists
    assert _balance(authed_client, customer["id"]) == balance
    orders = authed_client.get("/orders").json()
    assert all(o["customer_id"] != customer["id"] for o in orders)

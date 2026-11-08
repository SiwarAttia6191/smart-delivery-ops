"""Review tests (#216): the post-purchase rating flow over HTTP.

The guarantees from the issue: the lifecycle guard (no review before
delivered — and the status guard is a 409, not a silent accept), ONE
review per order (a second attempt is a 409 — the database's unique
constraint backs the service), tenant scoping (another tenant's order is
the uniform 404), and the snapshot/portal wiring (rating_average goes
live once reviews exist).
"""

from fastapi.testclient import TestClient

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


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
    client.post(f"/orders/{order['id']}/assign")
    client.post(f"/orders/{order['id']}/status", json={"status": "in_transit"})
    client.post(f"/orders/{order['id']}/status", json={"status": "delivered"})
    return restaurant, order


def test_lifecycle_guard_and_one_review_per_order(authed_client: TestClient):
    customer = authed_client.post("/customers", json=CUSTOMER).json()
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    authed_client.post("/drivers", json=DRIVER)
    order = authed_client.post(
        "/orders",
        json={
            "customer_id": customer["id"],
            "restaurant_id": restaurant["id"],
            "dropoff_latitude": CUSTOMER["latitude"],
            "dropoff_longitude": CUSTOMER["longitude"],
            "demand_ratio": 0,
        },
    ).json()

    # the lifecycle guard: a PENDING order cannot be rated
    early = authed_client.post(
        f"/orders/{order['id']}/review", json={"rating": 5, "comment": "too early"}
    )
    assert early.status_code == 409
    assert "delivered" in early.json()["error"]["message"]

    _deliver(authed_client, order["id"])
    first = authed_client.post(
        f"/orders/{order['id']}/review", json={"rating": 5, "comment": "hot and fast"}
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["rating"] == 5
    assert body["comment"] == "hot and fast"

    # one review per order: the second attempt is a 409, not a duplicate
    second = authed_client.post(f"/orders/{order['id']}/review", json={"rating": 1})
    assert second.status_code == 409
    assert "already reviewed" in second.json()["error"]["message"]


def _deliver(client: TestClient, order_id: str) -> None:
    a = client.post(f"/orders/{order_id}/assign")
    t = client.post(f"/orders/{order_id}/status", json={"status": "in_transit"})
    d = client.post(f"/orders/{order_id}/status", json={"status": "delivered"})
    assert a.status_code == 200, a.text
    assert t.status_code == 200, t.text
    assert d.status_code == 200, d.text


def test_restaurant_reviews_listing_and_average(authed_client: TestClient, db):
    restaurant, order = _place_and_deliver(authed_client)
    authed_client.post(f"/orders/{order['id']}/review", json={"rating": 4, "comment": "solid"})

    body = authed_client.get(f"/restaurants/{restaurant['id']}/reviews").json()
    assert body["restaurant_id"] == restaurant["id"]
    assert body["review_count"] == 1
    assert body["rating_average"] == 4.0
    assert body["reviews"][0]["rating"] == 4
    assert body["reviews"][0]["comment"] == "solid"


def test_rating_average_goes_live_in_the_sales_snapshot(authed_client: TestClient, db):
    """#219's rating_average rode null until reviews landed — it is live now."""
    restaurant, order = _place_and_deliver(authed_client)
    before = authed_client.get(f"/restaurants/{restaurant['id']}/sales-snapshot").json()
    assert before["rating_average"] is None

    authed_client.post(f"/orders/{order['id']}/review", json={"rating": 2})
    after = authed_client.get(f"/restaurants/{restaurant['id']}/sales-snapshot").json()
    assert after["rating_average"] == 2.0


def test_cross_tenant_review_is_the_uniform_404(client: TestClient, authed_client: TestClient, db):
    from core.security import hash_password
    from domain.models import Tenant, User

    restaurant, order = _place_and_deliver(authed_client)

    db.add(Tenant(slug="acme4", name="Acme4"))
    db.commit()
    acme = db.query(Tenant).filter_by(slug="acme4").one()
    db.add(
        User(username="acme4ops", password_hash=hash_password("pw"), role="ops", tenant_id=acme.id)
    )
    db.commit()
    from tests.integration.test_api_promotions import _headers, _token

    acme_headers = _headers(_token(client, "acme4ops", "pw"))
    assert (
        client.post(
            f"/orders/{order['id']}/review",
            json={"rating": 1},
            headers=acme_headers,
        ).status_code
        == 404
    )
    assert (
        client.get(f"/restaurants/{restaurant['id']}/reviews", headers=acme_headers).status_code
        == 404
    )


def test_rating_bounds_are_enforced(authed_client: TestClient):
    restaurant, order = _place_and_deliver(authed_client)
    assert (
        authed_client.post(f"/orders/{order['id']}/review", json={"rating": 0}).status_code == 422
    )
    assert (
        authed_client.post(f"/orders/{order['id']}/review", json={"rating": 6}).status_code == 422
    )
    # an unrated delivered order left no review rows behind
    reviews = authed_client.get(f"/restaurants/{restaurant['id']}/reviews").json()
    assert reviews["review_count"] == 0

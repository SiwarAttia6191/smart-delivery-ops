"""Driver job view (#311): the courier's own surface. The guarantees:
a driver token carries the did claim, me/jobs is courier-scoped, the
driver may advance ONLY their own assigned order and only the
ride-and-deliver transitions (cancel stays with the operator), and the
heartbeat self-scopes the same way. The dispatcher does the assignment —
the courier does the ride."""

import pytest
from fastapi.testclient import TestClient

from core.security import hash_password
from domain.models import Driver, User

CUSTOMER = {"name": "Lucia", "phone": "+216 20 555 777", "latitude": 36.8381, "longitude": 10.2385}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


@pytest.fixture
def driver_row(db) -> Driver:
    return Driver(
        tenant_id=1,
        name=DRIVER["name"],
        vehicle=DRIVER["vehicle"],
        latitude=DRIVER["latitude"],
        longitude=DRIVER["longitude"],
        is_available=True,
    )


@pytest.fixture
def driver_user(db, driver_row) -> User:
    db.add(driver_row)
    db.flush()
    user = User(
        username="driver1",
        password_hash=hash_password("driver1"),
        role="driver",
        tenant_id=1,
        driver_id=driver_row.id,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def seeded_restaurant(authed_client: TestClient) -> dict:
    """A restaurant the orders dispatch FROM."""
    return authed_client.post(
        "/restaurants", json={"name": "Casa Toni", "latitude": 36.8065, "longitude": 10.1815}
    ).json()


@pytest.fixture
def seeded_customer(authed_client: TestClient) -> dict:
    """A real customer row — the order's dropoff identity (#309 companion)."""
    return authed_client.post("/customers", json=CUSTOMER).json()


@pytest.fixture
def driver_client(client: TestClient, driver_user) -> TestClient:
    token = client.post("/auth/token", data={"username": "driver1", "password": "driver1"}).json()[
        "access_token"
    ]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_driver_token_carries_the_did_claim(client, driver_user):
    body = client.post("/auth/token", data={"username": "driver1", "password": "driver1"}).json()
    import jwt as pyjwt

    from core.config import get_settings

    claims = pyjwt.decode(
        body["access_token"], get_settings().auth_jwt_secret, algorithms=["HS256"]
    )
    assert claims["role"] == "driver"
    assert claims["did"] == driver_user.driver_id


def test_me_jobs_is_courier_scoped(
    seeded_customer, seeded_restaurant, client, driver_row, driver_client, authed_client
):
    # two orders: the dispatcher assigns ONE to the only available driver
    # (Karim) — the other stays pending and must NOT appear in his jobs
    order_assigned = authed_client.post(
        "/orders",
        json={
            "customer_id": seeded_customer["id"],
            "restaurant_id": seeded_restaurant["id"],
            "dropoff_address": "Rue de Tunis, Tunis",
            "latitude": 36.83,
            "longitude": 10.19,
        },
    ).json()
    order_pending = authed_client.post(
        "/orders",
        json={
            "customer_id": seeded_customer["id"],
            "restaurant_id": seeded_restaurant["id"],
            "dropoff_address": "Rue de Tunis, Tunis",
            "latitude": 36.83,
            "longitude": 10.19,
        },
    ).json()
    authed_client.post(f"/orders/{order_assigned['id']}/assign")

    jobs = driver_client.get("/drivers/me/jobs")
    assert jobs.status_code == 200
    ids = [o["id"] for o in jobs.json()]
    assert order_assigned["id"] in ids
    assert order_pending["id"] not in ids


def test_driver_advances_their_own_order(
    seeded_customer, seeded_restaurant, client, driver_row, driver_client, authed_client
):
    order = authed_client.post(
        "/orders",
        json={
            "customer_id": seeded_customer["id"],
            "restaurant_id": seeded_restaurant["id"],
            "dropoff_address": "Rue de Tunis, Tunis",
            "latitude": 36.83,
            "longitude": 10.19,
        },
    ).json()
    authed_client.post(f"/orders/{order['id']}/assign")

    transit = driver_client.post(f"/orders/{order['id']}/status", json={"status": "in_transit"})
    assert transit.status_code == 200
    assert transit.json()["status"] == "in_transit"

    delivered = driver_client.post(f"/orders/{order['id']}/status", json={"status": "delivered"})
    assert delivered.status_code == 200
    assert delivered.json()["status"] == "delivered"


def test_driver_cannot_advance_another_couriers_order(
    seeded_customer,
    seeded_restaurant,
    client,
    driver_row,
    driver_user,
    driver_client,
    authed_client,
    db,
):
    # a second fleet member takes the assignment
    other = Driver(
        tenant_id=1,
        name="Sami",
        vehicle="bicycle",
        latitude=36.82,
        longitude=10.19,
        is_available=True,
    )
    db.add(other)
    db.commit()
    order = authed_client.post(
        "/orders",
        json={
            "customer_id": seeded_customer["id"],
            "restaurant_id": seeded_restaurant["id"],
            "dropoff_address": "Rue de Tunis, Tunis",
            "latitude": 36.83,
            "longitude": 10.19,
        },
    ).json()
    # Karim is nearest? force the OTHER driver: make Karim unavailable
    # before the dispatch by disabling him, then re-enable — simpler:
    # dispatch with Karim unavailable picks Sami
    karim = db.get(Driver, driver_row.id)
    karim.is_available = False
    db.commit()
    authed_client.post(f"/orders/{order['id']}/assign")
    karim.is_available = True
    db.commit()

    stolen = driver_client.post(f"/orders/{order['id']}/status", json={"status": "in_transit"})
    assert stolen.status_code == 403
    assert "not assigned to you" in stolen.json()["detail"]


def test_driver_cannot_cancel(
    seeded_customer, seeded_restaurant, client, driver_row, driver_client, authed_client
):
    order_response = authed_client.post(
        "/orders",
        json={
            "customer_id": seeded_customer["id"],
            "restaurant_id": seeded_restaurant["id"],
            "dropoff_address": "Rue de Tunis, Tunis",
            "latitude": 36.83,
            "longitude": 10.19,
        },
    )
    print("DBG:", order_response.status_code, order_response.text[:200])
    order = order_response.json()
    authed_client.post(f"/orders/{order['id']}/assign")
    cancelled = driver_client.post(f"/orders/{order['id']}/status", json={"status": "cancelled"})
    assert cancelled.status_code == 403
    assert "start transit or mark delivered" in cancelled.json()["detail"]


def test_heartbeat_self_scopes(
    seeded_customer,
    seeded_restaurant,
    client,
    driver_row,
    driver_user,
    driver_client,
    authed_client,
):
    # own row: the courier updates location + availability
    own = driver_client.patch(
        f"/drivers/{driver_row.id}",
        json={"latitude": 36.82, "longitude": 10.20, "is_available": False},
    )
    assert own.status_code == 200
    assert own.json()["is_available"] is False
    # another courier's row: forbidden
    other = authed_client.post("/drivers", json=DRIVER | {"name": "Sami"}).json()
    foreign = driver_client.patch(
        f"/drivers/{other['id']}",
        json={"latitude": 36.0, "longitude": 10.0, "is_available": False},
    )
    assert foreign.status_code == 403


def test_viewer_cannot_advance_orders(
    seeded_customer, seeded_restaurant, client, authed_client, db
):
    from domain.models import User

    db.add(
        User(
            username="view",
            password_hash=hash_password("view"),
            role="viewer",
            tenant_id=1,
        )
    )
    db.commit()
    order = authed_client.post(
        "/orders",
        json={
            "customer_id": seeded_customer["id"],
            "restaurant_id": seeded_restaurant["id"],
            "dropoff_address": "Rue de Tunis, Tunis",
            "latitude": 36.83,
            "longitude": 10.19,
        },
    ).json()
    # the viewer trio signs in — role viewer has no transition authority
    vtoken = client.post("/auth/token", data={"username": "view", "password": "view"}).json()[
        "access_token"
    ]
    denied = client.post(
        f"/orders/{order['id']}/status",
        json={"status": "in_transit"},
        headers={"Authorization": f"Bearer {vtoken}"},
    )
    assert denied.status_code == 403

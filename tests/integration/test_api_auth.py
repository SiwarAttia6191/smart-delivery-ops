"""Integration: the auth wall over HTTP (#28).

Posture: mutating endpoints require a bearer token with a writer role;
GETs and the quote computation stay open (demo posture — a production
profile would close reads per role).
"""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from fastapi.testclient import TestClient

from core.config import get_settings
from core.security import hash_password
from domain.models import User

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
ORDER_BODY = {
    "customer_id": 1,
    "restaurant_id": 1,
    "dropoff_latitude": 36.8381,
    "dropoff_longitude": 10.2385,
}


def _seed_users(db, *specs: tuple[str, str, str]) -> None:
    for username, password, role in specs:
        db.add(User(username=username, password_hash=hash_password(password), role=role))
    db.commit()


def _token(client: TestClient, username: str, password: str) -> str:
    response = client.post("/auth/token", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    return body["access_token"]


class TestTokenIssuance:
    def test_valid_credentials_issue_bearer_token(self, client: TestClient, db):
        _seed_users(db, ("ops", "ops", "ops"))
        token = _token(client, "ops", "ops")
        assert token.count(".") == 2  # header.payload.signature

    def test_unknown_user_and_wrong_password_are_indistinguishable(self, client: TestClient, db):
        _seed_users(db, ("ops", "ops", "ops"))
        unknown = client.post("/auth/token", data={"username": "ghost", "password": "x"})
        wrong = client.post("/auth/token", data={"username": "ops", "password": "nope"})
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]

    def test_expired_token_is_rejected_on_write(self, client: TestClient, db):
        _seed_users(db, ("ops", "ops", "ops"))
        now = datetime.now(UTC)
        expired = pyjwt.encode(
            {"sub": "ops", "role": "ops", "exp": now - timedelta(minutes=1)},
            get_settings().auth_jwt_secret,
            algorithm="HS256",
        )
        response = client.post(
            "/orders", json=ORDER_BODY, headers={"Authorization": f"Bearer {expired}"}
        )
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()


class TestWriteWall:
    def test_mutation_without_token_is_401_with_authenticate_header(self, client: TestClient):
        response = client.post("/orders", json=ORDER_BODY)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_viewer_role_is_403_not_401(self, client: TestClient, db):
        _seed_users(db, ("view", "view", "viewer"))
        token = _token(client, "view", "view")
        response = client.post(
            "/orders", json=ORDER_BODY, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403

    def test_ops_role_can_create_and_advance_orders(self, client: TestClient, db):
        _seed_users(db, ("ops", "ops", "ops"))
        headers = {"Authorization": f"Bearer {_token(client, 'ops', 'ops')}"}
        customer = client.post("/customers", json=CUSTOMER, headers=headers).json()
        restaurant = client.post("/restaurants", json=RESTAURANT, headers=headers).json()
        driver = client.post(
            "/drivers",
            json={"name": "Karim", "vehicle": "motorcycle", "latitude": 36.81, "longitude": 10.19},
            headers=headers,
        ).json()
        assert driver["is_available"]
        order = client.post(
            "/orders",
            json={
                "customer_id": customer["id"],
                "restaurant_id": restaurant["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
            },
            headers=headers,
        )
        assert order.status_code == 201
        assigned = client.post(f"/orders/{order.json()['id']}/assign", headers=headers)
        assert assigned.status_code == 200

    def test_garbage_token_is_401(self, client: TestClient):
        response = client.post(
            "/orders", json=ORDER_BODY, headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert response.status_code == 401


class TestOpenReads:
    def test_gets_stay_open_without_token(self, client: TestClient):
        assert client.get("/analytics/summary").status_code == 200
        assert client.get("/config").status_code == 200
        assert client.get("/orders").status_code == 200

    def test_quote_is_open_despite_being_a_post(self, client: TestClient, db):
        from domain.models import Restaurant

        db.add(Restaurant(name="Chez Slah", latitude=36.8065, longitude=10.1815))
        db.commit()
        response = client.post(
            "/pricing/quote",
            json={"restaurant_id": 1, "dropoff_latitude": 36.8381, "dropoff_longitude": 10.2385},
        )
        assert response.status_code == 200

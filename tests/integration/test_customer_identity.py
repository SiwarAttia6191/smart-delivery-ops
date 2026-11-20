"""Customer identity (#309): phone → OTP → customer-scoped JWT →
self-ordered orders. The guarantees: the code is single-use and
attempt-capped, the token carries role `customer` + the `cid` claim,
`POST /orders` overrides the payload's customer_id for customer tokens
(never trusted), `me/orders` is customer-scoped, and staff tokens are
rejected on the customer-owned route."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from domain.models import CustomerOtp

CUSTOMER = {"name": "Aziza", "phone": "+216 20 111 222", "latitude": 36.8381, "longitude": 10.2385}


@pytest.fixture
def seeded_customer(authed_client: TestClient) -> dict:
    return authed_client.post("/customers", json=CUSTOMER).json()


def _issue(client: TestClient, phone: str) -> str:
    response = client.post("/customers/otp", json={"phone": phone})
    assert response.status_code == 201
    body = response.json()
    assert body["sent"] is True
    return body["dev_otp"]


def test_phone_otp_issues_and_verifies_to_a_customer_token(client, seeded_customer):
    code = _issue(client, seeded_customer["phone"])
    verify = client.post(
        "/customers/otp/verify", json={"phone": seeded_customer["phone"], "code": code}
    )
    assert verify.status_code == 200
    body = verify.json()
    assert body["token_type"] == "bearer"
    assert body["customer"]["id"] == seeded_customer["id"]
    # the token's claims: role customer + the cid, decodable without magic
    import jwt as pyjwt

    from core.config import get_settings

    claims = pyjwt.decode(
        body["access_token"], get_settings().auth_jwt_secret, algorithms=["HS256"]
    )
    assert claims["role"] == "customer"
    assert claims["cid"] == seeded_customer["id"]
    assert isinstance(claims.get("tid"), int)  # the tenant binding rides along


def test_the_code_is_single_use(client, seeded_customer):
    code = _issue(client, seeded_customer["phone"])
    first = client.post(
        "/customers/otp/verify", json={"phone": seeded_customer["phone"], "code": code}
    )
    assert first.status_code == 200
    replay = client.post(
        "/customers/otp/verify", json={"phone": seeded_customer["phone"], "code": code}
    )
    assert replay.status_code == 404  # no live challenge left — spent


def test_wrong_code_is_attempt_capped(client, seeded_customer):
    code = _issue(client, seeded_customer["phone"])
    # five wrong guesses: each a 403 otp_invalid (the challenge counts them)
    for _ in range(5):
        bad = client.post(
            "/customers/otp/verify",
            json={"phone": seeded_customer["phone"], "code": "000001"},
        )
        assert bad.status_code == 403
        assert bad.json()["detail"] == "otp_invalid"
    # the cap: even the REAL code is refused now — attempts exhausted
    spent = client.post(
        "/customers/otp/verify", json={"phone": seeded_customer["phone"], "code": code}
    )
    assert spent.status_code == 403
    assert spent.json()["detail"] == "otp_attempts_exhausted"


def test_expired_code_rejected(client, seeded_customer, db):
    _issue(client, seeded_customer["phone"])
    # reach back and age the challenge past its TTL
    otp = db.scalars(
        __import__("sqlalchemy")
        .select(CustomerOtp)
        .where(CustomerOtp.customer_id == seeded_customer["id"])
    ).all()[-1]
    otp.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    expired = client.post(
        "/customers/otp/verify", json={"phone": seeded_customer["phone"], "code": "123456"}
    )
    assert expired.status_code == 403
    assert "expired" in expired.json()["detail"]


def test_unknown_phone_is_a_404(client):
    response = client.post("/customers/otp", json={"phone": "+216 1 000 000"})
    assert response.status_code == 404


def test_customer_token_can_only_order_for_itself(client, seeded_customer, authed_client):
    # seed a second customer: the staff flow can order for either
    other = authed_client.post(
        "/customers",
        json={"name": "Bilel", "phone": "+216 20 333 444", "latitude": 36.80, "longitude": 10.18},
    ).json()
    restaurant = authed_client.post(
        "/restaurants", json={"name": "Chez Slim", "latitude": 36.80, "longitude": 10.18}
    ).json()

    code = _issue(client, seeded_customer["phone"])
    token = client.post(
        "/customers/otp/verify", json={"phone": seeded_customer["phone"], "code": code}
    ).json()["access_token"]

    # the payload CLAIMS the other customer's id — the token wins
    placed = client.post(
        "/orders",
        json={
            "customer_id": other["id"],
            "restaurant_id": restaurant["id"],
            "latitude": seeded_customer["latitude"],
            "longitude": seeded_customer["longitude"],
            "dropoff_address": "Rue de Tunis, Tunis",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert placed.status_code == 201
    assert placed.json()["customer_id"] == seeded_customer["id"]


def test_me_orders_is_customer_scoped_and_staff_rejected(client, seeded_customer, authed_client):
    restaurant = authed_client.post(
        "/restaurants", json={"name": "Dar Jloud", "latitude": 36.80, "longitude": 10.18}
    ).json()
    order_response = authed_client.post(
        "/orders",
        json={
            "customer_id": seeded_customer["id"],
            "restaurant_id": restaurant["id"],
            "dropoff_address": "Rue de Tunis, Tunis",
            "latitude": seeded_customer["latitude"],
            "longitude": seeded_customer["longitude"],
        },
    )
    assert order_response.status_code == 201
    order = order_response.json()

    code = _issue(client, seeded_customer["phone"])
    token = client.post(
        "/customers/otp/verify", json={"phone": seeded_customer["phone"], "code": code}
    ).json()["access_token"]

    mine = client.get("/customers/me/orders", headers={"Authorization": f"Bearer {token}"})
    assert mine.status_code == 200
    body = mine.json()
    assert len(body) == 1
    assert body[0]["id"] == order["id"]
    assert body[0]["events"]  # the timeline rides the same payload the tracking page renders

    # a STAFF token is rejected on the customer-owned route — customer
    # order history is customer-scoped, not staff-readable through it
    staff_view = authed_client.get("/customers/me/orders")
    assert staff_view.status_code == 403

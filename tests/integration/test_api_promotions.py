"""Promotions API (#159 item 3): per-tenant coupons + quote-time discounts.

The guarantees under test: codes are tenant-scoped (same code, two
tenants, two values — and cross-tenant use looks like an unknown code),
rejections collapse to one uniform 422 (no code probing), the quote's
discount line is auditable (total unchanged, final_total = total -
discount), and a quote never ticks the redemption counter.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from core.security import hash_password
from domain.models import Coupon, Tenant, User

QUOTE = {
    "pickup_latitude": 36.8065,
    "pickup_longitude": 10.1815,
    "dropoff_latitude": 36.8381,
    "dropoff_longitude": 10.2385,
    "demand_ratio": 0,
}


def _seed(db, *specs: tuple[str, str, str, int]) -> None:
    for username, password, role, tenant_id in specs:
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                tenant_id=tenant_id,
            )
        )
    db.commit()


def _token(client: TestClient, username: str, password: str) -> str:
    response = client.post("/auth/token", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _coupon_body(**over: object) -> dict:
    body: dict = {"code": "welcome10", "discount_kind": "percent", "discount_value": 10}
    body.update(over)
    return body


class TestCouponManagement:
    def test_create_normalizes_and_lists(self, authed_client: TestClient):
        created = authed_client.post("/coupons", json=_coupon_body())
        assert created.status_code == 201, created.text
        assert created.json()["code"] == "WELCOME10"
        assert created.json()["is_active"] is True
        listed = authed_client.get("/coupons").json()
        assert [c["code"] for c in listed] == ["WELCOME10"]

    def test_duplicate_code_is_409(self, authed_client: TestClient):
        assert authed_client.post("/coupons", json=_coupon_body()).status_code == 201
        dup = authed_client.post("/coupons", json=_coupon_body(code="WELCOME10"))
        assert dup.status_code == 409

    def test_percent_over_100_is_422(self, authed_client: TestClient):
        bad = authed_client.post(
            "/coupons", json=_coupon_body(discount_kind="percent", discount_value=150)
        )
        assert bad.status_code == 422

    def test_anonymous_cannot_create(self, client: TestClient):
        assert client.post("/coupons", json=_coupon_body()).status_code == 401

    def test_viewer_cannot_create(self, client: TestClient, db):
        _seed(db, ("view", "view", "viewer", 1))
        token = _token(client, "view", "view")
        assert (
            client.post("/coupons", json=_coupon_body(), headers=_headers(token)).status_code == 403
        )

    def test_deactivate_then_404_on_unknown(self, authed_client: TestClient):
        created = authed_client.post("/coupons", json=_coupon_body()).json()
        off = authed_client.delete(f"/coupons/{created['id']}")
        assert off.status_code == 200
        assert off.json()["is_active"] is False
        assert authed_client.delete("/coupons/99999").status_code == 404


class TestQuoteDiscounts:
    def test_percent_line_is_auditable(self, authed_client: TestClient):
        assert authed_client.post("/coupons", json=_coupon_body()).status_code == 201
        plain = authed_client.post("/pricing/quote", json=QUOTE).json()
        quoted = authed_client.post("/pricing/quote", json={**QUOTE, "coupon_code": "welcome10"})
        assert quoted.status_code == 200
        body = quoted.json()
        assert body["total"] == plain["total"]  # the fee itself is unchanged
        assert body["coupon_code"] == "WELCOME10"
        assert body["discount"] == round(plain["total"] * 0.10, 2)
        assert body["final_total"] == round(plain["total"] - body["discount"], 2)

    def test_amount_discount_is_capped_at_the_fee(self, authed_client: TestClient):
        authed_client.post(
            "/coupons", json=_coupon_body(code="FLAT5", discount_kind="amount", discount_value=5)
        )
        assert authed_client.post("/pricing/quote", json=QUOTE).json()["total"] >= 5
        body = authed_client.post("/pricing/quote", json={**QUOTE, "coupon_code": "FLAT5"}).json()
        assert body["discount"] == 5.0

        authed_client.post(
            "/coupons",
            json=_coupon_body(code="HUGE", discount_kind="amount", discount_value=9999),
        )
        body = authed_client.post("/pricing/quote", json={**QUOTE, "coupon_code": "HUGE"}).json()
        assert body["discount"] == body["total"]  # never a payout
        assert body["final_total"] == 0.0

    def test_rejections_are_one_uniform_422(
        self, client: TestClient, authed_client: TestClient, db
    ):
        # unknown
        assert (
            client.post("/pricing/quote", json={**QUOTE, "coupon_code": "NOPE"}).status_code == 422
        )
        # inactive
        created = authed_client.post("/coupons", json=_coupon_body(code="OFF")).json()
        authed_client.delete(f"/coupons/{created['id']}")
        # expired + redemption budget spent: planted directly, the API
        # refuses to create either shape
        db.add(
            Coupon(
                tenant_id=1,
                code="OLD",
                discount_kind="percent",
                discount_value=10,
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        db.add(
            Coupon(
                tenant_id=1,
                code="SPENT",
                discount_kind="percent",
                discount_value=10,
                max_redemptions=1,
                times_used=1,
            )
        )
        db.commit()
        for code in ("OFF", "OLD", "SPENT"):
            response = client.post("/pricing/quote", json={**QUOTE, "coupon_code": code})
            assert response.status_code == 422, (code, response.text)
            assert response.json()["detail"] == "coupon code is not valid"

    def test_quote_never_ticks_the_redemption_counter(
        self, client: TestClient, authed_client: TestClient
    ):
        authed_client.post(
            "/coupons",
            json=_coupon_body(
                code="BUDGET", discount_kind="amount", discount_value=1, max_redemptions=1
            ),
        )
        for _ in range(3):
            assert (
                client.post("/pricing/quote", json={**QUOTE, "coupon_code": "BUDGET"}).status_code
                == 200
            )
        listed = authed_client.get("/coupons").json()
        assert listed[0]["times_used"] == 0


class TestTenantScope:
    def test_cross_tenant_code_is_invalid_and_invisible(
        self, client: TestClient, authed_client: TestClient, db
    ):
        _seed(db, ("root", "root", "admin", 1))
        admin = _headers(_token(client, "root", "root"))
        assert client.post(
            "/tenants", json={"slug": "acme", "name": "Acme"}, headers=admin
        ).status_code in (201, 409)
        acme = db.query(Tenant).filter_by(slug="acme").one()

        # tenant 2's coupon, created by tenant 2's ops user
        db.add(
            User(
                username="acme-ops",
                password_hash=hash_password("pw"),
                role="ops",
                tenant_id=acme.id,
            )
        )
        db.commit()
        acme_headers = _headers(_token(client, "acme-ops", "pw"))
        assert (
            client.post(
                "/coupons", json=_coupon_body(code="ACMEONLY"), headers=acme_headers
            ).status_code
            == 201
        )

        # tenant 1 (anonymous default scope) cannot use or see it
        assert (
            client.post("/pricing/quote", json={**QUOTE, "coupon_code": "ACMEONLY"}).status_code
            == 422
        )
        assert "ACMEONLY" not in [c["code"] for c in client.get("/coupons").json()]
        # and tenant 2's ops cannot deactivate tenant 1's coupon either
        t1 = authed_client.post("/coupons", json=_coupon_body(code="MINE")).json()
        assert client.delete(f"/coupons/{t1['id']}", headers=acme_headers).status_code == 404

    def test_same_code_two_tenants_two_values(
        self, client: TestClient, authed_client: TestClient, db
    ):
        _seed(db, ("root", "root", "admin", 1))
        admin = _headers(_token(client, "root", "root"))
        client.post("/tenants", json={"slug": "beta", "name": "Beta"}, headers=admin)
        beta = db.query(Tenant).filter_by(slug="beta").one()
        db.add(
            User(
                username="beta-ops",
                password_hash=hash_password("pw"),
                role="ops",
                tenant_id=beta.id,
            )
        )
        db.commit()
        beta_headers = _headers(_token(client, "beta-ops", "pw"))

        authed_client.post("/coupons", json=_coupon_body(code="PROMO", discount_value=10))
        client.post(
            "/coupons", json=_coupon_body(code="PROMO", discount_value=25), headers=beta_headers
        )

        default_quote = client.post("/pricing/quote", json={**QUOTE, "coupon_code": "PROMO"})
        beta_quote = client.post(
            "/pricing/quote", json={**QUOTE, "coupon_code": "PROMO"}, headers=beta_headers
        )
        default_total = client.post("/pricing/quote", json=QUOTE).json()["total"]
        assert default_quote.json()["discount"] == round(default_total * 0.10, 2)
        assert beta_quote.json()["discount"] == round(default_total * 0.25, 2)

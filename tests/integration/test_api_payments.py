"""Payments integration tests (#159 item 2): the checkout seam over HTTP.

The checkout story end-to-end: an order's fee (coupon-discounted when a
code redeems) settles through the sandbox provider, the receipt is
tenant-scoped, double-charging 409s, and the settlement lands in the
order's event log where the warehouse will consume it.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from core.config import Settings, get_settings
from core.exceptions import PaymentError
from domain.models import OrderEvent
from domain.services import payment_service

CUSTOMER = {"name": "Aziza", "phone": "+216 20 000 000", "latitude": 36.8381, "longitude": 10.2385}
RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}
DRIVER = {"name": "Karim", "vehicle": "motorcycle", "latitude": 36.8100, "longitude": 10.1900}


def _seed(client: TestClient) -> dict:
    customer = client.post("/customers", json=CUSTOMER).json()
    restaurant = client.post("/restaurants", json=RESTAURANT).json()
    client.post("/drivers", json=DRIVER)
    return {"customer": customer, "restaurant": restaurant}


def _place(client: TestClient, seeded: dict, **over: object) -> dict:
    payload = {
        "customer_id": seeded["customer"]["id"],
        "restaurant_id": seeded["restaurant"]["id"],
        "dropoff_latitude": CUSTOMER["latitude"],
        "dropoff_longitude": CUSTOMER["longitude"],
        "demand_ratio": 0,
    }
    payload.update(over)
    response = client.post("/orders", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestSandboxPayment:
    def test_pay_settles_and_logs_the_event(self, authed_client: TestClient):
        seeded = _seed(authed_client)
        order = _place(authed_client, seeded)

        paid = authed_client.post(f"/orders/{order['id']}/pay")
        assert paid.status_code == 201, paid.text
        body = paid.json()
        assert body["status"] == "paid"
        assert body["provider"] == "sandbox"
        assert body["provider_ref"].startswith("sbx_")
        assert body["amount"] == order["delivery_fee"]

        # the settlement rides the order's event log — the warehouse feed
        tracked = authed_client.get(f"/orders/{order['id']}").json()
        paid_events = [e for e in tracked["events"] if e["event_type"] == "paid"]
        assert len(paid_events) == 1
        assert paid_events[0]["payload"]["provider_ref"] == body["provider_ref"]
        assert paid_events[0]["payload"]["amount"] == order["delivery_fee"]

    def test_receipts_list_and_read_back(self, authed_client: TestClient):
        seeded = _seed(authed_client)
        order = _place(authed_client, seeded)
        receipt = authed_client.post(f"/orders/{order['id']}/pay").json()

        listed = authed_client.get("/payments").json()
        assert [p["id"] for p in listed] == [receipt["id"]]
        fetched = authed_client.get(f"/payments/{receipt['id']}").json()
        assert fetched["order_id"] == order["id"]
        assert authed_client.get("/payments/99999").status_code == 404

    def test_double_charge_is_409(self, authed_client: TestClient):
        seeded = _seed(authed_client)
        order = _place(authed_client, seeded)
        assert authed_client.post(f"/orders/{order['id']}/pay").status_code == 201
        again = authed_client.post(f"/orders/{order['id']}/pay")
        assert again.status_code == 409
        assert "already paid" in again.json()["error"]["message"]

    def test_only_pending_orders_are_payable(self, authed_client: TestClient):
        seeded = _seed(authed_client)
        order = _place(authed_client, seeded)
        authed_client.post(f"/orders/{order['id']}/assign")
        late = authed_client.post(f"/orders/{order['id']}/pay")
        assert late.status_code == 409
        assert "pending" in late.json()["error"]["message"]

    def test_unknown_order_is_404(self, authed_client: TestClient):
        assert authed_client.post("/orders/no-such-order/pay").status_code == 404

    def test_anonymous_and_viewer_cannot_pay(
        self, client: TestClient, authed_client: TestClient, db
    ):
        from core.security import hash_password
        from domain.models import User

        seeded = _seed(authed_client)
        order = _place(authed_client, seeded)
        db.add(
            User(username="view", password_hash=hash_password("view"), role="viewer", tenant_id=1)
        )
        db.commit()
        from tests.integration.test_api_promotions import _headers, _token

        assert client.post(f"/orders/{order['id']}/pay").status_code == 401
        viewer = _headers(_token(client, "view", "view"))
        assert client.post(f"/orders/{order['id']}/pay", headers=viewer).status_code == 403


class TestTenantScope:
    def test_other_tenant_receipts_are_invisible(
        self, client: TestClient, authed_client: TestClient, db
    ):
        from core.security import hash_password
        from domain.models import Tenant, User
        from tests.integration.test_api_promotions import _headers, _token

        db.add(Tenant(slug="acme", name="Acme"))
        db.commit()
        acme = db.query(Tenant).filter_by(slug="acme").one()
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

        # acme catalog rows in tenant 2's scope, then place + pay there
        client.post("/customers", json=CUSTOMER, headers=acme_headers)
        client.post("/restaurants", json=RESTAURANT, headers=acme_headers)
        acme_customers = client.get("/customers", headers=acme_headers).json()
        acme_restaurants = client.get("/restaurants", headers=acme_headers).json()
        placed = client.post(
            "/orders",
            json={
                "customer_id": acme_customers[-1]["id"],
                "restaurant_id": acme_restaurants[-1]["id"],
                "dropoff_latitude": CUSTOMER["latitude"],
                "dropoff_longitude": CUSTOMER["longitude"],
                "demand_ratio": 0,
            },
            headers=acme_headers,
        )
        assert placed.status_code == 201, placed.text
        receipt = client.post(f"/orders/{placed.json()['id']}/pay", headers=acme_headers)
        assert receipt.status_code == 201, receipt.text

        # tenant 1 cannot see tenant 2's receipts — not in the list, not by id
        assert receipt.json()["id"] not in [p["id"] for p in authed_client.get("/payments").json()]
        assert authed_client.get(f"/payments/{receipt.json()['id']}").status_code == 404
        # and cannot pay tenant 2's order
        assert authed_client.post(f"/orders/{placed.json()['id']}/pay").status_code == 404


class TestCouponRedemptionAtCheckout:
    def test_order_redeems_the_code_and_ticks_the_counter(self, authed_client: TestClient):
        seeded = _seed(authed_client)
        assert (
            authed_client.post(
                "/coupons",
                json={"code": "CHECKOUT10", "discount_kind": "percent", "discount_value": 10},
            ).status_code
            == 201
        )

        list_fee = _place(authed_client, seeded)["delivery_fee"]
        discounted = _place(authed_client, seeded, coupon_code="checkout10")

        assert discounted["delivery_fee"] == round(list_fee * 0.90, 2)
        coupon = authed_client.get("/coupons").json()[0]
        assert coupon["code"] == "CHECKOUT10"
        assert coupon["times_used"] == 1  # the tick the quote never does

        tracked = authed_client.get(f"/orders/{discounted['id']}").json()
        created = next(e for e in tracked["events"] if e["event_type"] == "created")
        assert created["payload"]["coupon_code"] == "CHECKOUT10"
        assert created["payload"]["list_fee"] == list_fee
        assert created["payload"]["coupon_discount"] == round(list_fee * 0.10, 2)

        # and the discounted fee is what the sandbox charges
        paid = authed_client.post(f"/orders/{discounted['id']}/pay").json()
        assert paid["amount"] == discounted["delivery_fee"]

    def test_rejections_are_one_uniform_422(self, authed_client: TestClient, db):
        from domain.models import Coupon

        seeded = _seed(authed_client)
        # inactive (deactivated through the API); the expired / budget-spent
        # shapes are planted directly — the API refuses to CREATE either
        created = authed_client.post(
            "/coupons", json={"code": "OFF", "discount_kind": "percent", "discount_value": 10}
        ).json()
        authed_client.delete(f"/coupons/{created['id']}")
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

        for code in ("NOPE", "OFF", "SPENT"):
            response = authed_client.post(
                "/orders",
                json={
                    "customer_id": seeded["customer"]["id"],
                    "restaurant_id": seeded["restaurant"]["id"],
                    "dropoff_latitude": CUSTOMER["latitude"],
                    "dropoff_longitude": CUSTOMER["longitude"],
                    "demand_ratio": 0,
                    "coupon_code": code,
                },
            )
            assert response.status_code == 422, (code, response.text)
            assert response.json()["error"]["message"] == "coupon code is not valid"

    def test_failed_redemption_does_not_tick(self, authed_client: TestClient):
        seeded = _seed(authed_client)
        authed_client.post(
            "/coupons",
            json={"code": "BUDGET", "discount_kind": "percent", "discount_value": 10},
        )
        assert (
            authed_client.post(
                "/orders",
                json={
                    "customer_id": seeded["customer"]["id"],
                    "restaurant_id": seeded["restaurant"]["id"],
                    "dropoff_latitude": CUSTOMER["latitude"],
                    "dropoff_longitude": CUSTOMER["longitude"],
                    "demand_ratio": 0,
                    "coupon_code": "NOPE",
                },
            ).status_code
            == 422
        )
        coupon = authed_client.get("/coupons").json()[0]
        assert coupon["times_used"] == 0


class TestStripeProvider:
    """The Stripe TEST-mode provider (#215): the SAME seam with real-API
    semantics, proven over a stubbed httpx transport — deterministic,
    zero network, zero money. The live test-mode check is deferred to a
    Stripe account (test mode itself needs no funding)."""

    @pytest.fixture(autouse=True)
    def _stripe_settings(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "payment_provider", "stripe")
        monkeypatch.setattr(get_settings(), "stripe_api_key", "sk_test_5fakesecretkey")

    @staticmethod
    def _stripe_transport(handler) -> httpx.MockTransport:
        return httpx.MockTransport(handler)

    def test_pay_through_stripe_logs_a_pi_receipt(self, authed_client, db, monkeypatch):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path, request.read()))
            if request.url.path.endswith("/payment_intents"):
                return httpx.Response(
                    200, json={"id": "pi_test_1234567890", "status": "requires_confirmation"}
                )
            assert "pm_card_visa" in calls[-1][2].decode()
            return httpx.Response(200, json={"id": "pi_test_1234567890", "status": "succeeded"})

        monkeypatch.setattr(
            payment_service,
            "resolve_provider",
            lambda: payment_service.StripeProvider(transport=self._stripe_transport(handler)),
        )
        seeded = _seed(authed_client)
        order = _place(authed_client, seeded)

        paid = authed_client.post(f"/orders/{order['id']}/pay")
        assert paid.status_code == 201, paid.text
        body = paid.json()
        assert body["provider"] == "stripe"
        assert body["provider_ref"] == "pi_test_1234567890"  # the receipt IS the intent id
        assert body["status"] == "paid"

        # the intent was created for the fee in the SMALLEST currency unit
        method, path, _ = calls[0]
        assert (method, path) == ("POST", "/v1/payment_intents")
        assert f"amount={int(round(order['delivery_fee'] * 100))}".encode() in calls[0][2]

        # the acceptance: the pi_ receipt rides the order's event log
        event = db.query(OrderEvent).filter_by(order_id=order["id"], event_type="paid").one()
        assert event.payload["provider_ref"] == "pi_test_1234567890"

    def test_declined_card_maps_to_payment_error_409(self, authed_client, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/confirm"):
                return httpx.Response(
                    402,
                    json={"error": {"code": "card_declined", "decline_code": "insufficient_funds"}},
                )
            return httpx.Response(
                200, json={"id": "pi_test_declined", "status": "requires_confirmation"}
            )

        monkeypatch.setattr(
            payment_service,
            "resolve_provider",
            lambda: payment_service.StripeProvider(transport=self._stripe_transport(handler)),
        )
        seeded = _seed(authed_client)
        order = _place(authed_client, seeded)

        paid = authed_client.post(f"/orders/{order['id']}/pay")
        assert paid.status_code == 409
        assert paid.json()["error"]["code"] == "payment_rejected"
        assert "insufficient_funds" in paid.json()["error"]["message"]
        # nothing was recorded — a declined charge is not a payment
        assert authed_client.get("/payments").json() == []

    def test_stripe_outage_maps_to_payment_error(self, authed_client, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"message": "upstream error"}})

        monkeypatch.setattr(
            payment_service,
            "resolve_provider",
            lambda: payment_service.StripeProvider(transport=self._stripe_transport(handler)),
        )
        seeded = _seed(authed_client)
        order = _place(authed_client, seeded)
        paid = authed_client.post(f"/orders/{order['id']}/pay")
        assert paid.status_code == 409
        assert paid.json()["error"]["code"] == "payment_rejected"

    def test_provider_refuses_non_positive_amount(self, authed_client, monkeypatch):
        monkeypatch.setattr(
            payment_service, "resolve_provider", lambda: payment_service.StripeProvider()
        )
        with pytest.raises(PaymentError):
            payment_service.StripeProvider().charge(0, reference="order-x")

    def test_settings_refuse_stripe_without_a_key(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "stripe_api_key", None)
        with pytest.raises(ValueError):
            Settings(payment_provider="stripe", stripe_api_key=None)

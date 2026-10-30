"""Integration: tenant isolation over HTTP (#36).

What SQLite CAN prove here: tenant onboarding is admin-only, the JWT
carries the issuing user's tenant binding, a token'd write stamps the
CALLER's tenant (the funnel + middleware wiring end-to-end), per-tenant
pricing configuration round-trips, and the by-tenant breakdown slices.
What SQLite CANNOT prove: read/write enforcement by the database engine —
that is RLS, proven on real Postgres in scripts/data_plane_smoke.py (CI)
and tests/integration/test_rls_postgres.py.
"""

from fastapi.testclient import TestClient

from core.security import decode_access_token, hash_password
from domain.models import Tenant, User

CUSTOMER_A = {"name": "Aziza", "latitude": 36.8381, "longitude": 10.2385}


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


class TestTenantOnboarding:
    def test_admin_onboards_a_second_tenant(self, client: TestClient, db):
        _seed(db, ("root", "root", "admin", 1))
        response = client.post(
            "/tenants",
            json={"slug": "acme-express", "name": "Acme Express", "city": "tunis"},
            headers=_headers(_token(client, "root", "root")),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["slug"] == "acme-express"
        assert body["pricing_overrides"] is None

    def test_duplicate_slug_is_409(self, client: TestClient, db):
        _seed(db, ("root", "root", "admin", 1))
        headers = _headers(_token(client, "root", "root"))
        first = client.post("/tenants", json={"slug": "acme", "name": "Acme"}, headers=headers)
        second = client.post(
            "/tenants", json={"slug": "acme", "name": "Acme again"}, headers=headers
        )
        assert first.status_code == 201
        assert second.status_code == 409

    def test_ops_and_anonymous_cannot_onboard_or_list(self, client: TestClient, db):
        _seed(db, ("ops", "ops", "ops", 1))
        token = _token(client, "ops", "ops")
        assert (
            client.post(
                "/tenants", json={"slug": "x", "name": "X"}, headers=_headers(token)
            ).status_code
            == 403
        )
        assert client.post("/tenants", json={"slug": "x", "name": "X"}).status_code == 401
        assert client.get("/tenants").status_code == 401
        assert client.get("/tenants", headers=_headers(token)).status_code == 403


class TestTokenTenantBinding:
    def test_token_carries_the_users_tenant(self, client: TestClient, db):
        _seed(db, ("ops-a", "pw", "ops", 1))
        claims = decode_access_token(_token(client, "ops-a", "pw"))
        assert claims["tid"] == 1
        assert claims["tenant"] == "default"

    def test_write_is_stamped_with_the_callers_tenant(self, client: TestClient, db):
        """The funnel+middleware wiring, end to end: a tenant-B user's
        customer is created as tenant B's row (on Postgres RLS would ALSO
        enforce this at the engine — here the column tells the story)."""
        _seed(db, ("ops-b", "pw", "ops", 2))
        db.add(Tenant(id=2, slug="acme", name="Acme"))
        db.commit()
        response = client.post(
            "/customers", json=CUSTOMER_A, headers=_headers(_token(client, "ops-b", "pw"))
        )
        assert response.status_code == 201, response.text
        from domain.models import Customer

        row = db.get(Customer, response.json()["id"])
        db.refresh(row)
        assert row.tenant_id == 2


class TestPerTenantPricing:
    def test_config_roundtrip_and_effect_on_quotes(self, client: TestClient, db):
        _seed(db, ("ops", "ops", "ops", 1))
        headers = _headers(_token(client, "ops", "ops"))

        client.get("/pricing/config")
        put = client.put(
            "/pricing/config", json={"overrides": {"pricing_base_fee": 9.5}}, headers=headers
        )
        assert put.status_code == 200, put.text
        assert put.json()["base_fee"] == 9.5
        assert client.get("/pricing/config", headers=headers).json()["base_fee"] == 9.5

        quote = client.post(
            "/pricing/quote",
            json={
                "pickup_latitude": 36.8065,
                "pickup_longitude": 10.1815,
                "dropoff_latitude": 36.8381,
                "dropoff_longitude": 10.2385,
                "demand_ratio": 0,
            },
        )
        assert quote.json()["base_fee"] == 9.5
        # anonymous callers resolve through the same funnel: the default
        # tenant's configuration IS the demo deployment's configuration
        assert (
            client.post(
                "/pricing/quote",
                json={
                    "pickup_latitude": 36.8065,
                    "pickup_longitude": 10.1815,
                    "dropoff_latitude": 36.8381,
                    "dropoff_longitude": 10.2385,
                    "demand_ratio": 0,
                },
            ).json()["base_fee"]
            == 9.5
        )

    def test_unknown_override_key_is_422(self, client: TestClient, db):
        _seed(db, ("ops", "ops", "ops", 1))
        response = client.put(
            "/pricing/config",
            json={"overrides": {"database_url": "evil"}},
            headers=_headers(_token(client, "ops", "ops")),
        )
        assert response.status_code == 422
        db.expire_all()
        assert db.get(Tenant, 1).pricing_overrides is None

    def test_viewer_cannot_set_overrides(self, client: TestClient, db):
        _seed(db, ("view", "view", "viewer", 1))
        response = client.put(
            "/pricing/config",
            json={"overrides": {"pricing_base_fee": 1.0}},
            headers=_headers(_token(client, "view", "view")),
        )
        assert response.status_code == 403


class TestByTenantBreakdown:
    def test_one_slice_per_tenant(self, client: TestClient, db):
        _seed(db, ("ops", "ops", "ops", 1))
        db.add(Tenant(id=2, slug="acme", name="Acme"))
        db.commit()
        response = client.get("/analytics/by-tenant")
        assert response.status_code == 200
        slices = response.json()
        assert [s["tenant_slug"] for s in slices] == ["default", "acme"]
        assert all(s["orders_total"] == 0 for s in slices)

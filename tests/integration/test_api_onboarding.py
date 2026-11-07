"""Onboarding wizard endpoint tests (#224, R2): staff issuance + readiness.

The guarantees: a writer can mint ops/viewer for THEIR tenant (never
admin — no escalation through the wizard), generated passwords are
echoed exactly once, re-issuing an existing username reads as `exists`
(not a failure), and the readiness checklist reflects what the tenant
actually has — restaurants, dispatchable drivers, menu coverage, the
copilot's knowledge corpus, staff accounts.
"""

from domain.models import Tenant, User


class TestStaffIssuance:
    def test_writer_issues_ops_and_viewer(self, authed_client, db):
        response = authed_client.post(
            "/onboarding/staff",
            json={
                "users": [
                    {"username": "maria", "role": "ops"},
                    {"username": "pablo", "role": "viewer"},
                ]
            },
        )
        assert response.status_code == 200
        issued = response.json()["issued"]
        assert [u["status"] for u in issued] == ["created", "created"]
        assert all(len(u["password"]) >= 10 for u in issued)  # generated, shown once

        usernames = {u.username for u in db.query(User).all() if u.role in ("ops", "viewer")}
        assert {"maria", "pablo"} <= usernames

    def test_reissue_reads_as_exists(self, authed_client):
        authed_client.post(
            "/onboarding/staff", json={"users": [{"username": "maria", "role": "ops"}]}
        )
        second = authed_client.post(
            "/onboarding/staff", json={"users": [{"username": "maria", "role": "ops"}]}
        )
        assert second.status_code == 200
        assert second.json()["issued"][0]["status"] == "exists"
        assert second.json()["issued"][0]["password"] is None  # not re-echoed

    def test_writer_cannot_mint_admin(self, authed_client):
        response = authed_client.post(
            "/onboarding/staff", json={"users": [{"username": "sneaky", "role": "admin"}]}
        )
        assert response.status_code == 400

    def test_staff_lands_in_the_caller_s_tenant(self, authed_client, db):
        db.add(Tenant(slug="acme2", name="Acme2"))
        db.commit()
        authed_client.post(
            "/onboarding/staff", json={"users": [{"username": "tenant1ops", "role": "ops"}]}
        )
        user = db.query(User).filter_by(username="tenant1ops").one()
        assert user.tenant_id == 1  # the demo trio's tenant


class TestReadiness:
    def test_empty_tenant_is_not_ready(self, authed_client):
        body = authed_client.get("/onboarding/readiness").json()
        assert body["ready"] is False
        sections = {s["key"]: s for s in body["sections"]}
        assert set(sections) == {"restaurants", "drivers", "menu", "knowledge", "users"}
        assert sections["restaurants"]["ok"] is False

    def test_imports_flip_the_checklist(self, authed_client):
        from tests.integration.test_api_imports import CSV_RESTAURANTS

        authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        rid = authed_client.get("/restaurants").json()[0]["id"]
        rid2 = authed_client.get("/restaurants").json()[1]["id"]
        authed_client.post(
            "/import/drivers",
            json=[{"name": "Carlos", "vehicle": "motorcycle", "address": "Plaza Mayor 1, Madrid"}],
        )
        authed_client.post(
            "/import/menu-items",
            json=[
                {"restaurant_id": rid, "name": "Croqueta", "price": 4.5},
                {"restaurant_id": rid2, "name": "Tortilla", "price": 5.5},
            ],
        )
        authed_client.post(
            "/onboarding/staff", json={"users": [{"username": "maria2", "role": "ops"}]}
        )
        # the copilot's knowledge corpus via the real upload path
        authed_client.post(
            "/knowledge/documents",
            json={"title": "runbook", "content": "How to run the delivery floor."},
        )

        body = authed_client.get("/onboarding/readiness").json()
        sections = {s["key"]: s for s in body["sections"]}
        assert sections["restaurants"]["ok"] is True
        assert sections["drivers"]["ok"] is True
        assert sections["menu"]["ok"] is True
        assert sections["knowledge"]["ok"] is True
        assert sections["users"]["ok"] is True
        assert body["ready"] is True

    def test_menu_coverage_flags_the_uncovered_restaurant(self, authed_client):
        from tests.integration.test_api_imports import CSV_RESTAURANTS

        authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        rid = authed_client.get("/restaurants").json()[0]["id"]
        authed_client.post(
            "/import/menu-items",
            json=[{"restaurant_id": rid, "name": "Croqueta", "price": 4.5}],
        )
        body = authed_client.get("/onboarding/readiness").json()
        menu = next(s for s in body["sections"] if s["key"] == "menu")
        assert menu["ok"] is False  # 1 of 2 restaurants covered
        assert "no menu" in menu["detail"]

"""Menu API integration tests (#159 item 4): the portal's CRUD surface.

The guarantees: menu items are tenant+restaurant scoped (cross-tenant
reads and writes are uniform 404s), the auth wall matches the rest of the
API (open GET, writer mutations), availability flips are the "sold out"
mechanism, and deletion is a real removal.
"""

from fastapi.testclient import TestClient

RESTAURANT = {"name": "Chez Slah", "latitude": 36.8065, "longitude": 10.1815}


def _restaurant(client: TestClient) -> dict:
    response = client.post("/restaurants", json=RESTAURANT)
    assert response.status_code == 201, response.text
    return response.json()


def _item_body(**over: object) -> dict:
    body: dict = {"name": "Lablabi", "price": 6.5}
    body.update(over)
    return body


class TestMenuCrud:
    def test_create_list_patch_delete(self, authed_client: TestClient):
        restaurant = _restaurant(authed_client)

        created = authed_client.post(
            f"/restaurants/{restaurant['id']}/menu-items", json=_item_body()
        )
        assert created.status_code == 201, created.text
        item = created.json()
        assert item["price"] == 6.5 and item["is_available"] is True

        listed = authed_client.get(f"/restaurants/{restaurant['id']}/menu-items").json()
        assert [i["id"] for i in listed] == [item["id"]]

        sold_out = authed_client.patch(
            f"/menu-items/{item['id']}", json={"is_available": False, "price": 7.0}
        )
        assert sold_out.status_code == 200
        assert sold_out.json()["is_available"] is False
        assert sold_out.json()["price"] == 7.0

        removed = authed_client.delete(f"/menu-items/{item['id']}")
        assert removed.status_code == 200
        assert authed_client.get(f"/restaurants/{restaurant['id']}/menu-items").json() == []

    def test_validation_and_unknown_restaurant(self, authed_client: TestClient):
        assert (
            authed_client.post("/restaurants/9999/menu-items", json=_item_body()).status_code == 404
        )
        bad_price = authed_client.post("/restaurants/9999/menu-items", json=_item_body(price=0))
        assert bad_price.status_code == 422  # pydantic gt=0 fires before the 404 lookup

    def test_patch_and_delete_unknown_item_404(self, authed_client: TestClient):
        assert authed_client.patch("/menu-items/9999", json={"price": 1}).status_code == 404
        assert authed_client.delete("/menu-items/9999").status_code == 404

    def test_anonymous_and_viewer_cannot_mutate(
        self, client: TestClient, authed_client: TestClient, db
    ):
        from core.security import hash_password
        from domain.models import User
        from tests.integration.test_api_promotions import _headers, _token

        restaurant = _restaurant(authed_client)
        item = authed_client.post(
            f"/restaurants/{restaurant['id']}/menu-items", json=_item_body()
        ).json()
        db.add(
            User(username="view", password_hash=hash_password("view"), role="viewer", tenant_id=1)
        )
        db.commit()
        viewer = _headers(_token(client, "view", "view"))

        assert (
            client.post(
                f"/restaurants/{restaurant['id']}/menu-items", json=_item_body()
            ).status_code
            == 401
        )
        assert (
            client.patch(f"/menu-items/{item['id']}", json={"price": 1}, headers=viewer).status_code
            == 403
        )
        assert client.delete(f"/menu-items/{item['id']}", headers=viewer).status_code == 403
        # reads stay open (demo posture)
        assert client.get(f"/restaurants/{restaurant['id']}/menu-items").status_code == 200


class TestTenantScope:
    def test_cross_tenant_is_uniform_404(self, client: TestClient, authed_client: TestClient, db):
        from core.security import hash_password
        from domain.models import Tenant, User
        from tests.integration.test_api_promotions import _headers, _token

        # tenant 1's restaurant + menu item
        restaurant = _restaurant(authed_client)
        item = authed_client.post(
            f"/restaurants/{restaurant['id']}/menu-items", json=_item_body()
        ).json()

        # tenant 2's ops user
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
        acme = _headers(_token(client, "acme-ops", "pw"))

        # reads: invisible, not forbidden
        assert (
            client.get(f"/restaurants/{restaurant['id']}/menu-items", headers=acme).status_code
            == 404
        )
        # writes: uniform 404, identical to an unknown id — no probing
        assert (
            client.post(
                f"/restaurants/{restaurant['id']}/menu-items", json=_item_body(), headers=acme
            ).status_code
            == 404
        )
        assert (
            client.patch(f"/menu-items/{item['id']}", json={"price": 1}, headers=acme).status_code
            == 404
        )
        assert client.delete(f"/menu-items/{item['id']}", headers=acme).status_code == 404

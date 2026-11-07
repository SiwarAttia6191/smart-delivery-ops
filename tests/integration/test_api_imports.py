"""Bulk import tests (#223, Onboarding R1).

The guarantees from the issue: happy path (rows imported, addresses
geocoded), the bad-row report (all-or-nothing: one failed row rejects the
FILE, the report names the row and why, nothing committed), geocode
failure handling (unresolvable → row failure; provider down → 503) and
tenant scoping. The provider is monkeypatched at urllib level — the
suite never touches the network, and the pacing sleep is pinned to zero.
"""

import json
import urllib.error
import urllib.request

import pytest

from core.config import get_settings
from domain.models import Restaurant

NOMINATIM_MAP = {
    "calle gran vía 42, madrid": (40.4214, -3.7038, "Gran Vía, 42, Centro, Madrid, España"),
    "plaza mayor 1, madrid": (40.4150, -3.7066, "Plaza Mayor, Centro, Madrid, España"),
    "paseo del prado 8, madrid": (40.4131, -3.6938, "Paseo del Prado, 8, Madrid, España"),
    "calle fuencarral 57, madrid": (
        40.4242,
        -3.7009,
        "Mercado de San Ildefonso, 57, Calle de Fuencarral, Chueca, Madrid, España",
    ),
    "calle de la montera 5, madrid": (
        40.4174,
        -3.7031,
        "5, Calle de la Montera, Sol, Centro, Madrid, 28013, España",
    ),
}


class _Response:
    def __init__(self, query: str):
        key = query.strip().lower()
        if key in NOMINATIM_MAP:
            lat, lon, name = NOMINATIM_MAP[key]
            payload = [{"lat": str(lat), "lon": str(lon), "display_name": name}]
        else:
            payload = []
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


@pytest.fixture(autouse=True)
def fake_geocoder(monkeypatch):
    """Offline provider + zero pacing — the suite never sleeps or hits HTTP."""
    monkeypatch.setattr(get_settings(), "import_geocode_pace_seconds", 0)

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        query = url.split("q=")[1].split("&")[0]
        from urllib.parse import unquote_plus

        return _Response(unquote_plus(query))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


CSV_RESTAURANTS = (
    "name,address,is_active\n"
    'Casa Aurelio,"Calle Gran Vía 42, Madrid",true\n'
    'Taberna Sol,"Plaza Mayor 1, Madrid",true\n'
)


class TestHappyPath:
    def test_csv_import_geocodes_and_persists(self, authed_client, db):
        response = authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["imported"] == 2
        assert body["created"] == 2
        assert all(r["status"] == "created" for r in body["report"])
        first = body["report"][0]
        assert first["latitude"] == 40.4214
        assert first["resolved_address"].startswith("Gran Vía")

        restaurants = authed_client.get("/restaurants").json()
        names = {r["name"] for r in restaurants}
        assert {"Casa Aurelio", "Taberna Sol"} <= names

    def test_json_import_and_cache_reuse(self, authed_client):
        # first import pays the live call...
        first = authed_client.post(
            "/import/restaurants",
            json=[{"name": "Taberna Sol", "address": "Plaza Mayor 1, Madrid"}],
        )
        assert first.status_code == 201
        assert first.json()["report"][0]["cached"] is False

        # ...a LATER file reusing the SAME address is served from the
        # cache — the client pays only for genuinely new addresses
        second = authed_client.post(
            "/import/customers",
            json=[{"name": "Ana", "phone": "+34 600", "address": "plaza mayor 1, madrid"}],
        )
        assert second.status_code == 201
        assert second.json()["report"][0]["cached"] is True
        assert second.json()["report"][0]["resolved_address"].startswith("Plaza Mayor")


class TestAllOrNothing:
    def test_bad_row_rejects_file_and_reports_it(self, authed_client, db):
        before = len(authed_client.get("/restaurants").json())
        csv_file = CSV_RESTAURANTS + "\n,missing name and address,true\n"  # row 3: empty name
        response = authed_client.post(
            "/import/restaurants",
            content=csv_file.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "import_rejected"
        failed_rows = [r for r in error["details"]["report"] if r["status"] == "failed"]
        assert len(failed_rows) == 1
        assert failed_rows[0]["row"] == 3
        assert "name" in failed_rows[0]["detail"]

        # all-or-nothing: the two GOOD rows were NOT imported either
        assert len(authed_client.get("/restaurants").json()) == before

    def test_unresolvable_address_is_a_row_failure(self, authed_client):
        csv_file = (
            "name,address\n"
            'Casa Aurelio,"Calle Gran Vía 42, Madrid"\n'
            "Casa Fantasma,qqzzz nowhere street 00000\n"
        )
        response = authed_client.post(
            "/import/restaurants",
            content=csv_file.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert response.status_code == 422
        failed = [
            r for r in response.json()["error"]["details"]["report"] if r["status"] == "failed"
        ]
        assert failed[0]["row"] == 2
        assert "address not found" in failed[0]["detail"]

    def test_provider_down_maps_to_503(self, authed_client, monkeypatch):
        def failing_urlopen(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
        response = authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "geocoder_unavailable"

    def test_menu_rows_must_reference_an_owned_restaurant(self, authed_client):
        # import a restaurant, then menu rows — one for it, one for a
        # restaurant id that does not exist in this tenant
        authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        rid = authed_client.get("/restaurants").json()[0]["id"]
        rows = [
            {"restaurant_id": rid, "name": "Croqueta", "price": 4.5},
            {"restaurant_id": 99999, "name": "Fantasía", "price": 9.0},
        ]
        response = authed_client.post("/import/menu-items", json=rows)
        assert response.status_code == 422
        failed = [
            r for r in response.json()["error"]["details"]["report"] if r["status"] == "failed"
        ]
        assert failed[0]["row"] == 2
        assert "not found in your tenant" in failed[0]["detail"]
        # all-or-nothing: the good menu row did not land either
        items = authed_client.get(f"/restaurants/{rid}/menu-items").json()
        assert items == []


class TestScopingAndShape:
    def test_imports_land_in_the_importer_s_tenant(self, client, authed_client, db):
        from core.security import hash_password
        from domain.models import Tenant, User

        authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )

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
        from tests.integration.test_api_promotions import _headers, _token

        acme_headers = _headers(_token(client, "acme-ops", "pw"))
        acme_import = client.post(
            "/import/restaurants",
            content=b'name,address\nAcme Diner,"Plaza Mayor 1, Madrid"\n',
            headers={**acme_headers, "Content-Type": "text/csv"},
        )
        assert acme_import.status_code == 201

        # the WRITE side of tenancy: every row carries the IMPORTER's
        # tenant_id (the read side is the engine's RLS — test_rls_postgres)
        tenants = {r.name: r.tenant_id for r in db.query(Restaurant).all()}
        assert tenants["Acme Diner"] == acme.id
        assert tenants["Casa Aurelio"] != acme.id

    def test_unknown_entity_and_bad_bodies(self, authed_client):
        assert authed_client.post("/import/ufo", json=[{"name": "x"}]).status_code == 404
        # a header-only CSV parses to zero rows → rejected
        assert (
            authed_client.post(
                "/import/restaurants",
                content=b"not,valid,csv",
                headers={"Content-Type": "text/csv"},
            ).status_code
            == 422
        )
        wrong = authed_client.post(
            "/import/restaurants",
            json={"not": "a list"},
        )
        assert wrong.status_code == 422

    def test_driver_import_with_vehicle(self, authed_client):
        rows = [{"name": "Carlos", "vehicle": "motorcycle", "address": "Plaza Mayor 1, Madrid"}]
        response = authed_client.post("/import/drivers", json=rows)
        assert response.status_code == 201
        drivers = authed_client.get("/drivers").json()
        assert any(d["name"] == "Carlos" for d in drivers)


class TestPreview:
    """The wizard's map preview (#224): Phase 1 only — the full file's
    fate comes back (pending/failed), NOTHING entity-wise commits."""

    def test_preview_reports_every_row_and_commits_nothing(self, authed_client):
        before = len(authed_client.get("/restaurants").json())
        csv_file = CSV_RESTAURANTS + "Casa Fantasma,qqzzz nowhere street 00000\n"
        response = authed_client.post(
            "/import/restaurants/preview",
            content=csv_file.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["would_import"] == 2
        statuses = {r["row"]: r["status"] for r in body["report"]}
        assert statuses == {1: "pending", 2: "pending", 3: "failed"}
        pending = [r for r in body["report"] if r["status"] == "pending"]
        assert all(r["latitude"] is not None for r in pending)  # pins for the map

        # NOTHING landed — the preview is not a silent import
        assert len(authed_client.get("/restaurants").json()) == before

    def test_menu_preview_flags_foreign_tenant_restaurant(self, authed_client):
        response = authed_client.post(
            "/import/menu-items/preview",
            json=[{"restaurant_id": 424242, "name": "Fantasía", "price": 9.0}],
        )
        assert response.status_code == 200
        assert response.json()["report"][0]["status"] == "failed"
        assert "not found in your tenant" in response.json()["report"][0]["detail"]


class TestIdempotentReimports:
    """R3 (#225): a re-sent file UPDATES by natural key instead of
    duplicating — the same file twice reports `unchanged`, a corrected
    file reports `updated`, and only genuinely new rows are `created`."""

    def test_same_file_twice_is_all_unchanged(self, authed_client, db):
        first = authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert first.json()["created"] == 2
        before = len(authed_client.get("/restaurants").json())

        second = authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert second.status_code == 201
        body = second.json()
        assert body["created"] == 0
        assert body["updated"] == 0
        assert body["unchanged"] == 2
        assert all(r["status"] == "unchanged" for r in body["report"])
        # zero duplicates
        assert len(authed_client.get("/restaurants").json()) == before

    def test_corrected_file_updates_in_place(self, authed_client, db):
        authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        # same NAMES (the natural key), corrected address + availability
        corrected = (
            "name,address,is_active\n"
            'Casa Aurelio,"Calle Fuencarral 57, Madrid",false\n'
            'Taberna Sol,"Plaza Mayor 1, Madrid",true\n'
            'Nuevo Rincón,"Calle Gran Vía 42, Madrid",true\n'
        )
        response = authed_client.post(
            "/import/restaurants",
            content=corrected.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        body = response.json()
        assert body["created"] == 1  # Nuevo Rincón
        assert body["updated"] == 1  # Casa Aurelio (address + is_active changed)
        assert body["unchanged"] == 1  # Taberna Sol (identical)
        statuses = {r["row"]: r["status"] for r in body["report"]}
        assert statuses == {1: "updated", 2: "unchanged", 3: "created"}

        aurelio = next(
            r for r in authed_client.get("/restaurants").json() if r["name"] == "Casa Aurelio"
        )
        assert aurelio["latitude"] == 40.4242  # the corrected address's coords
        assert aurelio["is_active"] is False
        # still exactly one Casa Aurelio — updated, not duplicated
        names = [r["name"] for r in authed_client.get("/restaurants").json()]
        assert names.count("Casa Aurelio") == 1

    def test_preview_reports_the_upsert_plan(self, authed_client):
        authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        corrected = (
            "name,address,is_active\n"
            'Casa Aurelio,"Calle Fuencarral 57, Madrid",true\n'
            'Taberna Sol,"Plaza Mayor 1, Madrid",true\n'
            'Marca Alfa,"Calle de la Montera 5, Madrid",true\n'
        )
        response = authed_client.post(
            "/import/restaurants/preview",
            content=corrected.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert response.status_code == 200
        details = {r["row"]: r.get("detail") for r in response.json()["report"]}
        assert details[1] == "exists — will update"
        assert details[2] == "exists — no change needed"
        assert details[3] is None  # a genuinely new row

    def test_menu_reimport_updates_by_restaurant_and_name(self, authed_client):
        authed_client.post(
            "/import/restaurants",
            content=CSV_RESTAURANTS.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        rid = authed_client.get("/restaurants").json()[0]["id"]
        rows = [{"restaurant_id": rid, "name": "Croqueta", "price": 4.5}]
        authed_client.post("/import/menu-items", json=rows)

        repriced = [{**rows[0], "price": 5.0}]
        second = authed_client.post("/import/menu-items", json=repriced)
        assert second.json()["updated"] == 1
        items = authed_client.get(f"/restaurants/{rid}/menu-items").json()
        assert len(items) == 1  # updated, not duplicated
        assert items[0]["price"] == 5.0


class TestTemplates:
    def test_template_per_entity(self, authed_client):
        response = authed_client.get("/import/restaurants/template")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        header = response.text.splitlines()[0]
        assert header == "name,address,is_active"

        drivers = authed_client.get("/import/drivers/template")
        assert drivers.text.splitlines()[0] == "name,vehicle,address"

        items = authed_client.get("/import/menu-items/template")
        assert items.text.splitlines()[0] == "restaurant_id,name,description,price,is_available"

    def test_template_of_unknown_entity_404s(self, authed_client):
        assert authed_client.get("/import/ufo/template").status_code == 404

    def test_template_rows_round_trip_through_the_import(self, authed_client):
        """The template's own example row is a valid file: download it,
        import it verbatim — the contract can't drift."""
        template = authed_client.get("/import/drivers/template").text
        response = authed_client.post(
            "/import/drivers",
            content=template.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
        assert response.status_code == 201
        assert response.json()["created"] == 1

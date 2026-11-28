"""The owner-verified pickup point (#358): the restaurant's door as identity.
Coordinates alone = the dragged pin (owner_verified - the geocoder is never
consulted against the owner's hand). Address alone = geocoded + self-verified.
The response carries the verdict so the portal badges it like the checkout."""

import pytest

from domain.models import Restaurant

RESTAURANT = {"name": "Casa Toni — Malasaña", "latitude": 40.4256, "longitude": -3.7043}


@pytest.fixture
def restaurant(authed_client):
    return authed_client.post("/restaurants", json=RESTAURANT).json()


def test_pinned_point_is_owner_verified(authed_client, db, restaurant, monkeypatch):
    monkeypatch.setattr(
        "domain.services.geocoder.geocode",
        lambda *a, **k: pytest.fail("the owner's pin must outrank the geocoder"),
    )
    response = authed_client.patch(
        f"/restaurants/{restaurant['id']}/pickup-point",
        json={
            "pickup_latitude": 40.42385,
            "pickup_longitude": -3.70162,
            "address": "Calle de Fuencarral 24, Madrid",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pin_source"] == "owner_verified"
    assert body["precision"] == "owner_verified"
    assert body["address"] == "Calle de Fuencarral 24, Madrid"
    row = db.get(Restaurant, restaurant["id"])
    assert row.latitude == 40.42385  # the owner's point
    assert row.pin_source == "owner_verified"


def test_address_only_geocodes_and_verifies(authed_client, db, restaurant, monkeypatch):
    monkeypatch.setattr(
        "domain.services.geocoder.geocode",
        lambda session, address: {
            "lat": 40.4221,
            "lon": -3.7008,
            "display_name": "resolved",
            "precision": "street_mismatch",
            "snap_distance_m": 77.0,
            "reverse_display_name": "r",
            "cached": False,
        },
    )
    response = authed_client.patch(
        f"/restaurants/{restaurant['id']}/pickup-point",
        json={"address": "Calle de Fuencarral 24, Madrid"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pin_source"] == "geocoded"
    assert body["precision"] == "street_mismatch"  # the honest verdict rides
    row = db.get(Restaurant, restaurant["id"])
    assert row.latitude == 40.4221
    assert row.pin_source == "geocoded"


def test_requires_writer(client, authed_client):
    restaurant = authed_client.post("/restaurants", json=RESTAURANT).json()
    response = client.patch(
        f"/restaurants/{restaurant['id']}/pickup-point",
        json={"address": "somewhere"},
    )
    assert response.status_code in (401, 403)  # the auth wall, no token


def test_neither_coords_nor_address_is_422(authed_client, restaurant):
    with_error = authed_client.patch(
        f"/restaurants/{restaurant['id']}/pickup-point",
        json={},
    )
    assert with_error.status_code == 422


def test_response_carries_the_identity(authed_client, restaurant):
    body = authed_client.get(f"/restaurants/{restaurant['id']}").json()
    assert "address" in body
    assert "pin_source" in body
    assert body["pin_source"] is None  # a fresh import: nobody vouched yet

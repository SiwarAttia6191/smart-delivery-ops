"""The geocode precision layer (#343): the geocoder is guilty until proven
innocent. The Valverde incident — "Calle de Fuencarral 24" resolving to a
point ON the parallel street — is the signature these tests freeze: the
router-graph witness must catch it, the verdict must ride the cache row
and the created event, and the customer's dragged pin must outrank both
witnesses (coords = truth, text = label)."""

import pytest
from pydantic import ValidationError

from apps.api.schemas.order import OrderCreate
from apps.api.schemas.pricing import QuoteRequest
from domain.models import Customer, GeocodeCache, Restaurant
from domain.services import geocoder, order_service
from domain.services.geocoder import (
    _identity_tokens,
    _streets_match,
    geocode,
)

# ── street identity: the generic words ("Calle de") carry no identity ──


def test_identity_tokens_strip_the_generic():
    assert _identity_tokens("Calle de Fuencarral 24") == {"fuencarral"}
    assert _identity_tokens("Calle de Valverde") == {"valverde"}
    assert _identity_tokens(None) == set()


def test_same_street_matches_across_article_noise():
    assert _streets_match("Calle de Fuencarral 24, Madrid", "Calle de Fuencarral") is True
    assert _streets_match("Fuencarral 24", "CALLE DE FUENCARRAL") is True


def test_the_parallel_street_does_not_match():
    # THE Valverde signature: reverse/snap say Valverde, the text says Fuencarral
    assert _streets_match("Calle de Fuencarral 24", "Calle de Valverde") is False


def test_abstains_when_a_witness_names_nothing():
    assert _streets_match("Calle de Fuencarral 24", None) is None
    assert _streets_match("Calle de Fuencarral 24", "") is None


# ── the verdict: both witnesses, worst classification wins ──


@pytest.fixture
def settings():
    return type(
        "S",
        (),
        {
            "geocoder_base_url": "https://nominatim.openstreetmap.org/search",
            "geocoder_country_codes": "es",
            "osrm_base_url": "http://osrm:5000",
        },
    )()


def _witness(reverse_road, snap_road, snap_distance=8.0, reverse_number=None):
    return (
        {
            "address": {"road": reverse_road, "house_number": reverse_number}
            if reverse_road
            else {},
            "display_name": f"{reverse_road or 'somewhere'}, Madrid, Spain",
        },
        snap_road,
        snap_distance,
    )


def test_street_mismatch_the_valverde_signature(db, settings, monkeypatch):
    monkeypatch.setattr(
        geocoder,
        "_reverse",
        lambda url, lat, lon: _witness("Calle de Valverde", "Calle de Valverde")[0],
    )
    monkeypatch.setattr(geocoder, "_snap", lambda base_url, lat, lon: (8.0, "Calle de Valverde"))
    verdict = geocoder._verify(settings, "Calle de Fuencarral 24", 40.4221, -3.7008)
    assert verdict["precision"] == "street_mismatch"


def test_ok_when_both_witnesses_agree(db, settings, monkeypatch):
    monkeypatch.setattr(
        geocoder,
        "_reverse",
        lambda url, lat, lon: _witness(
            "Calle de Fuencarral", "Calle de Fuencarral", reverse_number="24"
        )[0],
    )
    monkeypatch.setattr(geocoder, "_snap", lambda base_url, lat, lon: (5.0, "Calle de Fuencarral"))
    verdict = geocoder._verify(settings, "Calle de Fuencarral 24", 40.4221, -3.7008)
    assert verdict["precision"] == "ok"
    assert verdict["snap_distance_m"] == pytest.approx(5.0)


def test_number_mismatch_is_the_lesser_offense(settings, monkeypatch):
    monkeypatch.setattr(
        geocoder,
        "_reverse",
        lambda url, lat, lon: _witness(
            "Calle de Fuencarral", "Calle de Fuencarral", reverse_number="26"
        )[0],
    )
    monkeypatch.setattr(geocoder, "_snap", lambda base_url, lat, lon: (6.0, "Calle de Fuencarral"))
    verdict = geocoder._verify(settings, "Calle de Fuencarral 24", 40.4221, -3.7008)
    assert verdict["precision"] == "number_mismatch"


def test_off_network_overrides_names(settings, monkeypatch):
    monkeypatch.setattr(
        geocoder,
        "_reverse",
        lambda url, lat, lon: _witness("Calle de Fuencarral", "Calle de Fuencarral")[0],
    )
    monkeypatch.setattr(
        geocoder, "_snap", lambda base_url, lat, lon: (500.0, "Calle de Fuencarral")
    )
    verdict = geocoder._verify(settings, "Calle de Fuencarral 24", 40.4221, -3.7008)
    assert verdict["precision"] == "off_network"


def test_offline_witnesses_verdict_unverified_not_a_lie(settings, monkeypatch):
    monkeypatch.setattr(geocoder, "_reverse", lambda url, lat, lon: None)
    monkeypatch.setattr(geocoder, "_snap", lambda base_url, lat, lon: (None, None))
    verdict = geocoder._verify(settings, "Calle de Fuencarral 24", 40.4221, -3.7008)
    assert verdict["precision"] == "unverified"


# ── the cache row carries the verdict; the cache hit replays it ──


def test_geocode_caches_the_verdict(db, settings, monkeypatch):
    monkeypatch.setattr(geocoder, "get_settings", lambda: settings)
    monkeypatch.setattr(
        geocoder,
        "_forward",
        lambda s, a: [
            {"lat": "40.4221", "lon": "-3.7008", "display_name": "24, Calle de Fuencarral, Madrid"}
        ],
    )
    monkeypatch.setattr(
        geocoder,
        "_reverse",
        lambda url, lat, lon: _witness("Calle de Valverde", "Calle de Valverde")[0],
    )
    monkeypatch.setattr(geocoder, "_snap", lambda base_url, lat, lon: (9.0, "Calle de Valverde"))

    resolved = geocode(db, "Calle de Fuencarral 24, Madrid")
    assert resolved["precision"] == "street_mismatch"  # wrong point, honest verdict
    assert resolved["cached"] is False

    row = db.query(GeocodeCache).one()
    assert row.precision == "street_mismatch"
    assert row.snap_distance_m == pytest.approx(9.0)

    again = geocode(db, "calle de fuencarral 24, madrid")  # normalized key
    assert again["cached"] is True
    assert again["precision"] == "street_mismatch"


# ── the customer-pinned dropoff: coords are the truth, text is the label ──


@pytest.fixture
def entities(db):
    customer = Customer(tenant_id=1, name="Nadia", latitude=40.4256, longitude=-3.7043)
    restaurant = Restaurant(tenant_id=1, name="Casa Toni", latitude=40.4256, longitude=-3.7043)
    db.add_all([customer, restaurant])
    db.commit()
    return customer, restaurant


def test_pinned_dropoff_never_calls_the_geocoder(db, entities, monkeypatch):
    customer, restaurant = entities
    monkeypatch.setattr(
        geocoder, "geocode", lambda *a, **k: pytest.fail("the pin must outrank the geocoder")
    )
    order = order_service.create_order(
        db,
        customer.id,
        restaurant.id,
        40.4221,
        -3.7008,
        dropoff_address="Calle de Fuencarral 24, Madrid",  # the dragged pin + its label
    )
    created = next(e for e in order.events if e.event_type == "created")
    assert created.payload["dropoff_precision"] == "customer_pinned"
    assert created.payload["dropoff_address"] == "Calle de Fuencarral 24, Madrid"
    assert order.dropoff_latitude == pytest.approx(40.4221)  # the customer's point
    assert order.dropoff_longitude == pytest.approx(-3.7008)


def test_address_only_still_geocodes_and_verifies(db, entities, monkeypatch):
    customer, restaurant = entities
    monkeypatch.setattr(
        geocoder,
        "geocode",
        lambda session, address: {
            "lat": 40.4221,
            "lon": -3.7008,
            "display_name": "resolved",
            "precision": "ok",
            "snap_distance_m": 4.0,
            "reverse_display_name": "r",
            "cached": False,
        },
    )
    order = order_service.create_order(
        db,
        customer.id,
        restaurant.id,
        None,
        None,
        dropoff_address="Calle de Fuencarral 24, Madrid",
    )
    created = next(e for e in order.events if e.event_type == "created")
    assert created.payload["dropoff_precision"] == "ok"
    assert created.payload["dropoff_snap_m"] == pytest.approx(4.0)


# ── the schema: address-only / coords-only / BOTH valid; NEITHER invalid ──


def test_schema_accepts_both_address_and_coords():
    payload = OrderCreate.model_validate(
        {
            "customer_id": 1,
            "restaurant_id": 1,
            "dropoff_latitude": 40.4221,
            "dropoff_longitude": -3.7008,
            "dropoff_address": "Calle de Fuencarral 24",
        }
    )
    assert payload.dropoff_address is not None


def test_schema_still_rejects_neither():
    with pytest.raises(ValidationError):
        OrderCreate.model_validate({"customer_id": 1, "restaurant_id": 1})


def test_quote_schema_accepts_both():
    payload = QuoteRequest.model_validate(
        {
            "restaurant_id": 1,
            "dropoff_latitude": 40.4221,
            "dropoff_longitude": -3.7008,
            "dropoff_address": "Calle de Fuencarral 24",
        }
    )
    assert payload.dropoff_latitude is not None

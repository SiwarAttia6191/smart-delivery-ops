"""Geocoder unit tests (#232): cache discipline + failure shapes.

The provider is monkeypatched at urllib level — tests never touch the
network. The guarantees: an address is geocoded exactly once (cache hit
makes zero provider calls), unresolvable text raises GeocodeAddressError
(422), an unreachable provider raises GeocoderUnavailableError (503),
and the cache key normalizes case/whitespace.
"""

import json
import urllib.error
import urllib.request

import pytest

from core.config import get_settings
from core.exceptions import GeocodeAddressError, GeocoderUnavailableError
from domain.models import GeocodeCache
from domain.services.geocoder import _cache_key, geocode

NOMINATIM_HIT = [
    {
        "lat": "40.41555",
        "lon": "-3.70745",
        "display_name": "Plaza Mayor, 28012 Madrid, España",
    }
]


class _FakeResponse:
    def __init__(self, payload: object):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _CallCounter:
    def __init__(self):
        self.count = 0
        self.last_url = ""

    def hit(self, payload: object):
        def fake_urlopen(request, timeout=None):  # noqa: ARG001 - signature match
            self.count += 1
            self.last_url = request.full_url
            return _FakeResponse(payload)

        return fake_urlopen


def test_cache_key_normalizes_case_and_whitespace():
    assert _cache_key("Calle Gran Vía 42") == _cache_key("calle gran vía  42")
    assert _cache_key("  Calle Gran Vía 42 ") == _cache_key("calle gran vía 42")


def test_geocode_miss_stores_and_hit_skips_the_provider(db, monkeypatch):
    counter = _CallCounter()
    monkeypatch.setattr(urllib.request, "urlopen", counter.hit(NOMINATIM_HIT))
    monkeypatch.setattr(get_settings(), "geocoder_country_codes", "es")

    first = geocode(db, "Plaza Mayor, Madrid")
    assert first["cached"] is False
    assert first["lat"] == 40.41555
    assert counter.count == 1
    # the URL is properly encoded and carries the country bias
    assert "q=Plaza+Mayor" in counter.last_url or "q=Plaza%20Mayor" in counter.last_url
    assert "countrycodes=es" in counter.last_url

    second = geocode(db, "plaza mayor, madrid")  # different case/spacing, same row
    assert second["cached"] is True
    assert second["lat"] == 40.41555
    assert counter.count == 1  # the provider was NOT called again
    assert db.query(GeocodeCache).count() == 1


def test_unresolvable_address_raises_422_shape(db, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _CallCounter().hit([]))
    with pytest.raises(GeocodeAddressError):
        geocode(db, "nowhere at all 00000")
    # nothing cached from a failed lookup
    assert db.query(GeocodeCache).count() == 0


def test_unreachable_provider_raises_503_shape(db, monkeypatch):
    def failing_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
    with pytest.raises(GeocoderUnavailableError):
        geocode(db, "Plaza Mayor, Madrid")
    assert db.query(GeocodeCache).count() == 0


def test_country_bias_is_omitted_when_unset(db, monkeypatch):
    counter = _CallCounter()
    monkeypatch.setattr(urllib.request, "urlopen", counter.hit(NOMINATIM_HIT))
    monkeypatch.setattr(get_settings(), "geocoder_country_codes", "")

    geocode(db, "Plaza Mayor, Madrid")
    assert "countrycodes" not in counter.last_url

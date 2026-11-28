"""The address typeahead (#365): prevent the typo before the geocoder
resolves it. Labels only — a selection fills the field and the resolution
still goes through the two-witness verification. The endpoint's contract
is to degrade: provider down = empty list, never an error."""

import json

from core.config import get_settings


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_suggestions_return_labels(client, monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=10):
        seen["url"] = request.full_url
        return _Response(
            [
                {"display_name": "Calle de Fuencarral 24, Madrid"},
                {"display_name": "Calle de Fuencarral 87, Madrid"},
            ]
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    body = client.get("/address-suggestions", params={"q": "Calle de Fuencarr"}).json()
    assert body["suggestions"] == [
        "Calle de Fuencarral 24, Madrid",
        "Calle de Fuencarral 87, Madrid",
    ]
    assert "q=Calle+de+Fuencarr" in seen["url"].replace("%20", "+") or "Fuencarr" in seen["url"]
    assert get_settings().geocoder_country_codes in seen["url"]  # the country bias applies


def test_short_query_is_422(client):
    response = client.get("/address-suggestions", params={"q": "Ca"})
    assert response.status_code == 422


def test_provider_down_degrades_to_empty(client, monkeypatch):
    # a DIFFERENT prefix than the earlier tests: the 60 s TTL cache is
    # module-level and would otherwise serve their successful entry
    def broken_urlopen(request, timeout=10):
        raise OSError("provider unreachable")

    monkeypatch.setattr("urllib.request.urlopen", broken_urlopen)
    response = client.get("/address-suggestions", params={"q": "Avenida de la Ilustraci"})
    assert response.status_code == 200
    assert response.json()["suggestions"] == []  # the typeahead never breaks typing


def test_hot_prefix_is_cached(client, monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=10):
        calls.append(1)
        return _Response([{"display_name": "Plaza Mayor 1, Madrid"}])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    first = client.get("/address-suggestions", params={"q": "Plaza Mayor"}).json()
    second = client.get("/address-suggestions", params={"q": "Plaza Mayor"}).json()
    assert first == second
    assert len(calls) == 1  # the 60 s TTL cache absorbed the repeat


def test_labels_only_never_coordinates(client, monkeypatch):
    # the contract: a suggestion fills the FIELD — the resolution still goes
    # through the two-witness verification, so labels are all we return
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=10: _Response(
            [
                {"display_name": "Somewhere 1", "lat": "40.1", "lon": "-3.7"},
            ]
        ),
    )
    body = client.get("/address-suggestions", params={"q": "Somewhere"}).json()
    assert body["suggestions"] == ["Somewhere 1"]

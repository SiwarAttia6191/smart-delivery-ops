"""The France prix-carburants adapter tests (#274 V2): the real open-data
XML format parsed into the normalized StationPrice shape.

The fixture mirrors the actual dataset structure: <pdv> elements with
coordinates ×1e5, French fuel-kind names (Gazole/SP95/SP98), prices as
strings with the update timestamp. A station without prices yields
nothing (the adapter never fabricates).
"""

from datetime import datetime
from pathlib import Path

from domain.services import fuel_adapters
from domain.services.fuel_adapters import normalize_france

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "prix_carburants_sample.xml"


def test_france_adapter_parses_the_real_shape():
    prices = normalize_france(FIXTURE.read_bytes(), source="prix-carburants.gouv.fr")
    # station 75000103 quotes no fuels -> zero rows for it; the others: 2+2+1
    assert len(prices) == 5
    gazole = [p for p in prices if p.fuel_kind == "diesel"]
    assert len(gazole) == 2
    first = gazole[0]
    assert first.station_id == "75000100"
    assert first.price == 1.85
    assert first.currency == "EUR"
    assert first.source == "prix-carburants.gouv.fr"
    # coordinates arrive ×1e5 — 484500 / 1e5 = 4.845 (Paris latitude)
    assert 4.0 < first.latitude < 5.0


def test_fuel_kinds_are_normalized():
    prices = normalize_france(FIXTURE.read_bytes())
    kinds = {p.fuel_kind for p in prices}
    assert kinds == {"diesel", "gasoline_95", "gasoline_98"}


def test_updated_at_is_timezone_aware():
    prices = normalize_france(FIXTURE.read_bytes())
    assert all(p.updated_at.tzinfo is not None for p in prices)
    gazole = sorted(p.updated_at for p in prices if p.fuel_kind == "diesel")
    assert gazole[-1].date() == datetime(2026, 11, 15).date()


def test_registry_keys_on_country_code():
    assert fuel_adapters.adapter_for("fr") is normalize_france
    assert fuel_adapters.adapter_for("FR") is normalize_france  # case-insensitive
    assert fuel_adapters.adapter_for("es") is None  # not yet — the manifest fallback covers
    assert fuel_adapters.adapter_for("") is None

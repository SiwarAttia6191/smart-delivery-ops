"""The station-level fuel price adapters (#274 V2): one normalized shape,
per-country adapters.

The problem: every country publishes station-level fuel prices through
its OWN source, format and cadence — but the consumers (the quote's
fuel estimate, the near-driver lookup) must speak ONE shape. The
adapter registry keys on the manifest's ISO country code; each adapter
converts its national format into the StationPrice shape. Where no
adapter exists, the manifest's official constant remains the fallback
(a deployment never breaks — it gets coarser resolution until an
adapter is written, rule 11 applied to data sources).

**The EU convergence**: AFIR (Regulation 2023/1804) obliges member
states to expose station-level data via National Access Points
harmonized on DATEX II — over time one DATEX II adapter family covers
many countries. Until then: one adapter per country, each tested
against the real format.

The normalized shape (the ONLY thing consumers see):
    station_id, name, latitude, longitude, fuel_kind, price, currency,
    updated_at, source

Scraping was evaluated and rejected: ToS-prohibited on the aggregators,
the EU Database Directive makes systematic extraction of curated
databases legally gray, and parser rot against a moving target is a
maintenance debt. Official open data beats it on every axis.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

# coordinate scale: France publishes lat/lon × 1e5 as integers
FRANCE_COORD_SCALE = 100_000.0

# the French fuel-kind names → the normalized kinds the cache stores
FRANCE_KIND_MAP = {
    "Gazole": "diesel",
    "SP95": "gasoline_95",
    "SP98": "gasoline_98",
    "E10": "e10",
    "E85": "e85",
    "GPLc": "lpg",
}


@dataclass(frozen=True)
class StationPrice:
    """The normalized shape every adapter emits and every consumer reads."""

    station_id: str
    name: str
    latitude: float
    longitude: float
    fuel_kind: str  # normalized: diesel, gasoline_95, ...
    price: float
    currency: str
    updated_at: datetime
    source: str


def _parse_france_station(station: ET.Element, source: str) -> list[StationPrice]:
    """One <pdv> element → the station's price rows (one per quoted fuel
    kind). Addresses: the element's ville + adresse; the id prefixes the
    name for uniqueness. Prices without a valeur or with a garbage
    maj are skipped — a malformed entry never kills the parse."""
    station_id = station.get("id", "")
    latitude = float(station.get("latitude", "0")) / FRANCE_COORD_SCALE
    longitude = float(station.get("longitude", "0")) / FRANCE_COORD_SCALE
    city = (station.findtext("ville") or "").strip()
    address = (station.findtext("adresse") or "").strip()
    name = (address or city or f"Station {station_id}")[:200]

    prices: list[StationPrice] = []
    for price_el in station.findall("prix"):
        kind_raw = price_el.get("nom", "")
        kind = FRANCE_KIND_MAP.get(kind_raw)
        valeur = price_el.get("valeur")
        maj = price_el.get("maj")
        if kind is None or not valeur:
            continue
        try:
            price = float(valeur)
            updated = datetime.fromisoformat(maj) if maj else datetime.now(UTC)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
        except ValueError:
            continue
        prices.append(
            StationPrice(
                station_id=station_id,
                name=name,
                latitude=latitude,
                longitude=longitude,
                fuel_kind=kind,
                price=price,
                currency="EUR",
                updated_at=updated,
                source=source,
            )
        )
    return prices


def normalize_france(
    xml_bytes: bytes, source: str = "prix-carburants.gouv.fr"
) -> list[StationPrice]:
    """The France adapter: prix-carburants.gouv.fr's XML (per-station,
    daily, full coverage). Coordinates arrive ×1e5; the fuel kinds carry
    French names mapped to the normalized kinds."""
    root = ET.fromstring(xml_bytes)
    prices: list[StationPrice] = []
    for station in root.iter("pdv"):
        prices.extend(_parse_france_station(station, source))
    return prices


# the registry: manifest country_code → the adapter that parses that
# country's open-data format. New countries: one adapter + one entry.
ADAPTERS = {
    "fr": normalize_france,
}


def adapter_for(country_code: str):
    """The country's adapter, or None — the manifest-constant fallback
    covers every deployment without one."""
    return ADAPTERS.get((country_code or "").lower())

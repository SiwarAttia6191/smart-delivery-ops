"""The geocoder (#232): address text → coordinates — now self-verifying (#343).

Pairs with the routing stack: OSRM routes on OpenStreetMap, so Nominatim
(OpenStreetMap's own geocoder) resolves the addresses — the SAME free
data, the SAME fair-use posture (1 request/second, a descriptive
User-Agent), and a DATABASE CACHE so any address is geocoded exactly once
ever. The cache is a technical artifact (like route_cache), deliberately
NOT tenant data: coordinates of an address are city facts.

Provider seam like everything else: Nominatim is the free default behind
settings (GEOCODER_BASE_URL / GEOCODER_COUNTRY_CODES); a commercial
geocoder is one class away. Failure posture: honest errors on both shapes
— an unresolvable address raises GeocodeAddressError (422: the caller's
data problem; a wrong-coordinate delivery is never acceptable) and an
unreachable provider raises GeocoderUnavailableError (503: retry, or send
coordinates instead). Note for the bulk-import future (#223): the cache
absorbs repeats, but N distinct addresses still mean N live calls — batch
callers must pace themselves to Nominatim's 1 req/s fair-use policy.

#343 — the geocoder is guilty until proven innocent. The Valverde incident
(2026-11-20) proved a forward geocode can be confidently wrong: "Calle de
Fuencarral 24" resolved to a point that SITS ON the parallel street, and
Nominatim's reverse view of that point anchored to a storefront on the
requested street — the wrong point came back labeled "Fuencarral". A
cached wrong answer is worse than no answer: it repeats forever. So every
first resolution is now verified against two independent witnesses before
it is cached:

1. Nominatim /reverse at the point — which street does the REVERSE view
   claim? (catches landings the reverse honestly names)
2. OSRM /nearest — which street does OUR OWN ROUTER GRAPH say the point
   sits on, and how far is it from any road? (the decisive witness for the
   Valverde class: the router graph has no POI anchor to be fooled by —
   and zero extra infrastructure, the router is already there)

The verdict is stored ON the cache row (precision + snap_distance_m +
reverse_display_name) and surfaces everywhere the point surfaces: the
quote response, the created event's payload, the tracking page badge.
Verdicts, not bans — a flagged address still resolves (the caller sees
the flag and the customer can drag the pin), it just never lies quietly.
scripts/verify_city_addresses.py re-runs this drill for any city and
repairs old cache rows.
"""

import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import httpx
from sqlalchemy.orm import Session

from core.config import get_settings
from core.exceptions import GeocodeAddressError, GeocoderUnavailableError

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "smart-delivery-ops-demo/1.0 (portfolio project; contact via the repo)"

# the snap beyond which a point is not "on the network" at all: 150 m from
# any road means the geocoder did not find an address, it found a field
OFF_NETWORK_METERS = 150.0

# precision verdicts, worst last (max() of the ordered list picks the worst)
PRECISION_OK = "ok"
PRECISION_NUMBER_MISMATCH = "number_mismatch"
PRECISION_STREET_MISMATCH = "street_mismatch"
PRECISION_OFF_NETWORK = "off_network"
PRECISION_UNVERIFIED = "unverified"
_PRECISION_RANK = {
    PRECISION_UNVERIFIED: 0,
    PRECISION_OK: 1,
    PRECISION_NUMBER_MISMATCH: 2,
    PRECISION_STREET_MISMATCH: 3,
    PRECISION_OFF_NETWORK: 4,
}

# generic street words that carry no identity: "Calle de Fuencarral" and
# "Fuencarral" are the same street; "Calle de Valverde" is NOT "Calle de
# Fuencarral" because the identity token (fuencarral / valverde) differs
_GENERIC_TOKENS = {
    "calle",
    "carrer",
    "carrera",
    "avenida",
    "avenue",
    "avenue",
    "rue",
    "via",
    "viale",
    "road",
    "street",
    "strasse",
    " plaza",
    "plaza",
    "passage",
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "du",
    "d",
    "l",
    "of",
    "the",
}

_HOUSE_NUMBER_RE = re.compile(r"\b(\d{1,4}[A-Za-z]?)\b")


def _cache_key(address: str) -> str:
    # normalize: lowercase + collapse whitespace runs — "Calle Gran Vía 42"
    # and "calle   gran   vía   42" must hit the same cache row
    normalized = re.sub(r"\s+", " ", address.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _identity_tokens(name: str | None) -> set[str]:
    """The identity-bearing words of a street name: lowercase, accent-stripped,
    generic street words dropped. 'Calle de Fuencarral' → {'fuencarral'}."""
    if not name:
        return set()
    text = unicodedata.normalize("NFD", name.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return {t for t in re.split(r"[^a-z0-9]+", text) if len(t) >= 3 and t not in _GENERIC_TOKENS}


def _streets_match(requested: str, provider_name: str | None) -> bool | None:
    """Do the requested address and a provider's road name name the same
    street? True / False on comparable names, None when the provider named
    nothing (unnamed way, empty reverse road) — an abstention, never a vote."""
    requested_tokens = _identity_tokens(requested)
    provider_tokens = _identity_tokens(provider_name)
    if not requested_tokens or not provider_tokens:
        return None
    return bool(requested_tokens & provider_tokens)


def _requested_house_number(address: str) -> str | None:
    first_segment = address.split(",")[0]
    match = _HOUSE_NUMBER_RE.search(first_segment)
    return match.group(1) if match else None


def _worst(*verdicts: str) -> str:
    """The worst verdict wins — and silence means both witnesses agreed,
    which is exactly what 'ok' means (the unverified verdict is appended
    EXPLICITLY when witnesses cannot testify, never as a fallback)."""
    present = [v for v in verdicts if v]
    if not present:
        return PRECISION_OK
    return max(present, key=lambda v: _PRECISION_RANK.get(v, 0))


def _http_json(url: str, timeout: float = 10.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _forward(session_settings: Any, address: str) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "q": address.strip(),
        "format": "json",
        "limit": "1",
        "addressdetails": "1",
    }
    if session_settings.geocoder_country_codes:
        # bias the search to the deployment's city country (e.g. "es" for
        # the Madrid pack) — without it "Plaza Mayor" could resolve to any
        # Spanish-speaking city on earth
        params["countrycodes"] = session_settings.geocoder_country_codes
    return _http_json(f"{session_settings.geocoder_base_url}?{urllib.parse.urlencode(params)}")


def _reverse(reverse_url: str, lat: float, lon: float) -> dict[str, Any] | None:
    """Nominatim /reverse at the point: the street the reverse view claims.
    None on failure — a witness that cannot testify is not evidence."""
    params = {
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "format": "json",
        "zoom": "18",
        "addressdetails": "1",
    }
    try:
        return _http_json(f"{reverse_url}?{urllib.parse.urlencode(params)}")
    except Exception:  # noqa: BLE001 - a witness that cannot testify is not evidence;
        return None  # any failure degrades the verdict to unverified, never a crash


def _snap(base_url: str, lat: float, lon: float) -> tuple[float | None, str | None]:
    """OSRM /nearest: (snap_distance_m, snapped_road_name). The router graph
    is the independent witness — it knows which street the point ACTUALLY
    sits on, with no POI anchor to fool it. (None, None) when unreachable;
    the geocode still succeeds, it just stays unverified."""
    url = f"{base_url.rstrip('/')}/nearest/v1/driving/{lon:.6f},{lat:.6f}"
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(url, params={"number": "1"})
            response.raise_for_status()
        waypoint = response.json()["waypoints"][0]
        return float(waypoint.get("distance", 0.0)), (waypoint.get("name") or None)
    except Exception:  # noqa: BLE001 - verification is best-effort by contract
        return None, None


def _verify(
    settings: Any,
    address: str,
    lat: float,
    lon: float,
) -> dict[str, Any]:
    """Run both witnesses and classify. Returns {precision, snap_distance_m,
    reverse_display_name}. Verification NEVER raises and NEVER rejects the
    resolution — the point goes through with its verdict attached (#343)."""
    reverse = _reverse(
        getattr(settings, "geocoder_reverse_url", None) or NOMINATIM_REVERSE_URL, lat, lon
    )
    snap_distance, snap_road = _snap(settings.osrm_base_url, lat, lon)
    reverse_road = (reverse or {}).get("address", {}).get("road")

    verdicts: list[str] = []
    # Witness hierarchy, learned LIVE (2026-11-21): the snap road — the
    # router graph's own answer — is the DECISIVE street witness. The
    # reverse geocode anchors to POIs: asked about the Valverde point it
    # cheerfully returned "Calle de Fuencarral 24" (a storefront on the
    # requested street), and a democracy where the unreliable witness's
    # yes acquits is how "ok" gets stamped on a wrong point. So: the snap
    # vote, when it can speak, decides street identity; the reverse vote
    # decides only when the snap abstains (unnamed way / router offline).
    snap_match = _streets_match(address, snap_road)
    if snap_match is False:
        verdicts.append(PRECISION_STREET_MISMATCH)
    elif snap_match is None:
        reverse_match = _streets_match(address, reverse_road)
        if reverse_match is False:
            verdicts.append(PRECISION_STREET_MISMATCH)
        elif reverse_match is None and not snap_road and reverse_road is None:
            # nobody could testify (offline drill environments, unnamed ways)
            verdicts.append(PRECISION_UNVERIFIED)

    # witness 1 bonus: the house number the reverse interpolates — a street
    # match with the WRONG number is interpolation drift, a lesser offense
    requested_number = _requested_house_number(address)
    reverse_number = (reverse or {}).get("address", {}).get("house_number")
    if (
        requested_number
        and reverse_number
        and requested_number.lstrip("0") != str(reverse_number).lstrip("0")
    ):
        verdicts.append(PRECISION_NUMBER_MISMATCH)

    # witness 2 bonus: the snap distance — too far from ANY road is fatal
    # regardless of names (a field, a construction site, a wrong city)
    if snap_distance is not None and snap_distance > OFF_NETWORK_METERS:
        verdicts.append(PRECISION_OFF_NETWORK)

    return {
        "precision": _worst(*verdicts),
        "snap_distance_m": snap_distance,
        "reverse_display_name": (reverse or {}).get("display_name"),
    }


def geocode(session: Session, address: str) -> dict[str, Any]:
    """Address text in, {lat, lon, display_name, precision, ...} out — cached
    forever per normalized address (addresses don't move; a city data
    update clears the cache via scripts/geocode_cache_clear.py or the drill
    repairs it, and the next lookup re-resolves). Raises GeocodeAddressError
    when nothing resolves, GeocoderUnavailableError when the provider is
    unreachable. Every FIRST resolution carries its #343 verification
    verdict; cache rows created before #343 read precision=None → surfaced
    as "unverified" until scripts/verify_city_addresses.py re-runs them."""
    from domain.models import GeocodeCache

    key = _cache_key(address)
    cached = session.query(GeocodeCache).filter(GeocodeCache.address_key == key).one_or_none()
    if cached is not None:
        return {
            "lat": cached.lat,
            "lon": cached.lon,
            "display_name": cached.display_name,
            "precision": cached.precision or PRECISION_UNVERIFIED,
            "snap_distance_m": cached.snap_distance_m,
            "reverse_display_name": cached.reverse_display_name,
            "cached": True,
        }

    settings = get_settings()
    try:
        results = _forward(settings, address)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise GeocoderUnavailableError(f"geocoder unreachable: {exc}") from exc
    if not results:
        raise GeocodeAddressError(f"address not found: {address.strip()}")
    best = results[0]
    lat, lon = float(best["lat"]), float(best["lon"])

    verdict = _verify(settings, address, lat, lon)

    row = GeocodeCache(
        address_key=key,
        address=address.strip(),
        lat=lat,
        lon=lon,
        display_name=best.get("display_name", ""),
        **verdict,
    )
    session.add(row)
    session.commit()
    return {
        "lat": row.lat,
        "lon": row.lon,
        "display_name": row.display_name,
        **verdict,
        "cached": False,
    }

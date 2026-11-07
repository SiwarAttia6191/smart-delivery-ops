"""The geocoder (#232): address text → coordinates.

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
"""

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy.orm import Session

from core.config import get_settings
from core.exceptions import GeocodeAddressError, GeocoderUnavailableError

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "smart-delivery-ops-demo/1.0 (portfolio project; contact via the repo)"


def _cache_key(address: str) -> str:
    # normalize: lowercase + collapse whitespace runs — "Calle Gran Vía 42"
    # and "calle   gran   vía   42" must hit the same cache row
    normalized = re.sub(r"\s+", " ", address.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def geocode(session: Session, address: str) -> dict[str, Any]:
    """Address text in, {lat, lon, display_name, cached} out — cached
    forever per normalized address (addresses don't move; a city data
    update clears the cache via scripts/geocode_cache_clear.py and the
    next lookup re-resolves). Raises GeocodeAddressError when nothing
    resolves, GeocoderUnavailableError when the provider is unreachable."""
    from domain.models import GeocodeCache

    key = _cache_key(address)
    cached = session.query(GeocodeCache).filter(GeocodeCache.address_key == key).one_or_none()
    if cached is not None:
        return {
            "lat": cached.lat,
            "lon": cached.lon,
            "display_name": cached.display_name,
            "cached": True,
        }

    settings = get_settings()
    params: dict[str, str] = {
        "q": address.strip(),
        "format": "json",
        "limit": "1",
    }
    if settings.geocoder_country_codes:
        # bias the search to the deployment's city country (e.g. "es" for
        # the Madrid pack) — without it "Plaza Mayor" could resolve to any
        # Spanish-speaking city on earth
        params["countrycodes"] = settings.geocoder_country_codes
    url = f"{settings.geocoder_base_url}?{urllib.parse.urlencode(params)}"

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            results = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise GeocoderUnavailableError(f"geocoder unreachable: {exc}") from exc
    if not results:
        raise GeocodeAddressError(f"address not found: {address.strip()}")
    best = results[0]

    row = GeocodeCache(
        address_key=key,
        address=address.strip(),
        lat=float(best["lat"]),
        lon=float(best["lon"]),
        display_name=best.get("display_name", ""),
    )
    session.add(row)
    session.commit()
    return {"lat": row.lat, "lon": row.lon, "display_name": row.display_name, "cached": False}

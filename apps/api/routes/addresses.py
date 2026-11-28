"""Address typeahead (#365): prevent the typo before the geocoder resolves it.

A thin typeahead endpoint over the SAME Nominatim provider the geocoder
uses — the market's standard source-side prevention (every major delivery
platform shows a typeahead while typing), at zero extra infrastructure:
same provider, same fair-use posture, same country bias.

Deliberately returns LABELS ONLY, never coordinates: a suggestion fills
the address field, and the resolution still goes through the normal
geocode -> two-witness verification -> pin flow (#343). Typeahead
PREVENTS; the precision layer DETECTS; neither bypasses the other.

Degradation by contract: the provider being unreachable yields an EMPTY
list (HTTP 200) — a typeahead must never break typing. Hot prefixes are
cached in-process (60 s TTL) to keep the demo box well inside Nominatim's
1 request/second fair use even with a debounced client.
"""

import time

from fastapi import APIRouter, Query

from core.config import get_settings
from domain.services import geocoder

router = APIRouter(prefix="/address-suggestions", tags=["addresses"])

_MIN_QUERY_LEN = 3
_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, list[dict]]] = {}


def _provider_labels(q: str) -> list[dict]:
    """Query Nominatim for completion candidates. Returns [] on ANY
    provider failure — the endpoint's contract is to degrade, not break."""
    import json
    import urllib.parse
    import urllib.request

    settings = get_settings()
    params: dict[str, str] = {
        "q": q,
        "format": "json",
        "limit": "5",
        "addressdetails": "0",
    }
    if settings.geocoder_country_codes:
        params["countrycodes"] = settings.geocoder_country_codes
    url = f"{settings.geocoder_base_url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": geocoder.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            results = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - typeahead degrades, never breaks typing
        return []
    return [{"label": item.get("display_name", "")} for item in results if item.get("display_name")]


@router.get("")
def suggest(
    q: str = Query(..., min_length=_MIN_QUERY_LEN, max_length=120),
) -> dict:
    """Top-5 completion labels for a partially typed address (#365).
    Labels only — picking one fills the address field, and the resolution
    still runs through the two-witness verification. Empty on provider
    failure: the typeahead never breaks typing."""
    key = q.strip().lower()
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < _TTL_SECONDS:
        return {"suggestions": [item["label"] for item in cached[1]]}
    items = _provider_labels(q)
    # keep the cache bounded: drop stale entries before inserting
    stale = [k for k, (ts, _) in _cache.items() if now - ts >= _TTL_SECONDS]
    for k in stale:
        del _cache[k]
    _cache[key] = (time.monotonic(), items)
    return {"suggestions": [item["label"] for item in items]}

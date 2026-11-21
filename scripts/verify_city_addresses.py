"""The city address drill (#343): the geocoder is guilty until proven innocent.

The Valverde incident (2026-11-20) is the reason this script exists: Madrid's
"Calle de Fuencarral 24" resolved — confidently, cacheably — to a point that
sits on the PARALLEL street, and nothing in the pipeline noticed. This drill
is the generic answer for ANY city, at onboarding and forever after:

1. Fresh-resolve each address (cache bypassed) and run the geocoder's two
   witnesses — Nominatim /reverse at the point, OSRM /nearest road name +
   snap distance from our own router graph.
2. --deep adds the third witness: Overpass street geometry — is the point
   actually closer to the requested street's lines than to some other
   street? (the witness that catches POI-anchored reverse results)
3. --routes N probes the ROUTER itself: N pairs through OSRM /route,
   flagging any detour ratio a sane city graph would not produce (the
   mixed-generation-graph class of failure — 4,200 km for a 0.48 km pair).
4. --fix writes fresh verdicts back onto the cache rows (pre-#343 rows
   read "unverified"; after --fix they carry a real verdict).

Runs anywhere the stack's env is present (host with .env loaded, or
`docker compose exec api python scripts/verify_city_addresses.py ...`).
Exit code 1 when any address fails — CI-able as a city-onboarding gate.

Usage (repo root):
    python scripts/verify_city_addresses.py --file data/city-addresses/madrid.txt
    python scripts/verify_city_addresses.py --cached                # re-verify cache rows
    python scripts/verify_city_addresses.py --file ... --fix        # write verdicts back
    python scripts/verify_city_addresses.py --file ... --routes 10  # + router probe
    python scripts/verify_city_addresses.py --address "Calle de Fuencarral 24, Madrid" --deep
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import engine  # noqa: E402
from domain.models import GeocodeCache  # noqa: E402
from domain.rules.geo import haversine_km  # noqa: E402
from domain.services import geocoder as geocoder_mod  # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# the deep check: a point whose OWN street's nearest line is this far away
# while ANOTHER street is closer is sitting on the wrong road
DEEP_WAYPOINT_RADIUS_M = 120.0
# the router probe: road distance ÷ straight-line distance for intra-city
# pairs. TOPOLOGY-DEPENDENT, not universal: a one-way grid measures far
# higher legitimate ratios than a highway grid — Madrid Centro's worst
# legitimate one-way loop measured 11.8x. The probe's real target is the
# BROKEN-graph class, which measures MILLIONS x (4,200 km for a 0.48 km
# pair), so 15x keeps headroom for dense one-way cities while still
# flagging any sick router with a 1000x margin. The per-order backstop
# in route_service stays at 30x for the same reason.
ROUTE_SANITY_RATIO = 15.0
# the pickup audit: door nodes within this distance of a restaurant's
# stored coordinates count as "the entrance is here"
PICKUP_DOOR_RADIUS_M = 40.0

VERDICT_BAD = {"street_mismatch", "number_mismatch", "off_network"}
VERDICT_PASS = {"ok", "customer_pinned"}


def _pace(last_request: list[float], min_interval: float = 1.1) -> None:
    """Nominatim fair use: 1 request/second, enforced here for batch runs."""
    elapsed = time.monotonic() - last_request[0]
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    last_request[0] = time.monotonic()


def deep_street_check(address: str, lat: float, lon: float) -> str | None:
    """Witness 3 (optional): Overpass street geometry around the point.
    Returns 'street_geometry_mismatch' when the point hugs a DIFFERENT
    named street while the requested street's lines stay far away, None
    when the point plausibly sits on the requested street (or Overpass is
    unreachable / the address names no comparable street)."""
    identity = geocoder_mod._identity_tokens(address)
    if not identity:
        return None
    query = (
        "[out:json][timeout:25];"
        f"way(around:{int(DEEP_WAYPOINT_RADIUS_M)},{lat:.6f},{lon:.6f})[highway][name];"
        "out geom;"
    )
    request = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": geocoder_mod.USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - the optional witness may be offline
        return None

    requested_best: float | None = None
    other_best: float | None = None
    other_name: str | None = None
    for way in data.get("elements", []):
        name = way.get("tags", {}).get("name")
        if not name:
            continue
        is_requested = bool(identity & geocoder_mod._identity_tokens(name))
        for pt in way.get("geometry", []):
            dist_m = haversine_km(lat, lon, pt["lat"], pt["lon"]) * 1000.0
            if is_requested:
                requested_best = dist_m if requested_best is None else min(requested_best, dist_m)
            elif other_best is None or dist_m < other_best:
                other_best, other_name = dist_m, name
    if requested_best is None:
        return None  # the requested street has no mapped lines nearby — no verdict
    if requested_best > 25.0 and other_best is not None and other_best < requested_best * 0.6:
        return (
            f"street_geometry_mismatch (closest other: {other_name} @ {other_best:.0f} m; "
            f"requested street @ {requested_best:.0f} m)"
        )
    return None


def door_numbers_near(lat: float, lon: float, radius: float = PICKUP_DOOR_RADIUS_M):
    """The INDEPENDENT house-number witness: real OSM door nodes
    (addr:housenumber) within `radius` of the point — no POI anchoring,
    no interpolation, the map's own doors. Returns the sorted number list,
    or None when Overpass is unreachable (the optional witness may be
    offline; report honestly, never crash)."""
    query = (
        f"[out:json][timeout:20];node(around:{int(radius)},{lat:.6f},{lon:.6f})"
        '["addr:housenumber"];out;'
    )
    request = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": geocoder_mod.USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - optional witness, honest degrade
        return None
    numbers = {
        element.get("tags", {}).get("addr:housenumber")
        for element in data.get("elements", [])
        if element.get("tags", {}).get("addr:housenumber")
    }
    return sorted(numbers)


def audit_pickups(session, settings, last_request: list[float]) -> list[dict]:
    """The PICKUP audit (#343 discussion, 2026-11-21): restaurants are seeded
    with raw coordinates and NO address, so nobody ever checked which street
    and door they actually sit on — the dropoff got a precision layer while
    the pickup stayed blind. This witness table makes pickups as auditable
    as dropoffs: the router-graph street (snap), the reverse geocode's
    street, and the map's own door numbers near the point. Two independent
    streets agreeing = confirmed; a disagreement or a blank = check it."""
    from domain.models import Restaurant

    reports: list[dict] = []
    for restaurant in session.scalars(select(Restaurant)).all():
        _pace(last_request)
        snap_distance, snap_road = geocoder_mod._snap(
            settings.osrm_base_url, restaurant.latitude, restaurant.longitude
        )
        _pace(last_request)
        reverse = geocoder_mod._reverse(
            geocoder_mod.NOMINATIM_REVERSE_URL, restaurant.latitude, restaurant.longitude
        )
        reverse_road = (reverse or {}).get("address", {}).get("road")
        doors = door_numbers_near(restaurant.latitude, restaurant.longitude)
        time.sleep(1.0)  # Overpass courtesy pacing

        snap_tokens = geocoder_mod._identity_tokens(snap_road)
        rev_tokens = geocoder_mod._identity_tokens(reverse_road)
        if snap_tokens and rev_tokens:
            confirmed = bool(snap_tokens & rev_tokens)
            verdict = "street_confirmed" if confirmed else "street_check"
        else:
            verdict = "unverified"
        snap_note = (
            f"{snap_road or '-'} ({snap_distance:.0f} m)" if snap_distance is not None else "-"
        )
        doors_note = f"doors {doors}" if doors is not None else "doors n/a (Overpass down)"
        reports.append(
            {
                "address": f"pickup · {restaurant.name}",
                "verdict": verdict,
                "detail": f"{snap_note} · reverse: {reverse_road or '-'} · {doors_note}",
            }
        )
    return reports


def drill_address(settings, address: str, last_request: list[float], deep: bool = False) -> dict:
    """Fresh forward geocode (cache bypassed) + all witnesses. The report."""
    _pace(last_request)
    try:
        results = geocoder_mod._forward(settings, address)
    except Exception as exc:  # noqa: BLE001 - the drill reports, never crashes
        return {"address": address, "verdict": "provider_error", "detail": str(exc)[:120]}
    if not results:
        return {"address": address, "verdict": "unresolvable", "detail": "no candidate returned"}
    best = results[0]
    lat, lon = float(best["lat"]), float(best["lon"])
    verdict = geocoder_mod._verify(settings, address, lat, lon)
    detail_parts = []
    if deep:
        deep_verdict = deep_street_check(address, lat, lon)
        if deep_verdict:
            verdict["precision"] = "street_mismatch"
            detail_parts.append(deep_verdict)
    snap = verdict.get("snap_distance_m")
    return {
        "address": address,
        "verdict": verdict["precision"],
        "detail": " · ".join(
            [
                f"({lat:.6f}, {lon:.6f})",
                f"snap {snap:.0f} m" if snap is not None else "snap n/a",
                f"reverse: {(verdict.get('reverse_display_name') or 'n/a')[:80]}",
                *detail_parts,
            ]
        ),
    }


def verify_cached(session, settings, last_request: list[float], fix: bool) -> list[dict]:
    """Re-verify every geocode_cache row at its STORED coordinates (the
    cheap drill: reverse + snap witnesses; the point itself is taken as
    given). --fix writes the fresh verdict back — this is how pre-#343
    rows graduate from 'unverified'."""
    reports: list[dict] = []
    for row in session.scalars(select(GeocodeCache)).all():
        _pace(last_request)
        verdict = geocoder_mod._verify(settings, row.address, row.lat, row.lon)
        snap = verdict.get("snap_distance_m")
        snap_note = f"snap {snap:.0f} m" if snap is not None else "snap n/a"
        reports.append(
            {
                "address": row.address,
                "verdict": verdict["precision"],
                "detail": f"cached ({row.lat:.6f}, {row.lon:.6f}) · {snap_note}",
            }
        )
        if fix and row.precision != verdict["precision"]:
            row.precision = verdict["precision"]
            row.reverse_display_name = verdict.get("reverse_display_name")
            row.snap_distance_m = snap
            session.commit()
    return reports


def probe_router(settings, points: list[tuple[float, float]], count: int) -> list[dict]:
    """Router sanity: N pairs through OSRM /route, flag detour ratios no
    sane city graph produces. Catches the mixed-generation-graph failure
    (the dual-router coin flip) for ANY city, not just Madrid."""
    reports: list[dict] = []
    pairs = [(points[i], points[(i + 1) % len(points)]) for i in range(min(count, len(points)))]
    for (a_lat, a_lon), (b_lat, b_lon) in pairs:
        url = f"{settings.osrm_base_url.rstrip('/')}/route/v1/driving/{a_lon:.6f},{a_lat:.6f};{b_lon:.6f},{b_lat:.6f}"
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(url, params={"overview": "false"})
                response.raise_for_status()
            route = response.json()["routes"][0]
        except Exception as exc:  # noqa: BLE001 - report, never crash
            reports.append(
                {
                    "address": f"router probe {a_lat:.4f},{a_lon:.4f} → {b_lat:.4f},{b_lon:.4f}",
                    "verdict": "router_unreachable",
                    "detail": str(exc)[:120],
                }
            )
            continue
        straight_m = haversine_km(a_lat, a_lon, b_lat, b_lon) * 1000.0
        ratio = route["distance"] / max(straight_m, 1.0)
        verdict = "ok" if ratio <= ROUTE_SANITY_RATIO else "suspect_router"
        reports.append(
            {
                "address": f"router probe {a_lat:.4f},{a_lon:.4f} → {b_lat:.4f},{b_lon:.4f}",
                "verdict": verdict,
                "detail": f"road {route['distance']:.0f} m vs straight {straight_m:.0f} m = {ratio:.1f}x",
            }
        )
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--file", help="text file, one address per line")
    source.add_argument("--address", help="a single address to drill")
    source.add_argument("--cached", action="store_true", help="re-verify every geocode_cache row")
    parser.add_argument(
        "--restaurants",
        action="store_true",
        help="audit every restaurant's pickup coordinates: street witnesses + real door numbers",
    )
    parser.add_argument(
        "--fix", action="store_true", help="write fresh verdicts back onto cache rows"
    )
    parser.add_argument(
        "--routes", type=int, default=0, metavar="N", help="also probe the router with N pairs"
    )
    parser.add_argument(
        "--deep", action="store_true", help="add the Overpass street-geometry witness"
    )
    args = parser.parse_args()
    if not (args.file or args.address or args.cached or args.restaurants):
        parser.error("choose a source: --file / --address / --cached / --restaurants")

    settings = get_settings()
    last_request = [0.0]
    session = sessionmaker(bind=engine)()
    try:
        if args.cached:
            reports = verify_cached(session, settings, last_request, args.fix)
        elif args.file or args.address:
            if args.file:
                lines = [
                    ln.strip()
                    for ln in Path(args.file).read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
            else:
                lines = [args.address]
            reports = [drill_address(settings, ln, last_request, deep=args.deep) for ln in lines]
            if args.fix:
                for report in reports:
                    if report["verdict"] in VERDICT_PASS | VERDICT_BAD | {"unverified"}:
                        row = session.get(GeocodeCache, geocoder_mod._cache_key(report["address"]))
                        if row is not None and row.precision != report["verdict"]:
                            row.precision = report["verdict"]
                            session.commit()
        else:
            reports = []

        if args.restaurants:
            reports.extend(audit_pickups(session, settings, last_request))

        if args.routes:
            points = [
                (row.lat, row.lon)
                for row in session.execute(
                    select(GeocodeCache.lat, GeocodeCache.lon).limit(max(args.routes, 2))
                ).all()
            ]
            if len(points) >= 2:
                reports.extend(probe_router(settings, points, args.routes))

        bad = 0
        width = max((len(r["address"]) for r in reports), default=20)
        print(f"{'VERDICT':<22}{'ADDRESS':<{min(width, 60)}}  DETAIL")
        for r in reports:
            mark = (
                "✗"
                if (
                    r["verdict"] in VERDICT_BAD
                    or r["verdict"] == "street_check"
                    or "error" in r["verdict"]
                    or "suspect" in r["verdict"]
                )
                else "✓"
            )
            if mark == "✗":
                bad += 1
            print(f"{mark} {r['verdict']:<20}{r['address'][:60]:<{min(width, 60)}}  {r['detail']}")
        print(
            f"\n{len(reports)} checked · {bad} flagged" + (" — FIX BEFORE GO-LIVE" if bad else "")
        )
        sys.exit(1 if bad else 0)
    finally:
        session.close()


if __name__ == "__main__":
    main()

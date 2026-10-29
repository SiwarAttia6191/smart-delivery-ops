"""Geo benchmark: measure distance providers against OSRM road distance.

Challenger-vs-champion harness (Phase 3, issue #24). Ground truth is the
self-hosted OSRM container; the champion (haversine) and the cheap
challenger (circuity x1.3) are scored against it by MAE / RMSE / MAPE /
mean bias, broken into distance buckets - because the worry is never
"average error", it is "is it terrible for long cross-zone trips?".

Snapshot convention (reproducibility): every report records max(event_id)
of the outbox and the git SHA, so any run can be traced to an exact
dataset. Data comes from two sources:
  - REAL pairs: created events that carry coordinates (the pipeline's own
    traffic),
  - SYNTHETIC pairs: a seeded, deterministic grid around the city's seed
    area (city manifest, #78), so every bucket has enough samples from
    day one.

Usage:
    python scripts/benchmark_geo.py                  # real + synthetic
    python scripts/benchmark_geo.py --synthetic 50   # more synthetic pairs
    python scripts/benchmark_geo.py --no-real        # synthetic only
    python scripts/benchmark_geo.py --region paris   # another city's grid
"""

import argparse
import math
import random
import statistics
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core.config import get_settings
from domain.city_config import BenchmarkGrid, load_city_config
from domain.models import OrderEvent
from domain.rules.geo import CircuityAdjustedProvider, HaversineProvider, OsrmProvider

BUCKETS = [(0.0, 2.0), (2.0, 5.0), (5.0, 10.0), (10.0, float("inf"))]
BUCKET_LABELS = ["<2km", "2-5km", "5-10km", ">10km"]


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:  # noqa: BLE001 - report must survive a non-git context
        return "unknown"


def real_pairs(session: Session) -> list[tuple[float, float, float, float]]:
    """Created events carrying coordinates - the pipeline's own traffic."""
    events = session.scalars(
        select(OrderEvent).where(OrderEvent.event_type == "created").order_by(OrderEvent.id)
    ).all()
    pairs = []
    for event in events:
        payload = event.payload or {}
        lat = payload.get("dropoff_latitude")
        lon = payload.get("dropoff_longitude")
        plat = payload.get("pickup_latitude")
        plon = payload.get("pickup_longitude")
        if None not in (lat, lon, plat, plon):
            assert lat is not None and lon is not None
            assert plat is not None and plon is not None
            pairs.append((float(plat), float(plon), float(lat), float(lon)))
    return pairs


def synthetic_pairs(
    count: int, grid: BenchmarkGrid, seed: int = 42
) -> list[tuple[float, float, float, float]]:
    """Deterministic grid sample around the city's seed area."""
    rng = random.Random(seed)
    return [
        (
            grid.center_lat + rng.uniform(-grid.spread_deg, grid.spread_deg),
            grid.center_lon + rng.uniform(-grid.spread_deg, grid.spread_deg),
            grid.center_lat + rng.uniform(-grid.spread_deg, grid.spread_deg),
            grid.center_lon + rng.uniform(-grid.spread_deg, grid.spread_deg),
        )
        for _ in range(count)
    ]


def bucket_stats(errors: list[float], truths: list[float]) -> dict:
    if not errors:
        return {"n": 0, "mae": None, "rmse": None, "mape": None, "bias_pct": None}
    return {
        "n": len(errors),
        "mae": round(statistics.mean(abs(e) for e in errors), 3),
        "rmse": round(math.sqrt(statistics.mean(e * e for e in errors)), 3),
        "mape": round(statistics.mean(abs(e) / t for e, t in zip(errors, truths) if t) * 100, 2),
        "bias_pct": round(statistics.mean(errors) / statistics.mean(truths) * 100, 2),
    }


def score(providers: dict, pairs: list, ground_truth: list[float]) -> dict:
    """Score each provider against ground truth, per distance bucket."""
    results: dict[str, dict] = {}
    for name, provider in providers.items():
        per_bucket = {}
        for (lo, hi), label in zip(BUCKETS, BUCKET_LABELS):
            errors: list[float] = []
            truths: list[float] = []
            for (lat1, lon1, lat2, lon2), truth in zip(pairs, ground_truth):
                if lo <= truth < hi:
                    estimate = provider.distance_km(lat1, lon1, lat2, lon2)
                    errors.append(estimate - truth)
                    truths.append(truth)
            per_bucket[label] = bucket_stats(errors, truths)
        results[name] = per_bucket
    return results


def render_report(results: dict, meta: dict) -> str:
    lines = [
        "# Geo distance benchmark",
        "",
        f"* Ground truth: OSRM road routing (self-hosted, `{meta['osrm_url']}`)",
        f"* Snapshot: `max(event_id)={meta['max_event_id']}`, git `{meta['git_sha']}`, "
        f"{meta['real_pairs']} real + {meta['synthetic_pairs']} synthetic pairs",
        f"* Generated {meta['generated_at']}",
        "",
        "Prior: haversine systematically UNDER-estimates road distance; "
        "circuity x1.3 should correct most of the bias offline. The per-bucket "
        "bias is the evidence.",
        "",
    ]
    for name, buckets in results.items():
        lines.append(f"## {name}")
        lines.append("")
        lines.append("| bucket | n | MAE (km) | RMSE (km) | bias % |")
        lines.append("|---|---|---|---|---|")
        for label in BUCKET_LABELS:
            m = buckets[label]
            if m["n"] == 0:
                lines.append(f"| {label} | 0 | - | - | - |")
            else:
                lines.append(f"| {label} | {m['n']} | {m['mae']} | {m['rmse']} | {m['bias_pct']} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", type=int, default=40, help="synthetic pairs to add")
    parser.add_argument("--osrm", default=None, help="OSRM base URL (default from settings)")
    parser.add_argument("--no-real", action="store_true", help="skip real order pairs")
    parser.add_argument("--region", default=None, help="city manifest (default CITY env / tunis)")
    args = parser.parse_args()

    settings = get_settings()
    osrm_url = (args.osrm or settings.osrm_base_url).rstrip("/")
    config = load_city_config(args.region or settings.city)

    pairs: list[tuple[float, float, float, float]] = []
    real_count = 0
    max_event_id = 0
    if not args.no_real:
        engine = create_engine(settings.database_url)
        with Session(engine) as session:
            pairs = real_pairs(session)
            max_event_id = (
                session.scalar(select(OrderEvent.id).order_by(OrderEvent.id.desc()).limit(1)) or 0
            )
        real_count = len(pairs)
    pairs += synthetic_pairs(args.synthetic, config.benchmark_grid)
    synthetic_count = len(pairs) - real_count

    osrm = OsrmProvider(osrm_url)
    # collect (pair, truth) TOGETHER: trimming pairs[:len(truth)] after the
    # loop misaligned every pair after a mid-run failure (review m3)
    scored: list[tuple[tuple[float, float, float, float], float]] = []
    skipped = 0
    for pair in pairs:
        try:
            scored.append((pair, osrm.distance_km(*pair)))
        except Exception as exc:  # noqa: BLE001 - one bad pair must not kill the run
            skipped += 1
            print(f"skip pair (OSRM failed: {exc})")
    pairs = [p for p, _ in scored]
    ground_truth = [t for _, t in scored]
    if skipped:
        print(f"WARNING: {skipped} pair(s) skipped - OSRM failures reduce coverage")

    providers = {
        "haversine (champion)": HaversineProvider(),
        "circuity x1.3": CircuityAdjustedProvider(HaversineProvider(), 1.3),
    }
    results = score(providers, pairs, ground_truth)
    meta = {
        "osrm_url": osrm_url,
        "max_event_id": max_event_id,
        "git_sha": git_sha(),
        "real_pairs": real_count,
        "synthetic_pairs": synthetic_count,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    report = render_report(results, meta)
    print(report)

    out_dir = Path("data/benchmarks")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"geo_benchmark_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text(report + "\n", encoding="utf-8")
    print(f"report saved: {out}")


if __name__ == "__main__":
    main()

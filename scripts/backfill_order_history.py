"""Backfill weeks of synthetic order history for the demo deployment.

The demand-forecasting benchmark (#25) trains on whatever the warehouse
holds. A demo deployment that has only seen a handful of manual test orders
has ~2 days of history - not enough for any model to stretch its legs. This
script backfills a few weeks of *synthetic but realistic* food-delivery
traffic for the configured city (manifest coordinates + volume, #78): lunch
and dinner peaks, quiet nights, heavier weekends.

Honesty contract (same one the benchmark harness follows):
- The harness itself never fabricates data - it reads the warehouse as-is.
- This backfill writes through the REAL pipeline: Order + outbox OrderEvent
  rows in the ops DB → relay → Kafka → ingest → staging → marts. Historical
  business timestamps ride the event payload, exactly like production events
  would.
- The benchmark report's snapshot (max(event_id) + git SHA) still identifies
  precisely which events any run trained on.

Each backfilled order carries its FULL event chain (created → assigned →
in_transit → delivered), because the warehouse's referential data test
assert_delivered_orders_have_assignments fails the stream otherwise - the
dbt contract policing the backfill is the point. Driver-side availability
events are deliberately NOT backfilled: supply history is a separate story
(#60), and mixing it in here would silently rewrite the supply marts.

Usage (repo root, needs `docker compose up -d postgres worker ingest`):
    python scripts/backfill_order_history.py                # 180 days
    python scripts/backfill_order_history.py --days 90
    BUSINESS_TIMEZONE=Europe/Paris python scripts/backfill_order_history.py --region paris

The noise model (#86 data depth) is deliberately richer than clean Poisson:
per-zone base-rate heterogeneity (zone volumes differ up to ~3.6x), a slow
multi-week drift with a ~6-week wave, weekend/holiday-adjacent spikes,
weather-day dips, occasional supply-outage hours, a day-of-month effect,
and gamma-mixed (overdispersed) counts so residuals aren't suspiciously
tight. Everything is seed-deterministic and printed before it is applied.
"""

import argparse
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import engine  # noqa: E402
from domain.city_config import CityConfig, ensure_business_timezone, load_city_config  # noqa: E402
from domain.models import Customer, Driver, Order, OrderEvent, OrderStatus, Restaurant  # noqa: E402
from domain.services import eta_service, pricing_service  # noqa: E402

# Local business hours derive in the configured BUSINESS_TIMEZONE (#78)
# through the tz database - no fixed offsets (DST-safe).


# Orders per hour at weight 1.0; the shape below is classic food-delivery:
# dead nights, a morning trickle, a 12-13h lunch spike, a 19-20h dinner peak.
BASE_RATE = 2.4
HOUR_WEIGHTS = {
    0: 0.08,
    1: 0.05,
    2: 0.05,
    3: 0.05,
    4: 0.08,
    5: 0.15,
    6: 0.30,
    7: 0.50,
    8: 0.60,
    9: 0.50,
    10: 0.70,
    11: 1.40,
    12: 2.20,
    13: 2.00,
    14: 1.20,
    15: 0.70,
    16: 0.70,
    17: 0.80,
    18: 1.20,
    19: 1.90,
    20: 1.90,
    21: 1.40,
    22: 0.90,
    23: 0.40,
}
WEEKEND_FACTOR = 1.35  # Sat/Sun
JITTER_DEG = 0.006  # dropoff scatter around the customer (~±650 m)

# ── Noise realism (#86): structure the models must actually track ──
GROWTH_ACROSS_WINDOW = 0.45  # slow upward drift over the full window
DRIFT_WAVE_DAYS = 42  # a ~6-week demand wave on top of the trend
DAY_OF_MONTH_AMPLITUDE = 0.12  # ±12% payday-shaped monthly effect
HOLIDAY_COUNT, HOLIDAY_FACTOR = 4, (1.9, 2.3)  # spike days
WEATHER_COUNT, WEATHER_FACTOR = 8, (0.45, 0.60)  # rain-day dips
OUTAGE_COUNT, OUTAGE_HOURS, OUTAGE_FACTOR = 10, (2, 4), 0.12  # near-dead hours
ZONE_RATE_RANGE = (0.5, 1.8)  # per-zone (restaurant) base-rate heterogeneity
OVERDISPERSION_SHAPE = 8.0  # gamma-mixed lambda → negative-binomial-ish counts

# Backfilled deliveries land in the 12-38 minute band - within the ETA
# model's range (1-6 min to assign, 3-8 to collect, 8-24 to deliver).


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's sampler - lambdas here stay small, so the multiply loop is fine."""
    l_minus = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= l_minus:
            return k
        k += 1


def local_to_utc(local: datetime) -> datetime:
    return local.replace(tzinfo=ZoneInfo(get_settings().business_timezone)).astimezone(UTC)


def build_noise_calendar(rng: random.Random, days: int, start_local: datetime) -> dict:
    """Seed-deterministic structure the models must track (printed before
    the backfill applies it, so the run log names what it contains)."""
    holiday_days = sorted(rng.sample(range(days), HOLIDAY_COUNT)) if days > HOLIDAY_COUNT else []
    weather_days = sorted(
        rng.sample(
            [d for d in range(days) if d not in holiday_days],
            min(WEATHER_COUNT, max(days - HOLIDAY_COUNT, 0)),
        )
    )
    outages = []
    for _ in range(OUTAGE_COUNT):
        day = rng.randrange(days)
        hour = rng.randrange(10, 23)  # inside business hours, hurts where it shows
        length = rng.randint(*OUTAGE_HOURS)
        outages.append(
            (
                start_local + timedelta(days=day, hours=hour),
                start_local + timedelta(days=day, hours=hour + length),
            )
        )
    return {
        "holidays": {d: rng.uniform(*HOLIDAY_FACTOR) for d in holiday_days},
        "weather": {d: rng.uniform(*WEATHER_FACTOR) for d in weather_days},
        "outages": outages,
    }


def day_multiplier(noise: dict, day_index: int, days: int, day_local: datetime) -> float:
    """Trend × wave × day-of-month × holiday × weather."""
    m = 1.0 + GROWTH_ACROSS_WINDOW * (day_index / max(days - 1, 1))
    m *= 1.0 + 0.18 * math.sin(2 * math.pi * day_index / DRIFT_WAVE_DAYS)
    m *= 1.0 + DAY_OF_MONTH_AMPLITUDE * math.sin(2 * math.pi * (day_local.day - 1) / 30.44)
    m *= noise["holidays"].get(day_index, 1.0)
    m *= noise["weather"].get(day_index, 1.0)
    return m


def in_outage(noise: dict, moment_local: datetime) -> bool:
    return any(start <= moment_local < end for start, end in noise["outages"])


def build_order(
    rng: random.Random,
    session,
    restaurant: Restaurant,
    customer: Customer,
    driver: Driver,
    created_utc: datetime,
) -> None:
    drop_lat = customer.latitude + rng.uniform(-JITTER_DEG, JITTER_DEG)
    drop_lon = customer.longitude + rng.uniform(-JITTER_DEG, JITTER_DEG)
    tenant_id = restaurant.tenant_id

    quote = pricing_service.quote_delivery(
        restaurant.latitude, restaurant.longitude, drop_lat, drop_lon
    )

    # Lifecycle timing: assignment a few minutes after creation, pickup a
    # few more, delivery inside the 18-42 minute band (always monotonic).
    assigned_utc = created_utc + timedelta(minutes=rng.randint(1, 6))
    in_transit_utc = assigned_utc + timedelta(minutes=rng.randint(3, 8))
    delivered_utc = in_transit_utc + timedelta(minutes=rng.randint(8, 24))

    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        restaurant_id=restaurant.id,
        driver_id=driver.id,
        status=OrderStatus.delivered,
        pickup_latitude=restaurant.latitude,
        pickup_longitude=restaurant.longitude,
        dropoff_latitude=drop_lat,
        dropoff_longitude=drop_lon,
        distance_km=quote.distance_km,
        delivery_fee=quote.breakdown.total,
        surge_multiplier=quote.breakdown.surge_multiplier,
        eta_minutes=eta_service.estimate_eta_minutes(quote.distance_km),
        created_at=created_utc,
        updated_at=delivered_utc,
        delivered_at=delivered_utc,
    )
    session.add(order)
    session.flush()  # order.id (uuid default) must exist before the events

    # Payloads mirror order_service.create_order / assign_driver / transition
    # exactly - the warehouse staging models read these keys, so hand-rolling
    # different ones would silently degrade the marts.
    session.add(
        OrderEvent(
            order_id=order.id,
            tenant_id=tenant_id,
            event_type="created",
            payload={
                "customer_id": customer.id,
                "restaurant_id": restaurant.id,
                "distance_km": quote.distance_km,
                "delivery_fee": quote.breakdown.total,
                "surge_multiplier": quote.breakdown.surge_multiplier,
                "eta_minutes": order.eta_minutes,
                "pickup_latitude": restaurant.latitude,
                "pickup_longitude": restaurant.longitude,
                "dropoff_latitude": drop_lat,
                "dropoff_longitude": drop_lon,
            },
            created_at=created_utc,
        )
    )
    session.add(
        OrderEvent(
            order_id=order.id,
            tenant_id=tenant_id,
            event_type="assigned",
            payload={
                "driver_id": driver.id,
                "match_distance_km": round(rng.uniform(0.2, 3.5), 2),
                "vehicle": driver.vehicle,
            },
            created_at=assigned_utc,
        )
    )
    session.add(
        OrderEvent(
            order_id=order.id,
            tenant_id=tenant_id,
            event_type="status",
            payload={"from": "assigned", "to": "in_transit"},
            created_at=in_transit_utc,
        )
    )
    session.add(
        OrderEvent(
            order_id=order.id,
            tenant_id=tenant_id,
            event_type="status",
            payload={"from": "in_transit", "to": "delivered"},
            created_at=delivered_utc,
        )
    )


def backfill(days: int, seed: int, config: CityConfig) -> None:
    # The environment zone is what dbt grains and ML features derive in;
    # writing a city's history under a different zone would mis-grain it.
    ensure_business_timezone(config, get_settings().business_timezone)
    rng = random.Random(seed)
    session = sessionmaker(bind=engine)()
    try:
        # Refuse to run twice: a second backfill would duplicate history and
        # double-count demand in every mart. (Reset via warehouse + ops DB
        # truncation if a redo is ever needed.)
        oldest = session.scalar(select(Order.created_at).order_by(Order.created_at.asc()).limit(1))
        if oldest is not None and oldest < datetime.now(UTC) - timedelta(days=3):
            print(
                f"Orders already go back to {oldest:%Y-%m-%d} - history looks "
                "backfilled already; refusing to duplicate it."
            )
            return

        restaurants = session.scalars(select(Restaurant)).all()
        customers = session.scalars(select(Customer)).all()
        drivers = session.scalars(select(Driver)).all()
        if not restaurants or not customers or not drivers:
            print("No restaurants/customers/drivers - run scripts/seed_demo_data.py first.")
            return

        now_local = datetime.now(UTC).astimezone(ZoneInfo(config.business_timezone))
        start_local = (now_local - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        noise = build_noise_calendar(rng, days, start_local)
        zone_rates = {r.id: rng.uniform(*ZONE_RATE_RANGE) for r in restaurants}
        zone_ids = list(zone_rates)
        zone_weights = [zone_rates[i] for i in zone_ids]
        print(
            f"noise calendar: holidays {len(noise['holidays'])} days "
            f"(x{min(noise['holidays'].values()):.1f}-{max(noise['holidays'].values()):.1f}), "
            f"weather dips {len(noise['weather'])} days, outages {len(noise['outages'])} "
            f"windows, zone rate spread {min(zone_weights):.2f}-{max(zone_weights):.2f}, "
            f"drift x{1 + GROWTH_ACROSS_WINDOW:.2f} over the window, wave {DRIFT_WAVE_DAYS}d"
        )

        total_orders = 0
        for day in range(days):
            day_local = start_local + timedelta(days=day)
            weekend = day_local.weekday() >= 5
            day_factor = day_multiplier(noise, day, days, day_local)
            for hour, weight in HOUR_WEIGHTS.items():
                lam = config.backfill.base_rate * weight * (WEEKEND_FACTOR if weekend else 1.0)
                lam *= day_factor
                created_local = day_local.replace(hour=hour) + timedelta(minutes=rng.randint(0, 59))
                if in_outage(noise, created_local):
                    lam *= OUTAGE_FACTOR  # supply outage: the platform is effectively down
                # overdispersion: gamma-mixed lambda (counts vary more than Poisson)
                lam = rng.gammavariate(OVERDISPERSION_SHAPE, lam / OVERDISPERSION_SHAPE)
                for _ in range(poisson(rng, lam)):
                    # zone choice follows the heterogeneous base rates; the
                    # ids come from the restaurants table just loaded, so the
                    # get() hit is guaranteed
                    restaurant = session.get(
                        Restaurant, rng.choices(zone_ids, weights=zone_weights)[0]
                    )
                    assert restaurant is not None
                    build_order(
                        rng,
                        session,
                        restaurant,
                        rng.choice(customers),
                        rng.choice(drivers),
                        local_to_utc(created_local),
                    )
                    total_orders += 1
            if day % 7 == 6:
                session.commit()
                print(f"  {day + 1}/{days} days, {total_orders} orders")
        session.commit()

        print(
            f"Backfilled {total_orders} delivered orders over {days} days "
            f"({start_local:%Y-%m-%d} → {day_local:%Y-%m-%d} local), seed={seed}."
        )
        print(
            "Next: the running relay/ingest drain them (watch `docker compose logs "
            "-f ingest`), then `make dbt`, then `make ml-benchmark`."
        )
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--region", default=get_settings().city)
    args = parser.parse_args()
    backfill(args.days, args.seed, load_city_config(args.region))

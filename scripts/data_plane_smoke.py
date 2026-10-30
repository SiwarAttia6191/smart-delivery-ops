"""Data-plane smoke test (#46) — the CI job's engine, also runnable locally.

Exercises the REAL pipeline pieces against throwaway Postgres + Kafka:
ops tables → outbox OrderEvent → relay_once (real confluent producer) →
orders.events topic → parse_message + RawEventStore.store (the ingest
codepath) → raw.order_events row. Optionally `--with-dbt` also runs a full
dbt build over the warehouse (CI runs it as a separate step for clearer
logs; the DB contract is the expensive part CI wants caught).

Every step exits non-zero on failure - never pipe-masked.

Usage:
    python scripts/data_plane_smoke.py            # roundtrip only
    python scripts/data_plane_smoke.py --with-dbt # + full dbt build
"""

import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.database import run_migrations  # noqa: E402
from domain.models import Customer, OrderEvent, Restaurant  # noqa: E402
from domain.services.order_service import create_order
from domain.services.tenant_service import default_id  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def step(name: str) -> None:
    print(f"\n=== {name} ===", flush=True)


def wait_for_kafka(bootstrap: str, timeout_s: float = 60.0) -> None:
    from confluent_kafka.admin import AdminClient

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            metadata = AdminClient({"bootstrap.servers": bootstrap}).list_topics(timeout=5)
            if metadata.brokers:
                return
        except Exception:
            time.sleep(2)
    raise SystemExit(f"kafka not reachable at {bootstrap} within {timeout_s:.0f}s")


def create_topics(bootstrap: str, topics: list[str]) -> None:
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": bootstrap})
    existing = set(admin.list_topics(timeout=10).topics)
    missing = [
        NewTopic(t, num_partitions=1, replication_factor=1) for t in topics if t not in existing
    ]
    if missing:
        for topic, future in admin.create_topics(missing).items():
            future.result(timeout=30)
            print(f"topic created: {topic}")


def seed_order(session_factory) -> tuple[int, str]:
    """Real order_service.create_order - the same code path as production
    (route capture degrades to None when no OSRM is around; the ETA model
    flag is unset in smoke, so the heuristic answers)."""
    with session_factory() as session:
        customer = Customer(
            name="Smoke", phone="+216 20 000 000", latitude=36.8381, longitude=10.2385
        )
        restaurant = Restaurant(name="Smoke Kitchen", latitude=36.8065, longitude=10.1815)
        tenant_id = default_id(session)
        customer.tenant_id = tenant_id
        restaurant.tenant_id = tenant_id
        session.add_all([customer, restaurant])
        session.commit()
        order = create_order(
            session,
            customer_id=customer.id,
            restaurant_id=restaurant.id,
            dropoff_lat=36.8381,
            dropoff_lon=10.2385,
        )
        event_id = session.query(OrderEvent).order_by(OrderEvent.id.desc()).first().id
        return event_id, order.id


def relay_events(bootstrap: str, topic: str, batch_size: int = 100) -> int:
    from apps.worker.relay import KafkaEventPublisher, relay_once
    from core.database import engine

    publisher = KafkaEventPublisher(bootstrap)
    with sessionmaker(bind=engine)() as session:
        relayed = relay_once(session, publisher, topic, batch_size)
    publisher.flush()
    failures = publisher.take_delivery_failures()
    assert failures == [], f"{len(failures)} event(s) failed delivery: {failures}"
    return relayed


def consume_and_assert(bootstrap: str, topic: str, warehouse_url: str, event_id: int) -> None:
    """The ingest codepath: parse_message + RawEventStore.store, driven by a
    one-shot consumer (same config semantics as ingest.main, bounded)."""
    from apps.worker.ingest import RawEventStore, parse_message

    store = RawEventStore(warehouse_url)
    store.ensure()

    from confluent_kafka import Consumer

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"data-plane-smoke-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])

    deadline = time.time() + 45
    inserted = quarantined = 0
    try:
        while time.time() < deadline:
            batch = consumer.consume(num_messages=100, timeout=2.0)
            if not batch:
                continue
            records = []
            for msg in batch:
                record = parse_message(msg.value())
                if record is None:
                    continue
                record["kafka_partition"] = msg.partition()
                record["kafka_offset"] = msg.offset()
                records.append(record)
            if records:
                added, quarantined_now = store.store(records)
                inserted += added
                quarantined += quarantined_now
            with create_engine(warehouse_url).connect() as conn:
                hit = conn.execute(
                    text("SELECT count(*) FROM raw.order_events WHERE event_id = :eid"),
                    {"eid": event_id},
                ).scalar_one()
                if hit:
                    print(
                        f"raw.order_events row found for event {event_id} (inserted={inserted}, quarantined={quarantined})"
                    )
                    return
        raise SystemExit(f"event {event_id} never reached raw.order_events within 45s")
    finally:
        consumer.close()


def dbt_build() -> None:
    env = os.environ | {
        "DBT_HOST": "localhost",
        "DBT_PORT": os.environ.get("DBT_PORT", "5432"),
        "DBT_LOG_PATH": "/tmp/dbt-logs",
        "DBT_TARGET_PATH": "/tmp/dbt-target",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dbt",
            "build",
            "--project-dir",
            str(REPO / "data" / "transform"),
            "--profiles-dir",
            str(REPO / "data" / "transform"),
            "--no-use-colors",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-6:])
    print(tail)
    if result.returncode != 0:
        raise SystemExit(f"dbt build failed with exit {result.returncode}")


def rls_proof(engine) -> None:
    """#36 acceptance: isolation enforced by the DATABASE ENGINE.

    Two tenants, one customer row each. Every session goes through the SAME
    mechanics the app uses (core.tenancy pins app.current_tenant per
    transaction via set_config LOCAL) — then we assert the engine, not a
    WHERE clause, hid the other tenant. Also proves the WITH CHECK side
    (a session cannot INSERT into someone else's tenant) and the explicit
    bypass GUC the internal services (relay, benchmarks) rely on.
    """
    from core.tenancy import TENANT_GUC, reset_request_tenant, set_request_tenant
    from domain.models import Customer, Tenant

    step("6 · RLS proof — two tenants, enforced by Postgres")
    # The bypass engine (the one internal services use) also performs the
    # SETUP: creating tenants + their rows is a platform operation no
    # single tenant scope could do (WITH CHECK would reject it).
    import core.tenancy

    bypass_engine = create_engine(engine.url)  # NOT str(url): that masks the password
    core.tenancy.mark_tenant_bypass(bypass_engine)
    with sessionmaker(bind=bypass_engine)() as s:
        a = Tenant(slug="rls-a", name="RLS proof A")
        b = Tenant(slug="rls-b", name="RLS proof B")
        s.add_all([a, b])
        s.flush()
        s.add(Customer(tenant_id=a.id, name="cust-a", latitude=36.8, longitude=10.18))
        s.add(Customer(tenant_id=b.id, name="cust-b", latitude=36.8, longitude=10.18))
        s.commit()
        a_id, b_id = a.id, b.id

    # THE PROOF RUNS AS A LEAST-PRIVILEGE ROLE, not the bootstrap superuser:
    # a superuser (POSTGRES_USER) bypasses RLS by definition, so proving
    # against it would prove nothing. sdo_smoke is what a real app
    # deployment connects as.
    with bypass_engine.connect() as conn:
        conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sdo_smoke') "
                "THEN CREATE ROLE sdo_smoke LOGIN PASSWORD 'sdo_smoke'; END IF; END $$"
            )
        )
        conn.execute(text("GRANT USAGE ON SCHEMA public TO sdo_smoke"))
        conn.execute(
            text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sdo_smoke")
        )
        conn.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sdo_smoke"))
        conn.commit()  # 2.0-style: no explicit commit = rolled back on close
    # str(URL) masks the password as *** — build from the URL object
    app_url = engine.url.set(username="sdo_smoke", password="sdo_smoke")
    app_engine = create_engine(app_url)
    app_factory = sessionmaker(bind=app_engine)

    def scoped_count(tenant_id: int) -> int:
        reset = set_request_tenant(tenant_id)
        try:
            with app_factory() as s:
                # a deliberate full scan: only RLS may narrow what comes back
                return len(s.query(Customer).all())
        finally:
            reset_request_tenant(reset)

    seen_a, seen_b, seen_default = scoped_count(a_id), scoped_count(b_id), scoped_count(1)
    # each scope sees exactly its own row: A→1, B→1, default→1 (the customer
    # step 3 seeded through the real service). No scope sees a neighbor's row.
    assert seen_a == 1, f"tenant A should see exactly its 1 customer, saw {seen_a}"
    assert seen_b == 1, f"tenant B should see exactly its 1 customer, saw {seen_b}"
    assert seen_default == 1, (
        f"default tenant should see exactly its 1 seeded customer, saw {seen_default}"
    )
    print(f"scoped SELECT: A saw {seen_a}, B saw {seen_b}, default saw {seen_default} — no leaks")

    # WITH CHECK: writing into another tenant's scope must be rejected
    denied = False
    reset = set_request_tenant(a_id)
    try:
        with app_factory() as s:
            s.add(Customer(tenant_id=b_id, name="smuggled", latitude=0.0, longitude=0.0))
            try:
                s.commit()
            except Exception:
                denied = True
                s.rollback()
    finally:
        reset_request_tenant(reset)
    assert denied, "RLS WITH CHECK must reject a cross-tenant INSERT"
    print("cross-tenant INSERT: rejected by WITH CHECK")

    # bypass: the flag relay/benchmarks set must see across tenants — also
    # proven as the least-privilege role (bypass is a GUC, not a privilege).
    # dispose() first: the scoped phase's connections are pooled WITHOUT
    # the bypass GUC (connect events fire only on new connections).
    core.tenancy.mark_tenant_bypass(app_engine)
    app_engine.dispose()
    with app_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar_one()
    app_engine.dispose()
    assert count >= 3, f"bypass session should see all tenants' customers, saw {count}"
    print(f"bypass session: saw {count} customers across tenants (>= 3 expected)")

    # and a plain GUC sanity: the app GUC name is exactly what policies use
    with engine.connect() as conn:
        conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '1', true)"))
        val = conn.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)")).scalar_one()
    assert val == "1", f"GUC roundtrip failed: {val!r}"
    print("GUC roundtrip ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-dbt", action="store_true")
    args = parser.parse_args()

    ops_url = os.environ["DATABASE_URL"]
    warehouse_url = os.environ["WAREHOUSE_DATABASE_URL"]
    bootstrap = os.environ["KAFKA_BOOTSTRAP_SERVERS"]

    step("1 · ops schema via the Alembic chain + warehouse raw layer")
    # The chain itself is under test here: a fresh throwaway Postgres must
    # reach head and produce the exact schema the models expect.
    run_migrations()
    engine = create_engine(ops_url)
    from apps.worker.ingest import RawDriverEventStore, RawEventStore

    RawEventStore(warehouse_url).ensure()
    RawDriverEventStore(warehouse_url).ensure()
    print("ops tables + raw.order_events / raw.driver_events ready")

    step("2 · kafka topics")
    wait_for_kafka(bootstrap)
    create_topics(
        bootstrap,
        [
            os.environ.get("KAFKA_ORDERS_TOPIC", "orders.events"),
            os.environ.get("KAFKA_DRIVERS_TOPIC", "drivers.events"),
        ],
    )

    step("3 · seed one order through the real service")
    session_factory = sessionmaker(bind=engine)
    event_id, order_id = seed_order(session_factory)
    print(f"order {order_id[:8]}… seeded, outbox event {event_id} (published_at NULL)")

    step("4 · relay_once → orders.events (real confluent producer)")
    relayed = relay_events(bootstrap, os.environ.get("KAFKA_ORDERS_TOPIC", "orders.events"))
    assert relayed >= 1, f"relay published {relayed} events"
    print(f"relayed {relayed} event(s)")

    step("5 · ingest codepath → raw.order_events")
    consume_and_assert(
        bootstrap, os.environ.get("KAFKA_ORDERS_TOPIC", "orders.events"), warehouse_url, event_id
    )

    if args.with_dbt:
        step("6 · dbt build over the throwaway warehouse")
        dbt_build()

    rls_proof(engine)

    step("SMOKE PASS")


if __name__ == "__main__":
    main()

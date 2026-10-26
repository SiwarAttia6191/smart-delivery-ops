"""Unit tests for the warehouse ingestion consumer (apps/worker/ingest.py).

Hermetic: the raw-layer store runs on the shared SQLite fixture with no
schema qualifier and no Kafka broker; parse logic, primary-key dedupe, and
batch behavior are what's under test. The Postgres-specific pieces (raw
schema, JSONB, ON CONFLICT, warehouse database bootstrap) are verified live.
"""

import json

from sqlalchemy import create_engine, inspect, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.pool import StaticPool

from apps.worker.ingest import (
    RawDriverEventStore,
    RawEventStore,
    parse_driver_message,
    parse_message,
)


def make_store() -> RawEventStore:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    return RawEventStore("sqlite://", schema=None, engine=engine)


def make_driver_store() -> RawDriverEventStore:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    return RawDriverEventStore("sqlite://", schema=None, engine=engine)


def wire_record(event_id: int = 1, event_type: str = "created") -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "event_id": event_id,
            "order_id": f"ORD-{event_id}",
            "event_type": event_type,
            "payload": {"note": event_type},
            "created_at": "2026-10-25T18:31:19.371540+00:00",
        }
    ).encode("utf-8")


def driver_wire_record(event_id: int = 1, driver_id: int = 17) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "event_id": event_id,
            "driver_id": driver_id,
            "event_type": "availability",
            "payload": {"from_available": True, "to_available": False},
            "created_at": "2026-10-25T18:31:19.371540+00:00",
        }
    ).encode("utf-8")


class TestParseMessage:
    def test_valid_message_normalizes(self):
        record = parse_message(wire_record())
        assert record is not None
        assert record["event_id"] == 1
        assert record["order_id"] == "ORD-1"
        assert record["event_type"] == "created"
        assert record["schema_version"] == 1
        assert json.loads(record["payload"]) == {"note": "created"}
        assert record["source_created_at"] == "2026-10-25T18:31:19.371540+00:00"

    def test_not_json_rejected(self):
        assert parse_message(b"not json at all") is None
        assert parse_message(None) is None

    def test_not_an_object_rejected(self):
        assert parse_message(b'["a", "list"]') is None

    def test_missing_required_field_rejected(self):
        broken = json.dumps({"event_id": 1, "order_id": "ORD-1"})  # no event_type/payload...
        assert parse_message(broken.encode()) is None

    def test_event_id_must_be_an_int(self):
        assert (
            parse_message(json.dumps({**json.loads(wire_record()), "event_id": "5"}).encode())
            is None
        )
        assert (
            parse_message(json.dumps({**json.loads(wire_record()), "event_id": True}).encode())
            is None
        )

    def test_order_id_must_be_a_string(self):
        assert (
            parse_message(json.dumps({**json.loads(wire_record()), "order_id": 7}).encode()) is None
        )

    def test_driver_event_normalizes_integer_driver_id(self):
        record = parse_driver_message(driver_wire_record())

        assert record is not None
        assert record["driver_id"] == 17
        assert record["event_type"] == "availability"
        assert json.loads(record["payload"])["to_available"] is False

    def test_driver_id_must_be_an_integer(self):
        broken = json.loads(driver_wire_record())
        broken["driver_id"] = "17"

        assert parse_driver_message(json.dumps(broken)) is None


class TestRawEventStore:
    def test_ensure_creates_table_and_store_roundtrip(self):
        store = make_store()
        store.ensure()

        engine = store._engine
        assert inspect(engine).has_table("order_events")
        assert sa_inspect(engine).has_table("ingest_dead_letters")
        record = parse_message(wire_record())
        assert record is not None
        assert store.store([record]) == (1, 0)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT event_id, order_id, event_type FROM order_events")
            ).one()
        assert row == (1, "ORD-1", "created")

    def test_replay_of_same_event_id_dedupes_to_zero(self):
        store = make_store()
        store.ensure()

        first = parse_message(wire_record())
        replay = parse_message(wire_record())
        assert first is not None and replay is not None
        assert store.store([first]) == (1, 0)
        assert store.store([replay]) == (0, 0)  # replay is a no-op

    def test_batch_store_with_partial_duplicates(self):
        store = make_store()
        store.ensure()
        stored = parse_message(wire_record(event_id=1))
        replayed = parse_message(wire_record(event_id=1))
        fresh = parse_message(wire_record(event_id=2))
        assert stored is not None and replayed is not None and fresh is not None
        store.store([stored])

        inserted, quarantined = store.store([replayed, fresh])

        assert (inserted, quarantined) == (1, 0)  # only the new event lands
        with store._engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM order_events")).scalar_one()
        assert count == 2

    def test_row_level_rejection_is_quarantined_not_batch_fatal(self):
        store = make_store()
        store.ensure()
        good = parse_message(wire_record(event_id=1))
        assert good is not None
        poison = dict(good, event_type=None)  # NOT NULL violation -> IntegrityError
        # (SQLite would auto-assign a NULL integer PK; NOT NULL is enforced)

        inserted, quarantined = store.store([poison, good])

        assert (inserted, quarantined) == (1, 1)
        with store._engine.connect() as conn:
            dead = conn.execute(text("SELECT event_id, error FROM ingest_dead_letters")).one()
        assert dead.error  # reason preserved
        # The good event right after the poison one still landed
        assert conn_execute_count(store) == 1

    def test_distinct_events_share_the_table(self):
        store = make_store()
        store.ensure()
        first = parse_message(wire_record(event_id=1))
        second = parse_message(wire_record(event_id=2, event_type="assigned"))
        assert first is not None and second is not None
        assert store.store([first, second]) == (2, 0)


class TestRawDriverEventStore:
    def test_driver_event_store_roundtrip_and_deduplication(self):
        store = make_driver_store()
        store.ensure()
        record = parse_driver_message(driver_wire_record())
        assert record is not None

        assert inspect(store._engine).has_table("driver_events")
        assert store.store([record]) == (1, 0)
        assert store.store([record]) == (0, 0)
        with store._engine.connect() as conn:
            row = conn.execute(
                text("SELECT event_id, driver_id, event_type FROM driver_events")
            ).one()
        assert row == (1, 17, "availability")


def conn_execute_count(store: RawEventStore) -> int:
    with store._engine.connect() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM order_events")).scalar_one())

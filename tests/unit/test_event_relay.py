"""Unit tests for the outbox relay (apps/worker/relay.py) — fully hermetic:
a fake publisher stands in for Kafka, the session runs on the shared SQLite
fixture, and no broker is ever contacted.

Note on locking: the claim query uses FOR UPDATE SKIP LOCKED, which SQLite
silently ignores — so the locking semantics themselves are exercised only on
PostgreSQL. What these tests CAN pin hermetically is the selection logic
(claim-based, not offset-based), the delivery-gating invariant, and the
at-least-once replay behavior.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from apps.worker.relay import SCHEMA_VERSION, relay_once
from domain.models import OrderEvent, RelayState


class FakePublisher:
    """In-memory stand-in. Can simulate the failure modes that matter:
    produce() raising mid-batch, and flush() failing to confirm delivery."""

    def __init__(self, fail_flush: bool = False, fail_produce_after: int | None = None) -> None:
        self.sent: list[tuple[str, str, str, list[tuple[str, str | bytes | None]] | None]] = []
        self.fail_flush = fail_flush
        self.fail_produce_after = fail_produce_after  # raise on the Nth produce call

    def produce(
        self,
        topic: str,
        key: str,
        value: str,
        headers: list[tuple[str, str | bytes | None]] | None = None,
    ) -> None:
        if self.fail_produce_after is not None and len(self.sent) >= self.fail_produce_after:
            raise RuntimeError("produce queue full")
        self.sent.append((topic, key, value, headers))

    def flush(self) -> None:
        if self.fail_flush:
            raise RuntimeError("delivery not confirmed")


def add_event(session: Session, order_id: str, event_type: str = "created") -> OrderEvent:
    event = OrderEvent(order_id=order_id, event_type=event_type, payload={"note": event_type})
    session.add(event)
    session.commit()
    return event


def published_ids(session: Session) -> list[int]:
    session.expire_all()  # re-read post-rollback state, not cached objects
    return [
        e.id for e in session.query(OrderEvent).filter(OrderEvent.published_at.isnot(None)).all()
    ]


class TestClaimBasedSelection:
    def test_publishes_due_events_keyed_by_order_in_id_order(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        add_event(db, "ORD-1", "assigned")
        publisher = FakePublisher()

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 3
        assert [key for _, key, _, _ in publisher.sent] == ["ORD-1", "ORD-2", "ORD-1"]
        assert all(topic == "orders.events" for topic, _, _, _ in publisher.sent)
        records = [json.loads(value) for _, _, value, _ in publisher.sent]
        assert [r["event_id"] for r in records] == [1, 2, 3]
        assert records[0]["event_type"] == "created"
        assert records[2]["event_type"] == "assigned"

    def test_rows_ignored_by_offset_are_still_claimed(self, db: Session):
        """The old offset cursor would have skipped these — e.g. after a
        sequence value was burned by a rolled-back insert. Claim-based
        selection relays whatever rows exist and are due."""
        first = add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        first.published_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
        publisher = FakePublisher()

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 1
        assert [key for _, key, _, _ in publisher.sent] == ["ORD-2"]

    def test_marked_rows_are_never_relayed_again(self, db: Session):
        add_event(db, "ORD-1")
        publisher = FakePublisher()
        relay_once(db, publisher, "orders.events")

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 0
        assert len(publisher.sent) == 1  # nothing re-sent

    def test_batch_limit_and_resume(self, db: Session):
        for i in range(3):
            add_event(db, f"ORD-{i}")
        publisher = FakePublisher()

        first = relay_once(db, publisher, "orders.events", batch_size=2)
        second = relay_once(db, publisher, "orders.events", batch_size=2)

        assert (first, second) == (2, 1)
        assert len(publisher.sent) == 3

    def test_empty_log_writes_no_state_row(self, db: Session):
        publisher = FakePublisher()

        relayed = relay_once(db, publisher, "orders.events")

        assert relayed == 0
        assert db.get(RelayState, 1) is None


class TestDeliveryGating:
    """The at-least-once invariant: the batch is marked published ONLY after
    the publisher confirms delivery. Any failure must leave every row due."""

    def test_flush_failure_marks_nothing_and_raises(self, db: Session):
        add_event(db, "ORD-1")
        publisher = FakePublisher(fail_flush=True)

        try:
            relay_once(db, publisher, "orders.events")
        except RuntimeError:
            pass
        db.rollback()  # what the worker loop does on failure

        assert published_ids(db) == []  # nothing marked → the batch will replay
        assert db.get(RelayState, 1) is None

    def test_produce_failure_mid_batch_marks_nothing(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        publisher = FakePublisher(fail_produce_after=1)

        try:
            relay_once(db, publisher, "orders.events")
        except RuntimeError:
            pass
        db.rollback()

        assert published_ids(db) == []
        assert len(publisher.sent) == 1  # first produce succeeded, batch still aborts

    def test_failed_batch_replays_fully_on_retry(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        failing = FakePublisher(fail_flush=True)
        try:
            relay_once(db, failing, "orders.events")
        except RuntimeError:
            pass
        db.rollback()

        healthy = FakePublisher()
        relayed = relay_once(db, healthy, "orders.events")

        assert relayed == 2  # both events replayed — duplicates are the contract
        assert [key for _, key, _, _ in healthy.sent] == ["ORD-1", "ORD-2"]


class TestWireFormat:
    def test_schema_version_and_event_type_header(self, db: Session):
        add_event(db, "ORD-1", "assigned")
        publisher = FakePublisher()

        relay_once(db, publisher, "orders.events")

        record = json.loads(publisher.sent[0][2])
        assert record["schema_version"] == SCHEMA_VERSION
        assert publisher.sent[0][3] == [("event_type", b"assigned")]

    def test_state_row_tracks_last_published_for_observability(self, db: Session):
        add_event(db, "ORD-1")
        add_event(db, "ORD-2")
        relay_once(db, FakePublisher(), "orders.events")

        state = db.get(RelayState, 1)
        assert state is not None
        assert state.last_relayed_event_id == 2
